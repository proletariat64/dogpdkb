use crate::{offline::Snapshots, summary::Progress};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

use anyhow::Result;
use serde_json::{Value, json};

use crate::input::{Document, Identity, Reader};
use crate::knowledge_units::{
    KINDS, Unit, align_units, extract_units, prose_key, source_lines, table_key,
};
use crate::text::Options;

const STATUSES: [&str; 5] = ["REUSE", "DELTA", "REVIEW", "NO_BODY", "UNKNOWN"];
const GRAPHIFY_ACTIONS: [&str; 3] = ["KEEP_EXISTING", "HOLD_EXISTING", "BODY_DELTA_CANDIDATE"];

pub fn extract_document_units(document: &Document, text: &str) -> Result<Vec<Unit>, &'static str> {
    let name = document.metadata["name"].as_str().unwrap_or_default();
    let normalized = text.replace("\r\n", "\n").replace('\r', "\n");
    let mut lines = source_lines(&normalized, true);
    let mut header = None;
    if let (Some(title), Some(origin)) = (lines.next(), lines.next()) {
        let origin_text = origin.trim_end_matches('\n');
        let origin_prefix = format!("doc_id: {} · book: ", document.identity.document_id);
        static EXPORTER: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
            regex::Regex::new(r"^.+ \(book_id [0-9]+\) · word_count: [0-9]+$").unwrap()
        });
        if !name.is_empty()
            && title.trim_end_matches('\n').strip_prefix("# ") == Some(name)
            && origin_text
                .strip_prefix(&origin_prefix)
                .is_some_and(|rest| EXPORTER.is_match(rest))
        {
            header = Some((title.len(), title.len() + origin_text.len()));
        }
    }
    drop(lines);
    let mut normalized = normalized;
    if let Some((start, end)) = header {
        normalized.replace_range(start..end, "");
    }
    let mut units = extract_units(&normalized, &document.raw["body_scope"])?;
    if let Some((title, rest)) = units
        .split_first_mut()
        .filter(|(title, _)| title.kind == "heading" && title.start_line == 1 && title.text == name)
    {
        title.exporter_title = header.is_some();
        for unit in rest {
            if unit.context.first() == Some(&title.key) {
                unit.context.remove(0);
            }
        }
    }
    Ok(units)
}

fn read_units(document: &Document, reader: &mut Reader) -> Result<Vec<Unit>, &'static str> {
    reader.begin_document();
    let data = reader.read(&document.path)?;
    let text = std::str::from_utf8(&data).map_err(|_| "TEXT_ENCODING_UNSUPPORTED")?;
    extract_document_units(document, text)
}

fn rich_layout_key(unit: &Unit) -> Option<String> {
    static TAG: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
        regex::Regex::new(r"(?i)<(?:font|span|u|div|p|center|b|strong|em|i|table|tr|td|th|thead|tbody|tfoot|br)\b").unwrap()
    });
    static LITERAL: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
        regex::Regex::new(r"(?i)<(?:pre|code|script|style)\b|white-space\s*:").unwrap()
    });
    if !matches!(unit.kind, "code" | "paragraph" | "table") {
        return None;
    }
    let text = unit.text.as_str();
    let first = text.trim_start();
    let rich_table = unit.kind == "table" || first.starts_with('|') && TAG.is_match(text);
    let rich_prose = TAG.find(first).is_some_and(|tag| tag.start() == 0);
    if (unit.kind == "code" && !rich_table && !rich_prose) || LITERAL.is_match(text) {
        return None;
    }
    Some(if rich_table {
        table_key(text)
    } else {
        format!("prose:{}", prose_key(text))
    })
}

fn zero_counts(names: &[&str]) -> Value {
    Value::Object(
        names
            .iter()
            .map(|name| (name.to_string(), json!(0)))
            .collect(),
    )
}

fn increment(counts: &mut Value, key: &str, value: u64) {
    counts[key] = json!(counts[key].as_u64().unwrap_or_default() + value);
}

fn identity_json(identity: &Identity) -> Value {
    json!({"knowledge_base": identity.knowledge_base, "document_id": identity.document_id})
}

fn error(document: &Document, side: &str, reason: &str) -> Value {
    json!({"knowledge_base": document.identity.knowledge_base, "document_id": document.identity.document_id,
        "side":side,"reason_code":reason})
}

