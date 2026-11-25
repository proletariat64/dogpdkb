use crate::{summary::Progress, workspace::Snapshots};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::Path;

use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::authored_body::{
    BodyProjection, Recovery, RecoveryRecord, Side, load_recovery, source_evidence,
};
use crate::input::{Document, Identity, Reader, json_bytes};
use crate::knowledge_units::{Alignment, Unit, align_units};
use crate::text::Options;

fn sequence(units: &[Unit]) -> (Vec<&Unit>, Vec<usize>) {
    units
        .iter()
        .enumerate()
        .filter(|(_, unit)| !unit.exporter_title)
        .map(|(index, unit)| (unit, index))
        .unzip()
}

fn changes(
    before: &[&Unit],
    after: &[&Unit],
    aligned: &Alignment,
    old_positions: &[usize],
    new_positions: &[usize],
) -> Vec<Value> {
    let mut exact = HashMap::<_, VecDeque<usize>>::new();
    for (index, unit) in before.iter().enumerate() {
        exact.entry(unit.exact_key()).or_default().push_back(index);
    }
    // The aligner may reuse one old unit to describe multiple incoming
    // occurrences. Diff operations need one old reference per occurrence.
    let matches: Vec<_> = aligned
        .items
        .iter()
        .zip(after)
        .map(|(item, current)| {
            let previous = if item.kind == "reuse" {
                exact
                    .get_mut(&current.exact_key())
                    .and_then(VecDeque::pop_front)
            } else {
                item.old
            };
            (item, previous)
        })
        .collect();
    let reserved: HashSet<_> = matches
        .iter()
        .filter_map(|(_, previous)| *previous)
        .collect();
    let mut used = HashSet::new();
    let mut movable = HashMap::<_, BTreeSet<usize>>::new();
    for (index, unit) in before.iter().enumerate() {
        if !reserved.contains(&index) {
            movable
                .entry((unit.kind, unit.key.as_str()))
                .or_default()
                .insert(index);
        }
    }
    let mut operations = Vec::new();
    for (index, (item, mut previous)) in matches.into_iter().enumerate() {
        let current = after[index];
        let operation = match item.kind {
            "reuse" => match previous {
                None => "add",
                Some(previous) if previous != index => "move",
                Some(_) => "equal",
            },
            other => {
                if let Some(candidates) = movable
                    .get(&(current.kind, current.key.as_str()))
                    .filter(|items| previous.is_none() && items.len() == 1)
                {
                    previous = candidates.first().copied();
                    "move"
                } else {
                    match other {
                        "add" => "add",
                        "revise" => "modify",
                        "unresolved" => "uncertain",
                        _ => unreachable!("incoming alignment operation"),
                    }
                }
            }
        };
        if let Some(previous) = previous {
            used.insert(previous);
            let unit = before[previous];
            movable
                .entry((unit.kind, unit.key.as_str()))
                .or_default()
                .remove(&previous);
        }
        operations.push(json!({
            "operation":operation, "old_ref":previous.map(|i| old_positions[i]),
            "new_ref":new_positions[index], "basis":item.basis,
        }));
    }
    operations.extend(
        (0..before.len())
            .filter(|index| !used.contains(index))
            .map(|index| {
                json!({
                    "operation":"delete", "old_ref":old_positions[index], "new_ref":null,
                    "basis":"AUTHORED_BODY_NOT_PRESENT",
                })
            }),
    );
    operations
}

fn load_side(
    doc: &Document,
    side: Side,
    record: Option<&RecoveryRecord>,
    reader: &mut Reader,
    evidence: &mut Value,
) -> Result<Vec<Unit>, &'static str> {
    let projection = BodyProjection::read(doc, record, reader)?;
    evidence["recovery"]
        .as_array_mut()
        .expect("recovery evidence")
        .extend(projection.recovery_evidence(side));
    let units = projection.extract()?;
    let field = match side {
        Side::Baseline => "old_units",
        Side::Incoming => "new_units",
    };
    evidence[field] = json!(units.evidence());
    Ok(units.into_units())
}

