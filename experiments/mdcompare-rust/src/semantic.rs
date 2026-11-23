use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf, absolute};

use anyhow::{Context, Result, ensure};
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::input::{Document, Identity, MANIFEST_LIMIT, Reader, json_bytes, parse_utf8_json};
use crate::knowledge::extract_document_units;
use crate::knowledge_units::{Alignment, Unit, align_units, source_lines};
use crate::text::Options;

#[derive(Clone, Copy, Eq, Hash, PartialEq)]
enum Side {
    Baseline,
    Incoming,
}

impl Side {
    fn parse(value: &Value) -> Option<Self> {
        match value.as_str()? {
            "baseline" => Some(Self::Baseline),
            "incoming" => Some(Self::Incoming),
            _ => None,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Baseline => "baseline",
            Self::Incoming => "incoming",
        }
    }

    fn unit_field(self) -> &'static str {
        match self {
            Self::Baseline => "old_units",
            Self::Incoming => "new_units",
        }
    }
}

struct Record {
    source_copy: PathBuf,
    insertions: Vec<Value>,
}

type Recovery = HashMap<(Identity, Side), Record>;

fn load_recovery(path: Option<&Path>, reader: &mut Reader) -> Result<Recovery> {
    const INVALID: &str = "RECOVERY_MANIFEST_INVALID";
    let Some(path) = path else {
        return Ok(Recovery::new());
    };
    let bytes = reader
        .read_with_limit(path, MANIFEST_LIMIT)
        .map_err(|_| anyhow::anyhow!(INVALID))?;
    let value = parse_utf8_json(&bytes).map_err(|_| anyhow::anyhow!(INVALID))?;
    ensure!(
        value["schema_version"] == "mdcompare-body-recovery-1",
        INVALID
    );
    let root = path.parent().context(INVALID)?;
    let mut recovery = Recovery::new();
    for row in value["documents"].as_array().context(INVALID)? {
        let field = |key| {
            row[key]
                .as_str()
                .filter(|value| !value.is_empty())
                .context(INVALID)
        };
        let side = Side::parse(&row["side"]).context(INVALID)?;
        let identity = Identity {
            knowledge_base: field("knowledge_base")?.to_owned(),
            document_id: field("document_id")?.to_owned(),
        };
        let record = Record {
            source_copy: root.join(field("source_copy")?),
            insertions: row["insertions"].as_array().context(INVALID)?.clone(),
        };
        ensure!(recovery.insert((identity, side), record).is_none(), INVALID);
    }
    Ok(recovery)
}

#[derive(Serialize)]
struct Repair<'a> {
    byte_offset: usize,
    text: &'a str,
    source_line: usize,
}

fn line_offsets(text: &str) -> Vec<usize> {
    std::iter::once(0)
        .chain(source_lines(text, true).scan(0, |offset, line| {
            *offset += line.len();
            Some(*offset)
        }))
        .collect()
}