fn reference(document: &Document, unit: &Unit) -> Value {
    json!({
        "knowledge_base": document.identity.knowledge_base, "document_id": document.identity.document_id,
        "start_line": unit.start_line, "end_line": unit.end_line,
    })
}

type IndexKey = (String, String, String, &'static str, Vec<String>, String);
type UnitIndex<'a> = HashMap<IndexKey, (&'a Document, Unit, &'static str)>;

fn index_key(document: &Document, unit: &Unit) -> IndexKey {
    let metadata = |key| {
        document.metadata[key]
            .as_str()
            .unwrap_or_default()
            .to_owned()
    };
    (
        document.identity.knowledge_base.clone(),
        metadata("name"),
        metadata("logical_path"),
        unit.kind,
        unit.context.clone(),
        unit.key.clone(),
    )
}

fn plan(row: &Value) -> Value {
    let unavailable = row["status"] == "UNKNOWN" || row["comparison_complete"] != true;
    let (mut candidates, mut context, mut presentation) = (Vec::new(), Vec::new(), Vec::new());
    let units = row["units"].as_array().expect("knowledge units");
    if !unavailable {
        for (i, unit) in units
            .iter()
            .enumerate()
            .filter(|(_, unit)| !unit["new"].is_null())
        {
            let heading = unit["unit_type"] == "heading";
            let reuse = unit["kind"] == "reuse";
            let seen = unit["body_seen_before"] == true;
            let layout = unit["presentation_seen_before"] == true;
            let eligible = matches!(unit["kind"].as_str(), Some("add" | "revise"))
                || unit["kind"] == "unresolved" && unit["basis"] == "AMBIGUOUS_BODY_ALIGNMENT";
            if unit["subject_context_changed"] == true || (!heading && !seen && !layout && eligible)
            {
                candidates.push(i);
            }
            if !reuse && (seen || heading) {
                context.push(i);
            }
            if !reuse && layout {
                presentation.push(i);
            }
        }
        if candidates.is_empty() {
            candidates.extend(units.iter().enumerate().filter_map(|(i, unit)| {
                (!unit["new"].is_null() && unit["body_context_changed"] == true).then_some(i)
            }));
        }
    }
    let action = if unavailable {
        "HOLD_EXISTING"
    } else if !candidates.is_empty() {
        "BODY_DELTA_CANDIDATE"
    } else if row["status"] == "REUSE" {
        "KEEP_EXISTING"
    } else {
        "HOLD_EXISTING"
    };
    let mut reasons = Vec::new();
    if action == "HOLD_EXISTING" {
        if row["status"] == "UNKNOWN" {
            reasons.push("COMPARISON_UNAVAILABLE");
        } else if row["comparison_complete"] != true {
            reasons.push("COMPARISON_INCOMPLETE");
        }
        if row["reason_codes"]
            .as_array()
            .is_some_and(|items| items.iter().any(|item| item == "BASELINE_BODY_INCOMPLETE"))
        {
            reasons.push("BASELINE_BODY_INCOMPLETE");
        }
        if !unavailable {
            for (condition, reason) in [
                (
                    row["counts"]["old_only_review"]
                        .as_u64()
                        .unwrap_or_default()
                        != 0,
                    "OLD_BODY_ONLY",
                ),
                (!context.is_empty(), "CONTEXT_REVIEW"),
                (!presentation.is_empty(), "PRESENTATION_REVIEW"),
                (row["status"] == "NO_BODY", "NO_BODY"),
            ] {
                if condition {
                    reasons.push(reason);
                }
            }
        }
        if reasons.is_empty() {
            reasons.push("NO_NEW_BODY_CANDIDATE");
        }
    }
    json!({
        "action": action, "hold_reason_codes": reasons, "plan_only": true,
        "automatic_extraction_allowed": false, "full_document_fallback": false,
        "candidate_unit_indexes": candidates, "context_review_unit_indexes": context,
        "presentation_review_unit_indexes": presentation,
        "candidate_body_chars": candidates.iter().filter_map(|&i| units[i]["new_body_chars"].as_u64()).sum::<u64>(),
    })
}

fn document_row<'a>(
    document: &'a Document,
    old: Option<&'a Document>,
    previous: &[Unit],
    current: &[Unit],
    incomplete_books: &HashSet<&str>,
    index: &UnitIndex<'a>,
    options: &Options,
    row: &mut Value,
) {
    let previous_ref: Vec<_> = previous.iter().collect();
    let current_ref: Vec<_> = current.iter().collect();
    let aligned = align_units(&previous_ref, &current_ref, options.work);
    let observed: HashMap<_, _> = previous
        .iter()
        .map(|unit| ((unit.kind, unit.key.as_str()), unit))
        .collect();
    let layout: HashMap<_, _> = previous
        .iter()
        .filter_map(|unit| rich_layout_key(unit).map(|key| (key, unit)))
        .collect();
    let old_headings = previous.iter().any(|unit| unit.kind == "heading");
    let mut remaining = options.chars;
    let mut truncated = false;
    let mut items = Vec::new();
    let mut reasons = Vec::new();
    let mut counts = zero_counts(&KINDS);
    for item in aligned.items {
        let (mut kind, mut basis, mut similarity) = (item.kind, item.basis, item.similarity);
        let mut before = item.old.map(|i| &previous[i]);
        let after = item.new.map(|i| &current[i]);
        let mut source = old;
        let mut source_side = "baseline";
        if let Some(unit) = after.filter(|_| before.is_none() && kind == "add") {
            if let Some((doc, unit, side)) = index.get(&index_key(document, unit)).filter(|_| {
                !document.metadata["name"]
                    .as_str()
                    .unwrap_or_default()
                    .trim()
                    .is_empty()
            }) {
                (source, before, source_side) = (Some(*doc), Some(unit), side);
                kind = "reuse";
                basis = if *side == "baseline" {
                    "BASELINE_CONTEXTUAL_BODY_REUSE"
                } else {
                    "INCOMING_CONTEXTUAL_BODY_REUSE"
                };
                similarity = Some(1.0);
            } else if old.is_none()
                && incomplete_books.contains(document.identity.knowledge_base.as_str())
            {
                kind = "unresolved";
                basis = "BASELINE_BODY_INCOMPLETE";
            }
        }
        if kind == "reuse"
            && old.is_some_and(|doc| doc.metadata["name"] != document.metadata["name"])
        {
            kind = "unresolved";
            basis = "DOCUMENT_SUBJECT_CONTEXT_CHANGED";
        }
        let seen = after
            .and_then(|unit| observed.get(&(unit.kind, unit.key.as_str())))
            .copied();
        let layout_match = after
            .filter(|_| seen.is_none())
            .and_then(rich_layout_key)
            .and_then(|key| layout.get(&key))
            .copied();
        let located = after
            .or(before)
            .expect("every alignment item refers to a unit");
        let subject_changed = after.is_some_and(|unit| {
            basis == "DOCUMENT_SUBJECT_CONTEXT_CHANGED"
                || unit.kind == "heading" && !unit.exporter_title && old_headings && seen.is_none()
        });
        let body_context_changed = after.is_some_and(|unit| {
            unit.kind != "heading"
                && kind != "reuse"
                && seen.is_some_and(|previous| previous.context != unit.context)
        });
        let mut entry = json!({
            "kind": kind, "unit_type": located.kind, "subject_context_changed": subject_changed,
            "body_context_changed": body_context_changed,
            "new_body_chars": after.map_or(0, |unit| unit.text.chars().count()),
            "body_seen_before": seen.is_some(),
            "existing_body": old.zip(seen).map(|(doc, unit)| reference(doc, unit)),
            "presentation_seen_before": layout_match.is_some(),
            "existing_presentation_body": old.zip(layout_match).map(|(doc, unit)| reference(doc, unit)),
            "basis": basis, "similarity": similarity,
            "old": source.zip(before).map(|(doc, unit)| reference(doc, unit)),
            "old_side": before.map(|_| source_side),
            "new": after.map(|unit| reference(document, unit)),
        });
        if options.evidence {
            let mut evidence = json!({});
            for (label, unit) in [("old", before), ("new", after)] {
                if let Some(unit) = unit {
                    let (slice, length) = excerpt(&unit.text, remaining);
                    remaining -= length;
                    truncated |= slice.len() < unit.text.len();
                    evidence[label] = json!(slice);
                }
            }
            entry["evidence"] = evidence;
        }
        increment(&mut counts, kind, 1);
        items.push(entry);
    }
    let status = if items.is_empty() {
        "NO_BODY"
    } else if ["unresolved", "old_only_review", "revise"]
        .iter()
        .any(|kind| counts[kind].as_u64().unwrap_or_default() != 0)
    {
        "REVIEW"
    } else if counts["add"].as_u64().unwrap_or_default() != 0 {
        "DELTA"
    } else {
        "REUSE"
    };
    if !aligned.complete {
        reasons.push("KNOWLEDGE_WORK_BUDGET_EXCEEDED");
    }
    for code in [
        "BASELINE_BODY_INCOMPLETE",
        "DOCUMENT_SUBJECT_CONTEXT_CHANGED",
    ] {
        if items.iter().any(|item| item["basis"] == code) {
            reasons.push(code);
        }
    }
    row["counts"] = counts;
    row["units"] = json!(items);
    row["status"] = json!(status);
    row["reason_codes"] = json!(reasons);
    row["comparison_complete"] = json!(aligned.complete);
    row["evidence_truncated"] = json!(truncated);
}

