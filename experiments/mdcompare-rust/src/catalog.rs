use crate::input::{Document, Identity, Reader, resolved};
use crate::resources::{decoded, entries, split_url, uses};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

fn absolute(doc: &Document, entry: &mut Value, key: &str) -> Result<()> {
    if let Some(path) = entry[key].as_str() {
        entry[key] = json!(resolved(&doc.resolve(path))?);
    }
    Ok(())
}

fn declarations(doc: &Document) -> Result<(Vec<Value>, BTreeMap<&str, usize>)> {
    let mut declared = Vec::new();
    let mut by_id = BTreeMap::new();
    for value in entries(doc) {
        let id = value["resource_id"]
            .as_str()
            .filter(|id| !id.is_empty())
            .context("RESOURCE_SCHEMA_INVALID")?;
        ensure!(
            by_id.insert(id, declared.len()).is_none(),
            "RESOURCE_ID_COLLISION"
        );
        declared.push(value.clone());
    }
    Ok((declared, by_id))
}

fn candidate(doc: &Document, url: &str) -> Result<Option<PathBuf>> {
    let cleaned = url.replace(['\r', '\n', '\t'], "");
    let target = split_url(&cleaned).context("RESOURCE_SYNTAX_UNVERIFIED")?;
    if !target.scheme.is_empty() || !target.host.is_empty() {
        return Ok(None);
    }
    let parent = doc.path.parent().context("INPUT_PATH_INVALID")?;
    Ok(Some(parent.join(decoded(target.path))))
}

fn urls(declared: &[Value]) -> BTreeMap<&str, &Value> {
    let mut by_url = BTreeMap::new();
    for entry in declared {
        if let Some(url) = entry["url"].as_str() {
            by_url.entry(url).or_insert(entry);
        }
    }
    by_url
}

pub(crate) fn local_candidates(doc: &Document, bytes: &[u8]) -> Result<Vec<PathBuf>> {
    let (declared, _) = declarations(doc)?;
    let by_url = urls(&declared);
    let (references, _) = uses(bytes, &by_url.keys().copied().collect());
    let mut paths = Vec::new();
    for usage in references {
        let path = match by_url
            .get(usage.url.as_str())
            .and_then(|entry| entry["path"].as_str())
        {
            Some(path) => Some(doc.resolve(path)),
            None => candidate(doc, &usage.url)?,
        };
        paths.extend(path);
    }
    Ok(paths)
}

fn catalog_document(doc: &Document, data: &[u8], reader: &mut Reader) -> Result<Value> {
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

    let (declared, by_id) = declarations(doc)?;
    let by_url = urls(&declared);
    let declared_urls = by_url.keys().copied().collect();
    let (used, warnings) = uses(data, &declared_urls);
    let historical = doc.content_kind() == "enriched";
    let mut resources = Vec::new();
    let mut unverified = Vec::new();
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
        let declared_path = entry["path"].as_str().map(|path| doc.resolve(path));
        let local = match &declared_path {
            Some(path) => Some(path.clone()),
            None => candidate(doc, &usage.url)?,
        };
        let reason = match local {
            Some(path) => {
                let (path, exists) = reader.local_file(&path)?;
                if exists || declared_path.is_some() {
                    entry["path"] = json!(path);
                }
                (!exists).then_some("LOCAL_RESOURCE_UNAVAILABLE")
            }
            None if entry["url"]
                .as_str()
                .is_some_and(|url| url.starts_with("data:")) =>
            {
                Some("EMBEDDED_BYTES_UNVERIFIED")
            }
            None => Some("REMOTE_RESOURCE_UNVERIFIED"),
        };
        if let Some(reason) = reason {
            unverified.push(json!({"resource_id":id,"reason_code":reason}));
        }
        if retained.insert(id) {
            resources.push(entry);
        }
    }
    row["path"] = json!(resolved(&doc.path)?);
    row["resources"] = json!(resources);
    row["resource_warnings"] = json!(warnings);
    row["unverified_resources"] = json!(unverified);
    row["status"] = json!(if warnings.is_empty() {
        "COMPLETE"
    } else {
        "UNKNOWN"
    });
    Ok(row)
}

pub(crate) fn catalog_documents(
    documents: &BTreeMap<Identity, Document>,
    order: &[Identity],
    reader: &mut Reader,
) -> Result<Value> {
    let mut rows = Vec::new();
    let mut unresolved = 0;
    for identity in order {
        let document = &documents[identity];
        reader.begin_document();
        let mut row = match reader.read(&document.path) {
            Ok(bytes) => catalog_document(document, &bytes, reader)?,
            Err(reason) => json!({
                "path": document.path,
                "resources": [],
                "resource_warnings": [reason],
                "status": "UNKNOWN",
            }),
        };
        row["knowledge_base"] = json!(identity.knowledge_base);
        row["document_id"] = json!(identity.document_id);
        unresolved += usize::from(
            row["resource_warnings"]
                .as_array()
                .is_some_and(|items| !items.is_empty()),
        );
        rows.push(row);
    }
    Ok(json!({
        "schema_version":"mdcompare-catalog-result-1",
        "state":if unresolved == 0 {"complete"} else {"partial"},
        "results":rows,"baseline_only":[],
        "counts":{"total":rows.len(),"unresolved":unresolved},
    }))
}
