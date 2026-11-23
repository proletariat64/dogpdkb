mod input;
mod text;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use input::{Document, Identity, Stamp};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const ACTIONS: [&str; 6] = [
    "SKIP_VERIFIED",
    "UPDATE_METADATA_ONLY",
    "UPDATE_TEXT_REUSE_OCR",
    "PROCESS_CHANGED_RESOURCES",
    "PROCESS_REQUIRED",
    "UNKNOWN",
];

#[derive(Parser)]
#[command(version, about = "Experimental offline Markdown comparison")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Compare raw Markdown snapshots; defer unresolved resource or OCR evidence.
    Compare {
        #[arg(long)]
        baseline: PathBuf,
        #[arg(long)]
        incoming: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        include_evidence: bool,
        #[arg(long)]
        normalize_line_endings: bool,
        #[arg(long, default_value_t=16*1024*1024, value_parser=clap::value_parser!(u64).range(1..))]
        max_file_bytes: u64,
        #[arg(long, default_value_t = 2_000_000)]
        max_diff_work: usize,
        #[arg(long, default_value_t = 8000)]
        max_evidence_chars: usize,
        #[arg(long, default_value_t = 4096)]
        max_char_region: usize,
    },
}

fn metadata(old: Option<&Document>, new: &Document) -> Value {
    let fields: Vec<_> = input::METADATA_KEYS
        .into_iter()
        .filter(|k| old.is_some_and(|old| old.metadata[*k] != new.metadata[*k]))
        .collect();
    let status = if old.is_none() {
        "NEW"
    } else if fields.is_empty() {
        "UNCHANGED"
    } else {
        "CHANGED"
    };
    json!({"status":status,"changed_fields":fields,"sync_timestamp_changed":
        old.is_some_and(|old| old.metadata.get("sync_timestamp") != new.metadata.get("sync_timestamp"))})
}

fn can_exclude_resources(data: &[u8]) -> bool {
    let Ok(body) = std::str::from_utf8(data) else {
        return false;
    };
    if body.contains(['[', '<']) {
        return false;
    }
    !body
        .to_ascii_lowercase()
        .match_indices("url")
        .any(|(i, _)| body[i + 3..].trim_start().starts_with('('))
}

fn identity(id: &Identity) -> Value {
    json!({"knowledge_base":id.0,"document_id":id.1})
}

