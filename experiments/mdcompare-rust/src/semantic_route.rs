use crate::input::{Reader, check_output, publish};
use anyhow::{Context, Result, anyhow, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, HashSet},
    path::{Component, Path, PathBuf},
};

pub type Identity = (String, String);

#[derive(Clone, PartialEq, Eq)]
pub struct Binding {
    pub evidence: String,
    pub digest: String,
}

pub struct Generation {
    pub directory: PathBuf,
    pub bindings: BTreeMap<Identity, Binding>,
    pub inputs: HashSet<PathBuf>,
    pub results: Vec<Value>,
    pub report_sha256: String,
    pub index_sha256: String,
}

pub fn identity(row: &Value) -> Result<Identity> {
    let field = |name| -> Result<String> {
        Ok(row[name]
            .as_str()
            .filter(|id| !id.is_empty())
            .ok_or_else(|| anyhow!("Invalid document identity"))?
            .to_owned())
    };
    Ok((field("knowledge_base")?, field("document_id")?))
}

pub fn digest(data: &[u8]) -> String {
    format!("{:x}", Sha256::digest(data))
}

pub fn eligible(row: &Value) -> bool {
    row["semantic_status"] == "CHANGED"
        && row["action"] == "COMPILE"
        && row["comparison_complete"] == true
        && row["extraction"]["available"] == true
}

pub fn read(reader: &mut Reader, path: &Path) -> Result<Vec<u8>> {
    reader.read(path).map_err(|code| anyhow!("{code}"))
}

