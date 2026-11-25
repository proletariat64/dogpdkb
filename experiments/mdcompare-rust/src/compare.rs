use crate::input::{Document, Identity, Reader};
use crate::{ocr, resources, text};
use crate::{summary::Progress, workspace::Snapshots};
use anyhow::{Result, ensure};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

const ACTIONS: [&str; 6] = [
    "SKIP_VERIFIED",
    "UPDATE_METADATA_ONLY",
    "UPDATE_TEXT_REUSE_OCR",
    "PROCESS_CHANGED_RESOURCES",
    "PROCESS_REQUIRED",
    "UNKNOWN",
];

fn metadata(old: Option<&Document>, new: &Document) -> Value {
    let fields: Vec<_> = crate::input::METADATA_KEYS
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
    json!({
        "status": status,
        "changed_fields": fields,
        "sync_timestamp_changed": old.is_some_and(|old|
            old.metadata.get("sync_timestamp") != new.metadata.get("sync_timestamp"))
    })
}

fn identity(id: &Identity) -> Value {
    json!({"knowledge_base":id.knowledge_base,"document_id":id.document_id})
}

fn historical(
    result: &mut Value,
    old: Option<&Document>,
    new: &Document,
    before: Option<&[u8]>,
    after: &[u8],
) {
    if new.content_kind() == "raw" && old.is_none_or(|document| document.content_kind() == "raw") {
        return;
    }
    result["observed_status"] = result["status"].clone();
    result["status"] = json!("UNKNOWN");
    result["comparison_scope"] = json!(format!(
        "{}_TO_{}",
        old.map_or("none", Document::content_kind)
            .to_ascii_uppercase(),
        new.content_kind().to_ascii_uppercase()
    ));
    result["provenance_verified"] = json!(false);
    let reasons = result["reason_codes"].as_array_mut().expect("text reasons");
    reasons.push(json!("HISTORICAL_RAW_UNAVAILABLE"));
    let marker = b"<!-- mdocr:ocr-";
    let has_marker = |bytes: &[u8]| bytes.windows(marker.len()).any(|part| part == marker);
    if !has_marker(after) && !before.is_some_and(has_marker) {
        reasons.push(json!("HISTORICAL_UNMARKED_CONTENT"));
    }
}

fn action(
    old_exists: bool,
    text: &Value,
    resources: &Value,
    metadata: &Value,
    ocr: &Value,
) -> &'static str {
    if [text, resources, ocr]
        .iter()
        .any(|dimension| dimension["status"] == "UNKNOWN")
    {
        "UNKNOWN"
    } else if !old_exists {
        "PROCESS_REQUIRED"
    } else if matches!(resources["status"].as_str(), Some("NEW" | "CHANGED")) {
        "PROCESS_CHANGED_RESOURCES"
    } else if !matches!(ocr["status"].as_str(), Some("UNCHANGED" | "NOT_APPLICABLE")) {
        "PROCESS_REQUIRED"
    } else if text["status"] == "CHANGED" {
        "UPDATE_TEXT_REUSE_OCR"
    } else if metadata["status"] == "CHANGED" {
        "UPDATE_METADATA_ONLY"
    } else {
        "SKIP_VERIFIED"
    }
}

