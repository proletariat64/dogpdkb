use crate::input::{Document, Reader};
use crate::resources::entries;
use regex::bytes::Regex;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

type Index<'a> = BTreeMap<&'a str, &'a Value>;
type Outcome = (&'static str, &'static str);

fn outcome(status: &'static str, reason: &'static str) -> Outcome {
    (status, reason)
}

fn records(doc: &Document) -> Result<Option<Index<'_>>, &'static str> {
    let evidence = &doc.raw["ocr"];
    let Some(images) = evidence["images"]
        .as_array()
        .filter(|_| evidence["contract"] == "mdocr-ocr-2")
    else {
        return Ok(None);
    };
    if images.len() > 10_000 {
        return Err("OCR_RECORD_BUDGET_EXCEEDED");
    }
    let mut result = BTreeMap::new();
    for record in images {
        let Some(id) = record["resource_id"].as_str().filter(|id| !id.is_empty()) else {
            return Ok(None);
        };
        if result.insert(id, record).is_some() {
            return Ok(None);
        }
    }
    Ok(Some(result))
}

fn resource_index(doc: &Document) -> Index<'_> {
    let mut result = BTreeMap::new();
    for entry in entries(doc) {
        if let Some(id) = entry["resource_id"].as_str() {
            result.entry(id).or_insert(entry);
        }
    }
    result
}

struct ReuseInputs<'a> {
    old_records: Option<Index<'a>>,
    new_records: Option<Index<'a>>,
    old_resources: Index<'a>,
    new_resources: Index<'a>,
}

fn reuse_inputs<'a>(old: &'a Document, new: &'a Document) -> Result<ReuseInputs<'a>, &'static str> {
    Ok(ReuseInputs {
        old_records: records(old)?,
        new_records: records(new)?,
        old_resources: resource_index(old),
        new_resources: resource_index(new),
    })
}

fn data(document: &Document, reference: &Value, reader: &mut Reader) -> Result<Vec<u8>, Outcome> {
    let Some(path) = reference.as_str().filter(|path| !path.is_empty()) else {
        return Err(outcome("UNKNOWN", "OCR_SOURCE_MAPPING_INCOMPLETE"));
    };
    reader
        .read(&document.resolve(path))
        .map_err(|reason| outcome("UNKNOWN", reason))
}

fn v2_record(
    document: &Document,
    record: &Value,
    resource: Option<&&Value>,
    mode: &str,
    reader: &mut Reader,
) -> Result<String, Outcome> {
    static HEADER: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(concat!(
            r"(?-u)^<!-- mdocr:ocr-begin v=2 id=[a-zA-Z0-9-]+ -->\n",
            r"<!-- mdocr:meta mode=(paddle|llm) sha256=([a-f0-9]{64}) -->\n",
            r"> Original image: \[source\]\(<[^<>\r\n]+>\)\n\n",
        ))
        .unwrap()
    });
    static NESTED: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i-u)<!--\s*mdocr:").unwrap());
    let original = resource.map_or(&Value::Null, |entry| &entry["path"]);
    if [&record["source_path"], original, &record["artifact"]]
        .iter()
        .any(|value| value.as_str().is_none_or(str::is_empty))
    {
        return Err(outcome("UNKNOWN", "OCR_SOURCE_MAPPING_INCOMPLETE"));
    }
    let source = data(document, &record["source_path"], reader)?;
    let digest = format!("{:x}", Sha256::digest(&source));
    if record["source_sha256"] != digest || source != data(document, original, reader)? {
        return Err(outcome("UNKNOWN", "OCR_SOURCE_MAPPING_MISMATCH"));
    }
    let block = data(document, &record["artifact"], reader)?;
    if record["artifact_sha256"] != format!("{:x}", Sha256::digest(&block)) {
        return Err(outcome("CHANGED", "OCR_ARTIFACT_CHANGED"));
    }
    let invalid = outcome("UNKNOWN", "OCR_ARTIFACT_MAPPING_INVALID");
    let header = HEADER.captures(&block).ok_or(invalid)?;
    let body = block
        .strip_suffix(b"\n<!-- mdocr:ocr-end -->")
        .and_then(|body| body.get(header.get(0).unwrap().end()..))
        .ok_or(invalid)?;
    if &header[1] != mode.as_bytes()
        || &header[2] != digest.as_bytes()
        || body
            .iter()
            .all(|ch| ch.is_ascii_whitespace() || *ch == 0x0b)
        || NESTED.is_match(body)
    {
        return Err(invalid);
    }
    Ok(digest)
}

fn digest_valid(input: &Value) -> bool {
    input.as_str().is_some_and(|value| {
        value.len() == 64
            && value
                .bytes()
                .all(|byte| matches!(byte, b'a'..=b'f' | b'0'..=b'9'))
    })
}