fn repair<'a>(
    original: &str,
    record: Option<&'a Record>,
    reader: &mut Reader,
) -> std::result::Result<(String, Vec<Repair<'a>>), &'static str> {
    const INVALID: &str = "RECOVERY_INSERTION_INVALID";
    let Some(record) = record else {
        return Ok((original.to_owned(), Vec::new()));
    };
    if reader.read(&record.source_copy)? != original.as_bytes() {
        return Err("RECOVERY_SOURCE_DRIFT");
    }
    let mut repairs = Vec::new();
    let mut previous = 0;
    for entry in &record.insertions {
        let byte_offset = entry["byte_offset"]
            .as_u64()
            .and_then(|value| usize::try_from(value).ok())
            .ok_or(INVALID)?;
        let text = entry["text"]
            .as_str()
            .filter(|text| text.ends_with('\n'))
            .ok_or(INVALID)?;
        if byte_offset < previous
            || !original.is_char_boundary(byte_offset)
            || !source_lines(text, false).all(|line| {
                matches!(
                    line,
                    "" | "<!-- mdocr:ocr-begin -->" | "<!-- mdocr:ocr-end -->"
                )
            })
        {
            return Err(INVALID);
        }
        previous = byte_offset;
        let prefix = &original[..byte_offset];
        let source_line = source_lines(prefix, false).count()
            + usize::from(prefix.is_empty() || prefix.ends_with(['\r', '\n']));
        repairs.push(Repair {
            byte_offset,
            text,
            source_line,
        });
    }
    let mut projected = String::new();
    let mut position = 0;
    for repair in &repairs {
        projected.push_str(&original[position..repair.byte_offset]);
        projected.push_str(repair.text);
        position = repair.byte_offset;
    }
    projected.push_str(&original[position..]);
    Ok((projected, repairs))
}

fn coordinate_map(original: &str, projected: &str, repairs: &[Repair<'_>]) -> Vec<usize> {
    let original_offsets = line_offsets(original);
    let mut inserted = 0;
    let mut inserts = repairs.iter().peekable();
    line_offsets(projected)
        .into_iter()
        .map(|position| {
            let mut source = position - inserted;
            while let Some(next) = inserts.peek() {
                let start = next.byte_offset + inserted;
                if position < start {
                    break;
                }
                if position < start + next.text.len() {
                    source = next.byte_offset;
                    break;
                }
                inserted += next.text.len();
                inserts.next();
                source = position - inserted;
            }
            original_offsets
                .partition_point(|&offset| offset <= source)
                .min(original_offsets.len().saturating_sub(1).max(1))
        })
        .collect()
}

fn unit_evidence(unit: &Unit, coordinates: Option<&[usize]>) -> Value {
    let mut evidence = json!(unit);
    if let Some(coordinates) = coordinates {
        evidence["projected_start_line"] = json!(unit.start_line);
        evidence["projected_end_line"] = json!(unit.end_line);
        evidence["start_line"] = json!(coordinates[unit.start_line - 1]);
        evidence["end_line"] = json!(coordinates[unit.end_line - 1]);
    }
    evidence
}

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
    let reserved: HashSet<_> = aligned
        .items
        .iter()
        .take(after.len())
        .filter_map(|item| item.old)
        .collect();
    let mut used = HashSet::new();
    let mut exact = HashMap::<_, VecDeque<usize>>::new();
    let mut movable = HashMap::<_, BTreeSet<usize>>::new();
    for (index, unit) in before.iter().enumerate() {
        exact.entry(unit.exact_key()).or_default().push_back(index);
        if !reserved.contains(&index) {
            movable
                .entry((unit.kind, unit.key.as_str()))
                .or_default()
                .insert(index);
        }
    }
    let mut operations = Vec::new();
    for (index, item) in aligned.items.iter().take(after.len()).enumerate() {
        let current = after[index];
        let mut previous = item.old;
        let operation = match item.kind {
            "reuse" => {
                let bucket = exact.entry(current.exact_key()).or_default();
                while bucket
                    .front()
                    .is_some_and(|candidate| used.contains(candidate))
                {
                    bucket.pop_front();
                }
                previous = bucket.pop_front();
                match previous {
                    None => "add",
                    Some(previous) if previous != index => "move",
                    Some(_) => "equal",
                }
            }
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

fn source(document: Option<&Document>) -> Result<Value> {
    document.map_or(Ok(Value::Null), |doc| {
        Ok(json!({
            "path": absolute(&doc.path)?,
            "content_kind": doc.content_kind(),
            "metadata": doc.metadata,
            "version": doc.raw["provenance"]["version"],
        }))
    })
}

fn load_side(
    doc: &Document,
    side: Side,
    record: Option<&Record>,
    reader: &mut Reader,
    evidence: &mut Value,
) -> std::result::Result<Vec<Unit>, &'static str> {
    let original = reader.read(&doc.path)?;
    let original = std::str::from_utf8(&original).map_err(|_| "TEXT_ENCODING_UNSUPPORTED")?;
    let (projected, repairs) = repair(original, record, reader)?;
    evidence["recovery"].as_array_mut().expect("recovery evidence").extend(repairs.iter().map(|item| json!({
        "side": side.label(), "byte_offset": item.byte_offset, "text": item.text, "source_line": item.source_line,
    })));
    let units = extract_document_units(doc, &projected)?;
    let coordinates = (!repairs.is_empty()).then(|| coordinate_map(original, &projected, &repairs));
    evidence[side.unit_field()] = json!(
        units
            .iter()
            .map(|unit| unit_evidence(unit, coordinates.as_deref()))
            .collect::<Vec<_>>()
    );
    Ok(units)
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
        "source_inputs": {"baseline":source(old)?, "incoming":source(Some(new))?},
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
    old: &BTreeMap<Identity, Document>,
    new: &BTreeMap<Identity, Document>,
    order: &[Identity],
    staging: &Path,
    recovery_path: Option<&Path>,
    options: &Options,
    reader: &mut Reader,
) -> Result<()> {
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