fn document(
    identity: &Identity,
    old: Option<&Document>,
    new: &Document,
    recovery: &Recovery,
    options: &Options,
    reader: &mut Reader,
) -> Result<(Value, Value)> {
    let mut outcome =
        json!({"knowledge_base":identity.knowledge_base, "document_id":identity.document_id});
    let mut evidence = json!({
        "schema_version":"mdcompare-diff-evidence-1",
        "knowledge_base": identity.knowledge_base, "document_id": identity.document_id,
        "source_inputs": {"baseline":source_evidence(old)?, "incoming":source_evidence(Some(new))?},
        "old_units": [], "new_units": [], "changes": [], "recovery": [],
    });
    reader.begin_document();
    let units = (|| {
        let mut before = Vec::new();
        let mut after = Vec::new();
        for (side, doc, units) in [
            (Side::Baseline, old, &mut before),
            (Side::Incoming, Some(new), &mut after),
        ] {
            if let Some(doc) = doc {
                *units = load_side(
                    doc,
                    side,
                    recovery.get(&(identity.clone(), side)),
                    reader,
                    &mut evidence,
                )?;
            }
        }
        Ok::<_, &'static str>((before, after))
    })();
    let (old_units, new_units) = match units {
        Ok(value) => value,
        Err(reason) => {
            outcome["semantic_status"] = json!("UNAVAILABLE");
            outcome["action"] = json!("DEFER");
            outcome["reason_codes"] = json!([reason]);
            outcome["extraction"] = json!({"available":false});
            outcome["comparison_complete"] = json!(false);
            evidence["reason_codes"] = json!([reason]);
            return Ok((outcome, evidence));
        }
    };
    let (before, old_positions) = sequence(&old_units);
    let (after, new_positions) = sequence(&new_units);
    let aligned = align_units(&before, &after, options.work);
    let sequence_equal = before
        .iter()
        .map(|unit| unit.exact_key())
        .eq(after.iter().map(|unit| unit.exact_key()));
    let subject_equal = old.is_some_and(|old| {
        ["name", "logical_path"]
            .iter()
            .all(|key| old.metadata[key] == new.metadata[key])
    });
    let (status, action, reason) = if old.is_none() {
        ("NEW", "COMPILE", "NEW_DOCUMENT")
    } else if sequence_equal && subject_equal {
        ("EQUIVALENT", "SKIP", "CONTEXTUAL_NORMALIZED_EQUALITY")
    } else if !aligned.complete || aligned.items.iter().any(|item| item.kind == "unresolved") {
        ("UNCERTAIN", "COMPILE", "BODY_ALIGNMENT_UNCERTAIN")
    } else {
        ("CHANGED", "COMPILE", "AUTHORED_BODY_CHANGED")
    };
    outcome["semantic_status"] = json!(status);
    outcome["action"] = json!(action);
    outcome["reason_codes"] = json!([reason]);
    outcome["extraction"] = json!({
        "available": true, "old_units":before.len(), "new_units":after.len(),
        "incoming_body_status": if after.is_empty() { "NO_TEXT" } else { "AVAILABLE" },
    });
    outcome["comparison_complete"] = json!(aligned.complete);
    outcome["advisory"] = json!({"images_and_ocr":"EXCLUDED"});
    evidence["reason_codes"] = json!([reason]);
    evidence["changes"] = json!(changes(
        &before,
        &after,
        &aligned,
        &old_positions,
        &new_positions
    ));
    Ok((outcome, evidence))
}

fn emit(directory: &Path, diff_root: &Path, count: usize, evidence: &Value) -> Result<Value> {
    let path = directory.join(format!("{count:06}.json"));
    let mut data = serde_json::to_vec(evidence)?;
    data.push(b'\n');
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&path)
        .context("REPORT_WRITE_FAILED")?;
    file.write_all(&data).context("REPORT_WRITE_FAILED")?;
    Ok(json!({
        "knowledge_base": evidence["knowledge_base"], "document_id": evidence["document_id"],
        "evidence": path.strip_prefix(diff_root)?,
        "sha256": format!("{:x}", Sha256::digest(&data)),
    }))
}

pub(crate) fn compare_documents(
    snapshots: &Snapshots,
    staging: &Path,
    recovery_path: Option<&Path>,
    options: &Options,
    reader: &mut Reader,
    progress: &Progress,
) -> Result<()> {
    let Snapshots {
        baseline: old,
        incoming: new,
        order,
    } = snapshots;
    let recovery = load_recovery(recovery_path, reader)?;
    let directory = staging.join("evidence");
    fs::DirBuilder::new().mode(0o700).create(&directory)?;
    let (mut results, mut documents) = (Vec::new(), Vec::new());
    let mut statuses = BTreeMap::<String, usize>::new();
    let mut actions = BTreeMap::<String, usize>::new();
    for identity in order.iter().filter(|id| new.contains_key(*id)) {
        let incoming_doc = &new[identity];
        let (outcome, evidence) = document(
            identity,
            old.get(identity),
            incoming_doc,
            &recovery,
            options,
            reader,
        )?;
        *statuses
            .entry(
                outcome["semantic_status"]
                    .as_str()
                    .expect("semantic status")
                    .to_owned(),
            )
            .or_default() += 1;
        *actions
            .entry(
                outcome["action"]
                    .as_str()
                    .expect("semantic action")
                    .to_owned(),
            )
            .or_default() += 1;
        results.push(outcome);
        documents.push(emit(&directory, staging, documents.len(), &evidence)?);
        progress.update("compare", results.len(), new.len(), 0, 0);
    }
    ensure!(reader.changed_paths().is_empty(), "input_drift");
    let baseline_only: Vec<_> = order
        .iter()
        .filter(|identity| !new.contains_key(*identity))
        .map(|identity| {
            json!({
                "knowledge_base":identity.knowledge_base, "document_id":identity.document_id,
            })
        })
        .collect();
    let complete = !actions.contains_key("DEFER");
    let report = json!({
        "schema_version":"mdcompare-semantic-result-1", "scope":"authored_body", "results":results,
        "state":if complete {"complete"} else {"partial"}, "baseline_only":baseline_only,
        "rules":{
            "version":"authored-body-v1", "equivalence":"paired_contextual_sequence_equality",
            "uncertainty":"COMPILE", "images_and_ocr":"ADVISORY_ONLY", "model":"NONE",
        },
        "counts":{"total":results.len(), "statuses":statuses, "actions":actions},
    });
    let report_bytes = json_bytes(&report)?;
    let index = json!({
        "schema_version":"mdcompare-diff-index-1",
        "evidence_complete":complete,"coordinate_system":"original_source_lines",
        "documents":documents, "evidence_directory":"evidence",
        "report_sha256":format!("{:x}", Sha256::digest(&report_bytes)),
    });
    crate::workspace::atomic_write(&staging.join("result.json"), &report_bytes)?;
    crate::workspace::atomic_write(&staging.join("diff-index.json"), &json_bytes(&index)?)
}