pub fn load(report_path: &Path, index_path: &Path, reader: &mut Reader) -> Result<Generation> {
    let report_bytes = read(reader, report_path)?;
    let index_bytes = read(reader, index_path)?;
    let report_sha256 = digest(&report_bytes);
    let index_sha256 = digest(&index_bytes);
    let report: Value = serde_json::from_slice(&report_bytes)?;
    let index: Value = serde_json::from_slice(&index_bytes)?;
    ensure!(
        report["schema_version"] == "semantic-1.0"
            && report["scope"] == "authored_body"
            && index["schema_version"] == "mdcompare-semantic-diff-1"
            && index["report_sha256"] == report_sha256,
        "Invalid semantic report generation"
    );
    let directory = index_path
        .parent()
        .filter(|path| !path.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."))
        .canonicalize()?;
    let evidence_name = index["evidence_directory"]
        .as_str()
        .context("Evidence directory missing")?;
    let mut components = Path::new(evidence_name).components();
    ensure!(
        matches!(components.next(), Some(Component::Normal(name)) if name == evidence_name)
            && components.next().is_none(),
        "Invalid evidence directory"
    );
    let evidence_root = directory.join(evidence_name).canonicalize()?;
    ensure!(
        evidence_root.parent() == Some(directory.as_path()) && evidence_root.is_dir(),
        "Evidence escapes its generation"
    );
    let mut inputs = HashSet::from([
        report_path.to_owned(),
        index_path.to_owned(),
        evidence_root.clone(),
    ]);
    let protected = index["protected_inputs"]
        .as_array()
        .filter(|items| !items.is_empty())
        .context("Protected input paths are missing")?;
    for item in protected {
        let path = PathBuf::from(item.as_str().context("Invalid protected input path")?);
        ensure!(path.is_absolute(), "Invalid protected input path");
        inputs.insert(path);
    }
    let mut bindings = BTreeMap::new();
    for row in index["documents"]
        .as_array()
        .context("Missing evidence bindings")?
    {
        let key = identity(row)?;
        let evidence = row["evidence"].as_str().context("Missing evidence path")?;
        ensure!(!Path::new(evidence).is_absolute(), "Invalid evidence path");
        let path = directory.join(evidence).canonicalize()?;
        ensure!(
            path.parent() == Some(evidence_root.as_path()),
            "Invalid evidence path"
        );
        reader.begin_document(&crate::input::Identity {
            knowledge_base: key.0.clone(),
            document_id: key.1.clone(),
        });
        let data = read(reader, &path)?;
        let sha = digest(&data);
        let detail: Value = serde_json::from_slice(&data)?;
        ensure!(
            identity(&detail)? == key && row["sha256"] == sha,
            "Invalid evidence binding"
        );
        inputs.insert(path);
        ensure!(
            bindings
                .insert(
                    key,
                    Binding {
                        evidence: evidence.to_owned(),
                        digest: sha
                    }
                )
                .is_none(),
            "Duplicate evidence"
        );
    }
    let results = report["results"]
        .as_array()
        .context("Missing semantic results")?;
    let mut keys = HashSet::new();
    for row in results {
        let key = identity(row)?;
        ensure!(
            bindings.contains_key(&key)
                && keys.insert(key)
                && matches!(row["action"].as_str(), Some("COMPILE" | "SKIP" | "DEFER"))
                && row["semantic_status"].is_string()
                && row["comparison_complete"].is_boolean()
                && row["extraction"]["available"].is_boolean(),
            "Invalid semantic result"
        );
    }
    ensure!(keys.len() == bindings.len(), "Unmatched evidence binding");
    Ok(Generation {
        directory,
        bindings,
        inputs,
        results: results.clone(),
        report_sha256,
        index_sha256,
    })
}

fn load_scores(
    generation: &Generation,
    index_path: &Path,
    scores: &Path,
    reader: &mut Reader,
) -> Result<BTreeMap<Identity, (u8, bool)>> {
    let manifest: Value = serde_json::from_slice(&read(reader, scores)?)?;
    let linked = manifest["diff_index"]
        .as_str()
        .context("Missing diff index")?;
    ensure!(
        manifest["schema_version"] == "mdcompare-semantic-scores-1"
            && scores
                .parent()
                .context("Missing scores directory")?
                .join(linked)
                .canonicalize()?
                == index_path.canonicalize()?,
        "Invalid score manifest"
    );
    let mut results = BTreeMap::new();
    for row in manifest["scores"].as_array().context("Missing scores")? {
        let key = identity(row)?;
        let binding = generation.bindings.get(&key).context("Unbound score")?;
        let score = row["score"]
            .as_u64()
            .filter(|score| (1..=5).contains(score))
            .context("Invalid score")?;
        let ambiguous = row["ambiguous"]
            .as_bool()
            .context("Invalid ambiguity flag")?;
        ensure!(
            row["evidence"] == binding.evidence && row["evidence_sha256"] == binding.digest,
            "Invalid score evidence binding"
        );
        ensure!(
            results.insert(key, (score as u8, ambiguous)).is_none(),
            "Duplicate score"
        );
    }
    Ok(results)
}

pub fn run(
    report: &Path,
    diff_index: &Path,
    scores: &Path,
    output: &Path,
    compile_threshold: u8,
    skip_ambiguity_intercept: bool,
) -> Result<bool> {
    let mut reader = Reader::new(u64::MAX);
    let generation =
        load(report, diff_index, &mut reader).map_err(|_| anyhow!("SCORE_BINDING_INVALID"))?;
    let decisions = load_scores(&generation, diff_index, scores, &mut reader)
        .map_err(|_| anyhow!("SCORE_BINDING_INVALID"))?;
    check_output(
        output,
        generation
            .inputs
            .iter()
            .map(PathBuf::as_path)
            .chain([scores]),
    )?;
    let mut results = generation.results;
    let mut actions = BTreeMap::new();
    for row in &mut results {
        let decision = decisions.get(&identity(row)?).copied();
        let reason = match (eligible(row), decision) {
            (true, Some((score, _))) if score > compile_threshold => "SCORE_ABOVE_THRESHOLD",
            (true, Some((_, true))) if skip_ambiguity_intercept => {
                "SCORE_SKIP_AMBIGUITY_INTERCEPTED"
            }
            (true, Some(_)) => {
                row["action"] = json!("SKIP");
                "SCORE_AT_OR_BELOW_THRESHOLD"
            }
            _ => "DETERMINISTIC_ROUTE",
        };
        row["routing_reason"] = json!(reason);
        row["score"] = json!(decision.map(|(score, _)| score));
        row["ambiguous"] = json!(decision.map(|(_, ambiguous)| ambiguous));
        let action = row["action"].as_str().context("Invalid result action")?;
        *actions.entry(action.to_owned()).or_insert(0usize) += 1;
    }
    ensure!(reader.changed_paths().is_empty(), "SCORE_BINDING_INVALID");
    let value = json!({
        "schema_version": "semantic-route-1.0",
        "compile_threshold": compile_threshold,
        "skip_ambiguity_intercept": if skip_ambiguity_intercept { "yes" } else { "no" },
        "results": results,
        "summary": { "actions": actions },
    });
    let mut bytes = serde_json::to_vec_pretty(&value)?;
    bytes.push(b'\n');
    publish(&[(output.to_owned(), bytes)])?;
    Ok(true)
}
