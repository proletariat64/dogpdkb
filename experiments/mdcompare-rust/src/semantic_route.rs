use crate::config::{Config, Scope};
use crate::input::{check_output, json_bytes, parse_utf8_json};
use crate::semantic_generation::{Generation, Identity, identity, load};
use crate::summary::{Outcome, Summary};
use crate::workspace::{self, Workspace, checksum};
use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::json;
use std::collections::BTreeMap;
use std::fs;
use std::os::unix::fs::{DirBuilderExt, MetadataExt};
use std::path::Path;

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

enum ScoreInput {
    External(ExternalScores),
    Native(crate::semantic_score::ScoreResult),
}

fn score_input(path: &Path) -> Result<(ScoreInput, String, crate::input::Stamp)> {
    let read = crate::input::read(path, crate::input::MANIFEST_LIMIT);
    let (stamp, digest) = match read {
        Ok((bytes, stamp)) => {
            let input = parse_utf8_json(&bytes).context("binding_invalid")?;
            let digest = checksum(&bytes);
            if input["schema_version"] == "mdcompare-external-scores-1" {
                let external = serde_json::from_value(input).context("binding_invalid")?;
                return Ok((ScoreInput::External(external), digest, stamp));
            }
            ensure!(
                input["schema_version"] == "mdcompare-score-result-1",
                "binding_invalid"
            );
            (stamp, Some(digest))
        }
        // Native scores can exceed the external manifest limit. Require the
        // native journal and validate the exact score result against it.
        Err("FILE_BUDGET_EXCEEDED") => {
            (fs::metadata(path).context("binding_invalid")?.into(), None)
        }
        Err(reason) => return Err(anyhow::Error::msg(reason)).context("binding_invalid"),
    };
    let (local, verified) =
        crate::semantic_score::verified_result(path).context("binding_invalid")?;
    ensure!(
        digest.is_none_or(|digest| digest == verified),
        "binding_invalid"
    );
    Ok((ScoreInput::Native(local), verified, stamp))
}

fn scores(generation: &Generation, input: ScoreInput) -> Result<BTreeMap<Identity, (u8, bool)>> {
    let mut decisions = BTreeMap::new();
    let eligible = generation.score_candidates()?;
    let mut insert = |key, witness: &str, path: Option<&str>, score, ambiguous| -> Result<()> {
        let binding = eligible.get(&key).context("binding_invalid")?;
        ensure!(
            binding.digest == witness
                && path.is_none_or(|path| path == binding.evidence)
                && (1..=5).contains(&score),
            "binding_invalid"
        );
        ensure!(
            decisions.insert(key, (score, ambiguous)).is_none(),
            "binding_invalid"
        );
        Ok(())
    };
    match input {
        ScoreInput::External(external) => {
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
        ScoreInput::Native(local) => {
            ensure!(
                local.comparison_sha256 == generation.comparison_sha256
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
    let config = Config::load(Some(&workspace.source), config, Scope::Route)?;
    let generation = load(&workspace)?;
    let (input, scores_sha256, stamp) = score_input(score_path)?;
    let decisions = scores(&generation, input)?;
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
    let mut protected: Vec<&Path> = frozen.input_paths().collect();
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
