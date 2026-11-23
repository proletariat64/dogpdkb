use crate::input::{
    Document, Reader, check_output, json_bytes, load, publish as publish_outputs, referenced_paths,
    resolved,
};
use crate::resources::{decoded, entries, split_url, uses};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

fn absolute(doc: &Document, entry: &mut Value, key: &str) -> Result<()> {
    if let Some(path) = entry[key].as_str() {
        entry[key] = json!(resolved(&doc.resolve(path))?);
    }
    Ok(())
}

// Duplicate declared IDs keep their final declared value and first position,
// matching manifest-catalog behavior. The comparison independently validates
// uniqueness when reading its evidence manifest.
fn declarations(doc: &Document) -> (Vec<Value>, BTreeMap<&str, usize>) {
    let mut declared = Vec::new();
    let mut by_id = BTreeMap::new();
    for value in entries(doc) {
        if let Some(id) = value["resource_id"].as_str() {
            if let Some(&index) = by_id.get(id) {
                declared[index] = value.clone();
            } else {
                by_id.insert(id, declared.len());
                declared.push(value.clone());
            }
        }
    }
    (declared, by_id)
}

fn catalog_document(
    doc: &Document,
    reader: &mut Reader,
    protected: &mut Vec<PathBuf>,
) -> Result<Value> {
    let mut row = doc.raw.clone();
    ensure!(
        row.get("resources").is_none_or(Value::is_array),
        "RESOURCE_SCHEMA_INVALID"
    );
    ensure!(
        row["ocr"].is_null() || row["ocr"].is_object(),
        "PROVENANCE_SCHEMA_INVALID"
    );
    if let Some(images) = row
        .get_mut("ocr")
        .and_then(|v| v.get_mut("images"))
        .and_then(Value::as_array_mut)
    {
        for image in images {
            if image.is_object() {
                absolute(doc, image, "source_path")?;
                absolute(doc, image, "artifact")?;
            }
        }
    }

    let (declared, by_id) = declarations(doc);
    let mut by_url = BTreeMap::new();
    for entry in &declared {
        if let Some(url) = entry["url"].as_str() {
            by_url.entry(url).or_insert(entry);
        }
    }
    let data = reader.read(&doc.path).map_err(anyhow::Error::msg)?;
    let declared_urls = by_url.keys().copied().collect();
    let (used, warnings) = uses(&data, &declared_urls);
    let historical = doc.content_kind() == "historical_enriched";
    let mut resources = Vec::new();
    let mut retained = BTreeSet::new();
    if historical {
        for value in &declared {
            let mut value = value.clone();
            absolute(doc, &mut value, "path")?;
            retained.insert(value["resource_id"].as_str().unwrap().to_owned());
            resources.push(value);
        }
    }
    for usage in used {
        let mut entry = by_url
            .get(usage.url.as_str())
            .map_or_else(|| json!({}), |entry| (*entry).clone());
        let id = entry["resource_id"]
            .as_str()
            .unwrap_or(&usage.url)
            .to_owned();
        ensure!(
            by_id
                .get(id.as_str())
                .is_none_or(|&index| declared[index]["url"] == usage.url),
            "RESOURCE_ID_COLLISION"
        );
        entry["resource_id"] = json!(id);
        entry["url"] = json!(usage.url);
        entry["kind"] = json!(usage.kind);
        if entry["path"].is_string() {
            absolute(doc, &mut entry, "path")?;
        } else {
            let cleaned = usage.url.replace(['\r', '\n', '\t'], "");
            let target = split_url(&cleaned).context("RESOURCE_SYNTAX_UNVERIFIED")?;
            if target.scheme.is_empty() && target.host.is_empty() {
                let parent = doc.path.parent().context("INPUT_PATH_INVALID")?;
                let candidate = resolved(&parent.join(decoded(target.path)))?;
                if candidate.is_file() {
                    entry["path"] = json!(candidate);
                }
            }
        }
        if retained.insert(id) {
            resources.push(entry);
        }
    }
    protected.extend(referenced_paths(doc));
    protected.extend(
        resources
            .iter()
            .filter_map(|entry| entry["path"].as_str())
            .map(PathBuf::from),
    );
    row["path"] = json!(resolved(&doc.path)?);
    row["resources"] = json!(resources);
    row["resource_warnings"] = json!(warnings);
    Ok(row)
}

pub(crate) fn publish(input: &Path, output: &Path) -> Result<()> {
    let mut reader = Reader::new(16 * 1024 * 1024);
    let documents = load(input, &mut reader, true)?;
    let mut protected = vec![input.to_path_buf()];
    let mut rows = Vec::new();
    for document in documents.values() {
        rows.push(catalog_document(document, &mut reader, &mut protected)?);
    }
    check_output(
        output,
        protected.iter().chain(reader.paths()).map(PathBuf::as_path),
    )?;
    let parent = output
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    std::fs::create_dir_all(parent)?;
    let report = json!({"schema_version":"1.0","documents":rows});
    publish_outputs(&[(output.into(), json_bytes(&report)?)])
}