fn verify(
    old: &Document,
    new: &Document,
    id: &str,
    inputs: &ReuseInputs<'_>,
    reader: &mut Reader,
) -> Outcome {
    let incomplete = outcome("UNKNOWN", "OCR_PROVENANCE_INCOMPLETE");
    let Some((old_records, new_records)) =
        inputs.old_records.as_ref().zip(inputs.new_records.as_ref())
    else {
        return incomplete;
    };
    let Some(previous) = old_records.get(id) else {
        return outcome("CHANGED", "OCR_SUCCESS_RECORD_MISSING");
    };
    match previous["status"].as_str() {
        Some("failed" | "pending") => return outcome("CHANGED", "OCR_SUCCESS_RECORD_UNSUCCESSFUL"),
        Some("success") => (),
        _ => return incomplete,
    }
    let Some(desired) = new_records.get(id) else {
        return incomplete;
    };

    let before = &old.raw["ocr"]["configuration"];
    let after = &new.raw["ocr"]["configuration"];
    let unverified = outcome("UNKNOWN", "OCR_CONFIGURATION_UNVERIFIED");
    if [before, after]
        .iter()
        .any(|settings| settings.as_object().is_none_or(|values| values.is_empty()))
    {
        return unverified;
    }
    if before != after {
        return outcome("CHANGED", "OCR_CONFIGURATION_INCOMPATIBLE");
    }
    let Some(mode @ ("paddle" | "llm")) = before["mode"].as_str() else {
        return unverified;
    };
    if mode == "llm" {
        let before = &old.raw["ocr"]["context_sha256"];
        let after = &new.raw["ocr"]["context_sha256"];
        if !digest_valid(before) || !digest_valid(after) {
            return outcome("UNKNOWN", "OCR_CONTEXT_UNVERIFIED");
        }
        if before != after {
            return outcome("CHANGED", "OCR_CONTEXT_CHANGED");
        }
    }
    let verified =
        v2_record(old, previous, inputs.old_resources.get(id), mode, reader).and_then(|left| {
            v2_record(new, desired, inputs.new_resources.get(id), mode, reader)
                .map(|right| left == right)
        });
    match verified {
        Ok(true) => outcome("UNCHANGED", "OCR_REUSE_VERIFIED"),
        Ok(false) => outcome("CHANGED", "OCR_SOURCE_MAPPING_MISMATCH"),
        Err(outcome) => outcome,
    }
}

fn removed_contributions<'a>(old: Option<&'a Document>, new: &Document) -> Vec<&'a str> {
    let Some(records) = old.and_then(|doc| records(doc).ok().flatten()) else {
        return Vec::new();
    };
    let incoming: BTreeSet<_> = entries(new)
        .filter_map(|entry| entry["resource_id"].as_str())
        .collect();
    records
        .into_iter()
        .filter(|(id, entry)| entry["status"] == "success" && !incoming.contains(id))
        .map(|(id, _)| id)
        .collect()
}

pub(crate) fn compare(
    old: Option<&Document>,
    new: &Document,
    resources: &Value,
    reader: &mut Reader,
) -> Value {
    let mut items = Vec::new();
    let mut inputs = None;
    let mut reasons = BTreeSet::new();
    let mut reusable = Vec::new();
    let mut processing = Vec::new();
    let mut unknown = resources["status"] == "UNKNOWN";
    if unknown {
        reasons.insert("OCR_RESOURCE_UNVERIFIED");
    }

    for resource in resources["items"].as_array().into_iter().flatten() {
        if resource["kind"] != "image" || resource["operation"] == "removed" {
            continue;
        }
        let id = resource["resource_id"].as_str().unwrap();
        let (status, reason) = match (resource["status"].as_str(), old) {
            (Some("NEW" | "CHANGED"), _) | (_, None) => outcome("NEW", "OCR_PROCESS_RESOURCE"),
            (Some("UNKNOWN"), _) => outcome("UNKNOWN", "OCR_RESOURCE_UNVERIFIED"),
            (_, Some(old)) if old.raw["ocr"].is_null() => {
                outcome("CHANGED", "OCR_SUCCESS_RECORD_MISSING")
            }
            (_, Some(old)) => match inputs.get_or_insert_with(|| reuse_inputs(old, new)) {
                Ok(inputs) => verify(old, new, id, inputs, reader),
                Err(reason) => outcome("UNKNOWN", reason),
            },
        };
        match status {
            "NEW" | "CHANGED" => processing.push(id),
            "UNKNOWN" => unknown = true,
            "UNCHANGED" => reusable.push(id),
            _ => unreachable!(),
        }
        reasons.insert(reason);
        items.push(json!({"resource_id":id, "status":status, "reason_codes":[reason]}));
    }

    let status = if unknown {
        "UNKNOWN"
    } else if !processing.is_empty() {
        "CHANGED"
    } else if items.is_empty() {
        "NOT_APPLICABLE"
    } else {
        "UNCHANGED"
    };
    json!({
        "status": status,
        "reason_codes": reasons,
        "items": items,
        "reusable": reusable,
        "processing_required": processing,
        "removed_contributions": removed_contributions(old,new),
    })
}
