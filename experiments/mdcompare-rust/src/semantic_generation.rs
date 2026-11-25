//! Verified semantic comparison generation shared by scoring and routing.
use crate::input::parse_utf8_json;
use crate::workspace::{self, Workspace, checksum};
use anyhow::{Context, Result, ensure};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::PathBuf;

pub type Identity = (String, String);

pub struct Binding {
    pub evidence: String,
    pub digest: String,
}

pub struct Generation {
    pub directory: PathBuf,
    bindings: BTreeMap<Identity, Binding>,
    pub results: Vec<Value>,
    pub comparison_sha256: String,
}

impl Generation {
    /// The authoritative scoreable document set and its committed evidence.
    pub fn score_candidates(&self) -> Result<BTreeMap<Identity, &Binding>> {
        self.results
            .iter()
            .filter(|row| eligible(row))
            .map(|row| {
                let key = identity(row)?;
                let binding = self.bindings.get(&key).context("binding_invalid")?;
                Ok((key, binding))
            })
            .collect()
    }
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

fn eligible(row: &Value) -> bool {
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
