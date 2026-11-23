use crate::config::Config;
use crate::input::{check_output, json_bytes, parse_utf8_json};
use crate::summary::{Outcome, Summary};
use crate::workspace::{self, Workspace, checksum};
use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::os::unix::fs::{DirBuilderExt, MetadataExt};
use std::path::{Path, PathBuf};

pub type Identity = (String, String);

pub struct Binding {
    pub evidence: String,
    pub digest: String,
}

pub struct Generation {
    pub directory: PathBuf,
    pub bindings: BTreeMap<Identity, Binding>,
    pub results: Vec<Value>,
    pub comparison_sha256: String,
}

pub(crate) fn identity(row: &Value) -> Result<Identity> {
    let field = |name| {
        row[name]
            .as_str()
            .filter(|value| !value.is_empty())
            .context("binding_invalid")
    };
    Ok((
        field("knowledge_base")?.to_owned(),
        field("document_id")?.to_owned(),
    ))
}

pub fn eligible(row: &Value) -> bool {
    row["semantic_status"] == "CHANGED"
        && row["action"] == "COMPILE"
        && row["comparison_complete"] == true
        && row["extraction"]["available"] == true
}

pub fn load(workspace: &Workspace) -> Result<Generation> {
    let commit = workspace::verify_commit(workspace)?.context("binding_invalid")?;
    let artifact = |name: &str| -> Result<Value> {
        let bytes = fs::read(workspace.root.join(name)).context("binding_invalid")?;
        ensure!(
            commit
                .artifacts
                .get(name)
                .is_some_and(|sha| *sha == checksum(&bytes)),
            "binding_invalid"
        );
        parse_utf8_json(&bytes).context("binding_invalid")
    };
    let report = artifact("result.json")?;
    let index = artifact("diff-index.json")?;
    ensure!(
        report["schema_version"] == "mdcompare-semantic-result-1"
            && report["scope"] == "authored_body"
            && index["schema_version"] == "mdcompare-diff-index-1",
        "binding_invalid"
    );
    let mut bindings = BTreeMap::new();
    for row in index["documents"].as_array().context("binding_invalid")? {
        let evidence = row["evidence"].as_str().context("binding_invalid")?;
        let digest = row["sha256"].as_str().context("binding_invalid")?;
        ensure!(
            bindings
                .insert(
                    identity(row)?,
                    Binding {
                        evidence: evidence.to_owned(),
                        digest: digest.to_owned()
                    }
                )
                .is_none(),
            "binding_invalid"
        );
    }
    let results = report["results"].as_array().context("binding_invalid")?;
    let mut seen = BTreeSet::new();
    for row in results {
        let key = identity(row)?;
        ensure!(
            bindings.contains_key(&key) && seen.insert(key),
            "binding_invalid"
        );
        let expected = match row["semantic_status"].as_str() {
            Some("NEW" | "CHANGED" | "UNCERTAIN") => ("COMPILE", true),
            Some("EQUIVALENT") => ("SKIP", true),
            Some("UNAVAILABLE") => ("DEFER", false),
            _ => anyhow::bail!("binding_invalid"),
        };
        ensure!(
            row["action"] == expected.0
                && row["extraction"]["available"] == expected.1
                && row["comparison_complete"].is_boolean()
                && (expected.0 != "SKIP" || row["comparison_complete"] == true),
            "binding_invalid"
        );
    }
    ensure!(seen.len() == bindings.len(), "binding_invalid");
    Ok(Generation {
        directory: workspace.root.clone(),
        bindings,
        results: results.clone(),
        comparison_sha256: commit.comparison_sha256,
    })
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ExternalScores {
    schema_version: String,
    comparison_sha256: String,
    scores: Vec<ExternalScore>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ExternalScore {
    knowledge_base: String,
    document_id: String,
    evidence_sha256: String,
    score: u8,
    ambiguous: bool,
}

fn scores(
    generation: &Generation,
    path: &Path,
    bytes: &[u8],
) -> Result<BTreeMap<Identity, (u8, bool)>> {
    let input = parse_utf8_json(bytes).context("binding_invalid")?;
    let mut decisions = BTreeMap::new();
    let eligible: BTreeSet<_> = generation
        .results
        .iter()
        .filter(|row| self::eligible(row))
        .map(identity)
        .collect::<Result<_>>()?;
    let mut insert = |key, witness: &str, path: Option<&str>, score, ambiguous| -> Result<()> {
        let binding = generation.bindings.get(&key).context("binding_invalid")?;
        ensure!(
            binding.digest == witness
                && path.is_none_or(|path| path == binding.evidence)
                && eligible.contains(&key)
                && (1..=5).contains(&score),
            "binding_invalid"
        );
        ensure!(
            decisions.insert(key, (score, ambiguous)).is_none(),
            "binding_invalid"
        );
        Ok(())
    };
    match input["schema_version"].as_str() {
        Some("mdcompare-external-scores-1") => {
            let external: ExternalScores =
                serde_json::from_value(input).context("binding_invalid")?;
            ensure!(
                external.schema_version == "mdcompare-external-scores-1"
                    && external.comparison_sha256 == generation.comparison_sha256,
                "binding_invalid"
            );
            for row in external.scores {
                insert(
                    (row.knowledge_base, row.document_id),
                    &row.evidence_sha256,
                    None,
                    row.score,
                    row.ambiguous,
                )?;
            }
        }
        Some("mdcompare-score-result-1") => {
            let (local, digest) =
                crate::semantic_score::verified_result(path).context("binding_invalid")?;
            ensure!(
                local.comparison_sha256 == generation.comparison_sha256
                    && digest == checksum(bytes)
                    && matches!(local.state.as_str(), "complete" | "partial"),
                "binding_invalid"
            );
            for row in local.rows {
                if row.state == "scored" {
                    insert(
                        (row.knowledge_base, row.document_id),
                        row.evidence_sha256.as_deref().context("binding_invalid")?,
                        Some(row.evidence.as_deref().context("binding_invalid")?),
                        row.score.context("binding_invalid")?,
                        row.ambiguous.context("binding_invalid")?,
                    )?;
                }
            }
        }
        _ => anyhow::bail!("binding_invalid"),
    }
    Ok(decisions)
}

fn owned_dir(path: &Path) -> Result<()> {
    match fs::DirBuilder::new().mode(0o700).create(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            ensure!(fs::symlink_metadata(path)?.is_dir(), "binding_invalid");
            Ok(())
        }
        Err(error) => Err(error).context("publish_failed"),
    }
}

pub fn run(plan: &Path, score_path: &Path, config: Option<&Path>) -> Result<Outcome> {
    let workspace = Workspace::locate(plan)?;
    let frozen = workspace::load_frozen(&workspace)?;
    let config = Config::load(Some(&workspace.source), config)?;
    let generation = load(&workspace)?;
    let (bytes, stamp) = crate::input::read(score_path, 16 * 1024 * 1024)
        .map_err(anyhow::Error::msg)
        .context("binding_invalid")?;
    let decisions = scores(&generation, score_path, &bytes)?;
    let scores_sha256 = checksum(&bytes);
    let policy = config.route;
    let key = checksum(&serde_json::to_vec(&(
        &generation.comparison_sha256,
        &scores_sha256,
        policy.compile_above,
        policy.compile_ambiguous,
    ))?);
    let mut rows = generation.results;
    let mut actions = BTreeMap::<String, usize>::new();
    for row in &mut rows {
        let decision = decisions.get(&identity(row)?).copied();
        let original = row["action"].clone();
        let reason = match decision {
            Some((score, _)) if score > policy.compile_above => "SCORE_ABOVE_THRESHOLD",
            Some((_, true)) if policy.compile_ambiguous => "SCORE_SKIP_AMBIGUITY_INTERCEPTED",
            Some(_) => {
                row["action"] = json!("SKIP");
                "SCORE_AT_OR_BELOW_THRESHOLD"
            }
            None => "DETERMINISTIC_ROUTE",
        };
        row["original_action"] = original;
        row["routing_reason"] = json!(reason);
        row["score"] = json!(decision.map(|(score, _)| score));
        row["ambiguous"] = json!(decision.map(|(_, ambiguous)| ambiguous));
        *actions
            .entry(
                row["action"]
                    .as_str()
                    .context("binding_invalid")?
                    .to_owned(),
            )
            .or_default() += 1;
    }
    let result = json!({
        "schema_version":"mdcompare-route-result-1","state":"complete",
        "comparison_sha256":generation.comparison_sha256,"scores_sha256":scores_sha256,
        "compile_above":policy.compile_above,"compile_ambiguous":policy.compile_ambiguous,
        "results":rows,"counts":{"total":rows.len(),"actions":actions},
    });
    let result_bytes = json_bytes(&result)?;
    let _guard = workspace.lock("route.lock", true)?;
    ensure!(stamp.current(score_path), "binding_invalid");
    let routes = workspace.root.join("routes");
    let route = routes.join(key);
    owned_dir(&routes)?;
    owned_dir(&route)?;
    let output = route.join("result.json");
    let mut protected: Vec<&Path> = frozen.inputs.keys().map(PathBuf::as_path).collect();
    protected.extend([workspace.source.as_path(), score_path]);
    protected.extend(config.source.as_deref());
    check_output(&output, protected).context("publish_failed")?;
    match fs::symlink_metadata(&output) {
        Ok(meta) => {
            ensure!(
                meta.is_file() && meta.nlink() == 1 && fs::read(&output)? == result_bytes,
                "binding_invalid"
            );
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            workspace::atomic_write(&output, &result_bytes)?;
            for directory in [&routes, &workspace.root] {
                fs::File::open(directory)?
                    .sync_all()
                    .context("publish_failed")?;
            }
        }
        Err(error) => return Err(error).context("publish_failed"),
    }
    Ok(Summary::new("route", "complete")
        .comparison(&generation.comparison_sha256)
        .artifact("result", output)
        .count("total", rows.len())
        .count("scored", decisions.len())
        .exit(0))
}