fn reasons(value: &Value) -> impl Iterator<Item = &str> {
    value["reason_codes"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
}

pub(crate) fn render(report: &Value) -> Result<Vec<u8>> {
    use std::fmt::Write;
    let mut output = format!(
        "# mdcompare report\n\nDocuments: {}\n\n",
        report["counts"]["total"]
    );
    for row in report["results"].as_array().expect("report rows") {
        let string = |key: &str| row[key].as_str().expect("report string");
        writeln!(
            output,
            "## {} / {}\n",
            string("knowledge_base"),
            string("document_id")
        )?;
        writeln!(output, "Action: {}\n", string("action"))?;
        writeln!(
            output,
            "Reasons: {}\n",
            reasons(row).collect::<Vec<_>>().join(", ")
        )?;
        for dim in ["text", "resources", "metadata", "ocr"] {
            writeln!(
                output,
                "- {dim}: {}",
                row[dim]["status"].as_str().expect("dimension status")
            )?;
        }
        let differences = &row["text"]["differences"];
        if differences
            .as_array()
            .is_some_and(|values| !values.is_empty())
        {
            writeln!(
                output,
                "\nText differences (bounded JSON):\n\n```json\n{}\n```",
                serde_json::to_string_pretty(differences)?
            )?;
        }
        writeln!(output)?;
    }
    Ok(output.into_bytes())
}

pub(crate) fn compare_documents(
    snapshots: &Snapshots,
    opts: &text::Options,
    reader: &mut Reader,
    progress: &Progress,
) -> Result<Value> {
    let Snapshots {
        baseline: old,
        incoming: new,
        order,
    } = snapshots;
    let mut errors = BTreeSet::new();
    let mut results = Vec::new();
    for id in order.iter().filter(|id| new.contains_key(*id)) {
        let right = &new[id];
        reader.begin_document();
        let left = old.get(id);
        let metadata = metadata(left, right);
        let mut resources = json!({"status":"UNKNOWN","reason_codes":[],"items":[],
            "processing_required":[],"references_changed":null});
        let mut ocr = json!({"status":"UNKNOWN","reason_codes":[],"items":[],
            "reusable":[],"processing_required":[]});
        let documents = reader.read(&right.path).and_then(|after| {
            let before = left
                .map(|document| reader.read(&document.path))
                .transpose()?;
            Ok((before, after))
        });
        let text = match documents {
            Err(reason) => {
                errors.insert((id, reason));
                text::result("UNKNOWN", &[reason], None)
            }
            Ok((before, after)) => {
                let before = before.as_deref();
                let mut value = text::compare(before, &after, opts);
                historical(&mut value, left, right, before, &after);
                resources = resources::compare(left, right, before, Some(&after), reader);
                ocr = ocr::compare(left, right, &resources, reader);
                value
            }
        };
        let action = action(left.is_some(), &text, &resources, &metadata, &ocr);
        let codes = [&text, &resources, &ocr]
            .into_iter()
            .flat_map(reasons)
            .collect::<BTreeSet<_>>();
        results.push((
            id,
            json!({"knowledge_base":id.knowledge_base,"document_id":id.document_id,
                "metadata":metadata,"text":text,"resources":resources,"ocr":ocr,
                "action":action,"reason_codes":codes}),
        ));
        progress.update("compare", results.len(), new.len(), 0, 0);
    }

    ensure!(reader.changed_paths().is_empty(), "input_drift");
    let mut actions = BTreeMap::from(ACTIONS.map(|action| (action, 0usize)));
    let mut rows = Vec::with_capacity(results.len());
    for (_, row) in results {
        *actions
            .get_mut(row["action"].as_str().expect("action string"))
            .expect("known action") += 1;
        rows.push(row);
    }
    let complete = actions["UNKNOWN"] == 0 && errors.is_empty();
    let rules = if opts.normalize {
        json!([{"name":"line-endings-lf","version":"1","contract":"CRLF and lone CR become LF; all other bytes are preserved"}])
    } else {
        json!([{"name":"identity","version":"1"}])
    };
    let absent: Vec<_> = order
        .iter()
        .filter(|id| !new.contains_key(*id))
        .map(identity)
        .collect();
    let errors: Vec<_> = errors
        .into_iter()
        .map(|(id, reason)| {
            json!({
                "knowledge_base":id.knowledge_base,"document_id":id.document_id,"reason_code":reason
            })
        })
        .collect();
    Ok(json!({
        "schema_version":"mdcompare-strict-result-1","rules":rules,"results":rows,
        "state":if complete {"complete"} else {"partial"},
        "baseline_only":absent,"counts":{"total":rows.len(),"actions":actions},"errors":errors,
    }))
}
