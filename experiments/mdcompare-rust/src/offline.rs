use crate::input::{Document, Identity, Reader, json_bytes, parse_utf8_json};
use crate::summary::{Outcome, Progress, Summary};
use crate::text::Options;
use crate::workspace::{self, Commit, FrozenPlan, Operation, Workspace};
use anyhow::{Context, Result, ensure};
use std::collections::BTreeMap;
use std::fs;
use std::os::unix::fs::MetadataExt;
use std::path::Path;

pub(crate) struct Snapshots {
    pub baseline: BTreeMap<Identity, Document>,
    pub incoming: BTreeMap<Identity, Document>,
    pub order: Vec<Identity>,
}

pub(crate) fn run(plan: &Path, config: Option<&Path>, progress: &Progress) -> Result<Outcome> {
    let (workspace, frozen) = workspace::accept(plan, config)?;
    if let Some(commit) = workspace::verify_commit(&workspace)? {
        return published("run", &workspace, commit);
    }

    let _lock = workspace.lock("execution.lock", false)?;
    if let Some(commit) = workspace::verify_commit(&workspace)? {
        return published("run", &workspace, commit);
    }

    frozen.verify_inputs()?;
    let stage = tempfile::tempdir_in(&workspace.root).context("publish_failed")?;
    let (mut old, mut new) = (BTreeMap::<Identity, Document>::new(), BTreeMap::new());
    let mut order = Vec::new();
    for (baseline, incoming) in frozen.documents() {
        let identity = incoming
            .as_ref()
            .or(baseline.as_ref())
            .context("binding_invalid")?
            .identity
            .clone();
        order.push(identity.clone());
        if let Some(doc) = baseline {
            old.insert(identity.clone(), doc);
        }
        if let Some(doc) = incoming {
            new.insert(identity, doc);
        }
    }

    let snapshots = Snapshots {
        baseline: old,
        incoming: new,
        order,
    };
    let total = snapshots.incoming.len();
    let compare = &frozen.settings.compare;
    let report = &frozen.settings.report;
    let options = Options {
        file_bytes: compare.max_file_bytes,
        normalize: compare.normalize_line_endings,
        work: compare.max_diff_work,
        evidence: report.include_body_excerpts,
        chars: report.max_excerpt_chars.unwrap_or(8000),
        char_region: 4096,
    };
    let mut reader = Reader::new_frozen(options.file_bytes, &frozen.inputs, &frozen.presence);
    progress.update("compare", 0, total, 0, 0);
    let mut artifacts = vec!["result.json"];
    let operation = frozen.work.operation;
    let result = match operation {
        Operation::Semantic => {
            crate::semantic::compare_documents(
                &snapshots,
                stage.path(),
                frozen.work.body_recovery.as_deref(),
                &options,
                &mut reader,
                progress,
            )?;
            artifacts.push("diff-index.json");
            None
        }
        Operation::Knowledge => Some(crate::knowledge::compare_documents(
            &snapshots,
            &options,
            &mut reader,
            progress,
        )),
        Operation::Strict => Some(crate::compare::compare_documents(
            &snapshots,
            &options,
            &mut reader,
            progress,
        )?),
        Operation::Catalog => Some(crate::catalog::catalog_documents(
            &snapshots,
            &mut reader,
            progress,
        )?),
    };

    ensure!(
        !reader.drifted() && reader.changed_paths().is_empty(),
        "input_drift"
    );
    frozen.verify_inputs()?;
    if let Some(mut result) = result {
        if report.markdown {
            let markdown = match operation {
                Operation::Strict => Some(crate::compare::render(&result)?),
                Operation::Knowledge => Some(crate::knowledge::render(&result)?),
                _ => None,
            };
            if let Some(markdown) = markdown {
                workspace::atomic_write(&stage.path().join("review.md"), &markdown)?;
                artifacts.push("review.md");
            }
        }
        // The optional report is the only output whose excerpts depend on [report].
        for row in result["results"].as_array_mut().context("publish_failed")? {
            match operation {
                Operation::Strict => {
                    row["text"]
                        .as_object_mut()
                        .context("publish_failed")?
                        .remove("differences");
                    row["text"]
                        .as_object_mut()
                        .context("publish_failed")?
                        .remove("evidence_truncated");
                }
                Operation::Knowledge => {
                    row.as_object_mut()
                        .context("publish_failed")?
                        .remove("evidence_truncated");
                    for unit in row["units"].as_array_mut().context("publish_failed")? {
                        unit.as_object_mut()
                            .context("publish_failed")?
                            .remove("evidence");
                    }
                }
                _ => {}
            }
        }
        workspace::atomic_write(&stage.path().join("result.json"), &json_bytes(&result)?)?;
    }
    progress.update("publish", total, total, 0, 0);
    for name in artifacts
        .iter()
        .copied()
        .chain(matches!(operation, Operation::Semantic).then_some("evidence"))
    {
        let destination = workspace.root.join(name);
        if let Ok(prior) = fs::symlink_metadata(&destination) {
            // A previous interrupted run may have placed files before it wrote its commit.
            if name == "evidence" {
                ensure!(prior.is_dir(), "publish_failed");
                fs::rename(&destination, stage.path().join("abandoned-evidence"))
                    .context("publish_failed")?;
            } else {
                ensure!(prior.is_file() && prior.nlink() == 1, "publish_failed");
            }
        }
        fs::rename(stage.path().join(name), destination).context("publish_failed")?;
    }
    let commit = workspace::publish_commit(&workspace, &artifacts)?;
    published("run", &workspace, commit)
}

fn published(command: &'static str, workspace: &Workspace, commit: Commit) -> Result<Outcome> {
    let read = fs::read(workspace.root.join("result.json")).context("binding_invalid")?;
    ensure!(
        commit
            .artifacts
            .get("result.json")
            .is_some_and(|hash| *hash == workspace::checksum(&read)),
        "binding_invalid"
    );
    let report = parse_utf8_json(&read).context("binding_invalid")?;
    let state = report["state"]
        .as_str()
        .filter(|state| matches!(*state, "complete" | "partial"))
        .context("binding_invalid")?;
    let mut summary = Summary::new(command, state)
        .comparison(commit.comparison_sha256)
        .artifact("plan", workspace.root.join("plan.json"))
        .artifact("commit", workspace.root.join("commit.json"));
    for (artifact, key) in [
        ("result.json", "result"),
        ("diff-index.json", "diff_index"),
        ("review.md", "review"),
    ] {
        if commit.artifacts.contains_key(artifact) {
            summary = summary.artifact(key, workspace.root.join(artifact));
        }
    }
    if let Some(counts) = report["counts"].as_object() {
        for (name, count) in counts {
            if let Some(count) = count.as_u64() {
                summary = summary.count(name, usize::try_from(count)?);
            }
        }
    }
    Ok(summary.exit(if command == "run" && state == "partial" {
        2
    } else {
        0
    }))
}

pub(crate) fn status(workspace: &Workspace, frozen: &FrozenPlan) -> Result<Outcome> {
    if let Some(commit) = workspace::verify_commit(workspace)? {
        return published("status", workspace, commit);
    }
    let execution = workspace::execution_state(workspace)?;
    let running = execution == Some(true);
    let mut summary = Summary::new("status", if running { "running" } else { "accepted" })
        .artifact("plan", workspace.root.join("plan.json"))
        .count("total", frozen.work.documents.len());
    if execution == Some(false) {
        summary = summary.reason("interrupted");
    }
    Ok(summary.exit(0))
}