fn excerpt(text: &str, limit: usize) -> (&str, usize) {
    let mut length = 0;
    for (pos, _) in text.char_indices() {
        if length == limit {
            return (&text[..pos], length);
        }
        length += 1;
    }
    (text, length)
}

pub(crate) fn compare_documents(
    snapshots: &Snapshots,
    options: &Options,
    reader: &mut Reader,
    progress: &Progress,
) -> Value {
    let Snapshots {
        baseline,
        incoming,
        order,
    } = snapshots;
    let mut old_units = BTreeMap::new();
    let mut failed = BTreeMap::new();
    let mut index = UnitIndex::new();
    let mut errors = Vec::new();
    for (identity, document) in baseline {
        match read_units(document, reader) {
            Ok(units) => {
                for unit in &units {
                    index
                        .entry(index_key(document, unit))
                        .or_insert_with(|| (document, unit.clone(), "baseline"));
                }
                old_units.insert(identity, units);
            }
            Err(reason) => {
                failed.insert(identity, reason);
                errors.push(error(document, "baseline", reason));
            }
        }
    }
    let incomplete_books = failed
        .keys()
        .map(|identity| identity.knowledge_base.as_str())
        .collect();
    let mut results = Vec::new();
    for (identity, document) in incoming {
        let old = baseline.get(identity);
        let historical = document.content_kind() == "enriched"
            || old.is_some_and(|doc| doc.content_kind() == "enriched");
        let mut row = json!({
            "knowledge_base": identity.knowledge_base, "document_id": identity.document_id,
            "status":"UNKNOWN","counts":zero_counts(&KINDS),"units":[],"reason_codes":[],
            "comparison_complete":false,"evidence_truncated":false,
            "source_limitations": if historical { json!(["HISTORICAL_UNMARKED_TEXT_ORIGIN_UNVERIFIED"]) } else { json!([]) },
        });
        match read_units(document, reader) {
            Ok(units) => {
                if let Some(reason) = failed.get(identity) {
                    row["reason_codes"] = json!([reason]);
                } else {
                    let previous = old_units.get(identity).map_or(&[][..], Vec::as_slice);
                    document_row(
                        document,
                        old,
                        previous,
                        &units,
                        &incomplete_books,
                        &index,
                        options,
                        &mut row,
                    );
                    for unit in units {
                        index
                            .entry(index_key(document, &unit))
                            .or_insert((document, unit, "incoming"));
                    }
                }
            }
            Err(reason) => {
                row["reason_codes"] = json!([reason]);
                errors.push(error(document, "incoming", reason));
            }
        }
        results.push(row);
        progress.update("compare", results.len(), incoming.len(), 0, 0);
    }
    if !reader.changed_paths().is_empty() {
        for row in &mut results {
            row["status"] = json!("UNKNOWN");
            row["comparison_complete"] = json!(false);
            let mut reasons: BTreeSet<_> = row["reason_codes"]
                .as_array()
                .expect("reason codes")
                .iter()
                .filter_map(Value::as_str)
                .collect();
            reasons.insert("INPUT_CHANGED");
            row["reason_codes"] = json!(reasons);
            let units = row["units"].as_array_mut().expect("knowledge units");
            for unit in units.iter_mut() {
                unit["kind"] = json!("unresolved");
                unit["basis"] = json!("INPUT_CHANGED");
            }
            let unresolved = units.len();
            row["counts"] = zero_counts(&KINDS);
            row["counts"]["unresolved"] = json!(unresolved);
        }
        errors.push(json!({"reason_code":"INPUT_CHANGED","side":"snapshot"}));
    }
    let (mut statuses, mut units, mut actions) = (
        zero_counts(&STATUSES),
        zero_counts(&KINDS),
        zero_counts(&GRAPHIFY_ACTIONS),
    );
    let (mut candidate_chars, mut context, mut presentation) = (0, 0, 0);
    for row in &mut results {
        let plan = plan(row);
        increment(
            &mut statuses,
            row["status"].as_str().expect("knowledge status"),
            1,
        );
        increment(
            &mut actions,
            plan["action"].as_str().expect("Graphify action"),
            1,
        );
        for kind in KINDS {
            increment(
                &mut units,
                kind,
                row["counts"][kind].as_u64().unwrap_or_default(),
            );
        }
        candidate_chars += plan["candidate_body_chars"].as_u64().unwrap_or_default();
        context += usize::from(
            plan["context_review_unit_indexes"]
                .as_array()
                .is_some_and(|items| !items.is_empty()),
        );
        presentation += usize::from(
            plan["presentation_review_unit_indexes"]
                .as_array()
                .is_some_and(|items| !items.is_empty()),
        );
        row["graphify_plan"] = plan;
    }
    let positions: HashMap<_, _> = order
        .iter()
        .enumerate()
        .map(|(position, identity)| {
            (
                (
                    identity.knowledge_base.as_str(),
                    identity.document_id.as_str(),
                ),
                position,
            )
        })
        .collect();
    results.sort_by_key(|row| {
        positions[&(
            row["knowledge_base"].as_str().unwrap(),
            row["document_id"].as_str().unwrap(),
        )]
    });
    let complete = statuses["UNKNOWN"] == 0 && statuses["REVIEW"] == 0 && errors.is_empty();
    let baseline_only: Vec<_> = order
        .iter()
        .filter(|id| !incoming.contains_key(*id))
        .map(identity_json)
        .collect();
    json!({
        "schema_version": "mdcompare-knowledge-result-1", "scope":"body_only", "errors":errors,
        "state":if complete {"complete"} else {"partial"},
        "rules":{
            "version": "body-units-v5",
            "reuse": "contextual_normalized_body_equality",
            "generated_regions": "outside_code_or_verified_generated_regions_v1",
            "exporter_header": "matching_title_and_document_id_v1",
            "similarity": "revision_candidate_only", "images_and_ocr": "excluded",
            "cross_document": "same_book_name_path_and_unit_context",
            "max_work_per_document": options.work, "automatic_knowledge_merge": false,
            "graphify_execution": "plan_only_no_full_document_fallback",
        },
        "results":results, "baseline_only":baseline_only,
        "counts":{
            "total":results.len(),"statuses":statuses,"units":units,
            "graphify_plan":{
                "actions":actions,"candidate_body_chars":candidate_chars,
                "automatic_full_document_extractions":0,"context_review_documents":context,
                "presentation_review_documents":presentation,
            },
        },
    })
}

pub(crate) fn render(report: &Value) -> Result<Vec<u8>> {
    use std::fmt::Write;
    let mut text = format!(
        "# mdcompare body knowledge report\n\nScope: body_only. Images and OCR are excluded; no production writes.\n\nDocuments: {}\n\nUnit counts: {}\n\nGraphify plan (not an extraction queue): {}\n\nCandidates require confirmation. No automatic extraction or full-document fallback; the current Graphify source-replacement API cannot execute these as patches.\n\n",
        report["counts"]["total"], report["counts"]["units"], report["counts"]["graphify_plan"],
    );
    for row in report["results"].as_array().expect("knowledge rows") {
        writeln!(
            text,
            "## {} / {}\n\nStatus: {}\n\nCounts: {}\n\nGraphify plan: {}\n\n```json\n{}\n```\n",
            row["knowledge_base"].as_str().unwrap_or_default(),
            row["document_id"].as_str().unwrap_or_default(),
            row["status"].as_str().unwrap_or_default(),
            row["counts"],
            row["graphify_plan"],
            serde_json::to_string_pretty(&row["units"])?,
        )?;
    }
    Ok(text.into_bytes())
}