fn compare(
    baseline: &Path,
    incoming: &Path,
    output: &Path,
    limit: u64,
    opts: &text::Options,
) -> Result<bool> {
    let (old, old_stamp) = input::manifest(baseline)?;
    let (new, new_stamp) = input::manifest(incoming)?;
    let protected = || {
        [baseline, incoming]
            .into_iter()
            .chain(old.values().chain(new.values()).flat_map(|doc| {
                std::iter::once(doc.path.as_path())
                    .chain(doc.evidence_paths.iter().map(PathBuf::as_path))
            }))
    };
    input::check_output(output, protected())?;

    let mut snapshots: BTreeMap<&Path, Stamp> = BTreeMap::new();
    let mut dependencies = BTreeMap::<&Identity, Vec<&Path>>::new();
    let mut errors = BTreeSet::new();
    let mut results = Vec::new();
    for (id, right) in &new {
        let left = old.get(id);
        let metadata = metadata(left, right);
        let mut text = text::result("UNKNOWN", &[], None);
        let mut resources = json!({"status":"UNKNOWN","reason_codes":[],"items":[],
            "processing_required":[],"references_changed":null});
        let mut ocr = json!({"status":"UNKNOWN","reason_codes":[],"items":[],
            "reusable":[],"processing_required":[]});
        let read = |doc: &'_ Document| input::read(&doc.path, limit);
        let documents = read(right).and_then(|(after, stamp)| {
            let prior = snapshots.get(right.path.as_path());
            if prior.is_some_and(|s| s != &stamp) {
                return Err("INPUT_CHANGED");
            }
            snapshots.insert(&right.path, stamp);
            let deps = dependencies.entry(id).or_default();
            deps.push(&right.path);
            let Some(left) = left else {
                return Ok((None, after));
            };
            let before = if left.path == right.path {
                after.clone()
            } else {
                let (bytes, stamp) = read(left)?;
                if snapshots
                    .get(left.path.as_path())
                    .is_some_and(|s| s != &stamp)
                {
                    return Err("INPUT_CHANGED");
                }
                snapshots.insert(&left.path, stamp);
                deps.push(&left.path);
                bytes
            };
            Ok((Some(before), after))
        });
        match documents {
            Err(reason) => {
                text["reason_codes"] = json!([reason]);
                errors.insert((id, reason));
            }
            Ok((before, after)) => {
                let historical = !right.raw || left.is_some_and(|d| !d.raw);
                text = if historical {
                    text::result("UNKNOWN", &["RUST_EXPERIMENT_HISTORY_UNSUPPORTED"], None)
                } else {
                    text::compare(before.as_deref(), &after, opts)
                };
                let verified = !historical
                    && !right.has_evidence
                    && !left.is_some_and(|d| d.has_evidence)
                    && can_exclude_resources(&after)
                    && before.as_deref().is_none_or(can_exclude_resources);
                if verified {
                    resources["status"] = json!("NOT_APPLICABLE");
                    resources["references_changed"] = json!(false);
                    ocr["status"] = json!("NOT_APPLICABLE");
                } else {
                    resources["reason_codes"] = json!(["RUST_EXPERIMENT_RESOURCE_UNVERIFIED"]);
                    ocr["reason_codes"] = json!(["OCR_RESOURCE_UNVERIFIED"]);
                }
                ocr["removed_contributions"] = json!([]);
            }
        }
        let action = if [&text, &resources, &ocr]
            .iter()
            .any(|d| d["status"] == "UNKNOWN")
        {
            "UNKNOWN"
        } else if left.is_none() {
            "PROCESS_REQUIRED"
        } else if text["status"] == "CHANGED" {
            "UPDATE_TEXT_REUSE_OCR"
        } else if metadata["status"] == "CHANGED" {
            "UPDATE_METADATA_ONLY"
        } else {
            "SKIP_VERIFIED"
        };
        let reasons: BTreeSet<_> = [&text, &resources, &ocr]
            .iter()
            .flat_map(|d| {
                d["reason_codes"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
            })
            .collect();
        let reasons = json!(reasons);
        results.push((
            id,
            json!({"knowledge_base":id.0,"document_id":id.1,"metadata":metadata,
            "text":text,"resources":resources,"ocr":ocr,"action":action,"reason_codes":reasons}),
        ));
    }

    anyhow::ensure!(
        old_stamp.current(baseline) && new_stamp.current(incoming),
        "MANIFEST_CHANGED"
    );
    let changed: BTreeSet<_> = snapshots
        .iter()
        .filter_map(|(&path, stamp)| (!stamp.current(path)).then_some(path))
        .collect();
    let mut actions = BTreeMap::from(ACTIONS.map(|a| (a, 0usize)));
    let mut rows = Vec::with_capacity(results.len());
    for (id, mut row) in results {
        if dependencies
            .get(id)
            .is_some_and(|paths| paths.iter().any(|p| changed.contains(p)))
        {
            row["action"] = json!("UNKNOWN");
            let r: BTreeSet<_> = row["reason_codes"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .chain(["INPUT_CHANGED"])
                .collect();
            row["reason_codes"] = json!(r);
            errors.insert((id, "INPUT_CHANGED"));
        }
        *actions
            .get_mut(row["action"].as_str().context("INVALID_ACTION")?)
            .context("INVALID_ACTION")? += 1;
        rows.push(row);
    }
    let complete = actions["UNKNOWN"] == 0;
    let rules = if opts.normalize {
        json!([{"name":"line-endings-lf","version":"1","contract":"CRLF and lone CR become LF; all other bytes are preserved"}])
    } else {
        json!([{"name":"identity","version":"1"}])
    };
    let absent: Vec<_> = old
        .keys()
        .filter(|id| !new.contains_key(*id))
        .map(identity)
        .collect();
    let errors: Vec<_> = errors
        .into_iter()
        .map(|(id, reason)| {
            json!({
                "knowledge_base":id.0,"document_id":id.1,"reason_code":reason
            })
        })
        .collect();
    let report = json!({"schema_version":"1.0","rules":rules,"results":rows,"baseline_only":absent,
        "summary":{"total":rows.len(),"actions":actions},"errors":errors});
    let parent = output
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut file = tempfile::NamedTempFile::new_in(parent).context("REPORT_WRITE_FAILED")?;
    serde_json::to_writer_pretty(&mut file, &report).context("REPORT_WRITE_FAILED")?;
    writeln!(file).context("REPORT_WRITE_FAILED")?;
    file.as_file().sync_all().context("REPORT_WRITE_FAILED")?;
    input::check_output(output, protected())?;
    file.persist(output).context("REPORT_WRITE_FAILED")?;
    Ok(complete)
}

fn main() -> ExitCode {
    let cli = match Cli::try_parse() {
        Ok(cli) => cli,
        Err(error) => {
            let code = u8::from(error.use_stderr());
            let _ = error.print();
            return ExitCode::from(code);
        }
    };
    let Command::Compare {
        baseline,
        incoming,
        output,
        include_evidence,
        normalize_line_endings,
        max_file_bytes,
        max_diff_work,
        max_evidence_chars,
        max_char_region,
    } = cli.command;
    match compare(
        &baseline,
        &incoming,
        &output,
        max_file_bytes,
        &text::Options {
            normalize: normalize_line_endings,
            evidence: include_evidence,
            work: max_diff_work,
            chars: max_evidence_chars,
            char_region: max_char_region,
        },
    ) {
        Ok(true) => ExitCode::SUCCESS,
        Ok(false) => ExitCode::from(2),
        Err(err) => {
            eprintln!("mdcompare: {err}");
            ExitCode::FAILURE
        }
    }
}
