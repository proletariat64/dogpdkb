use crate::input::{Document, Reader};
use crate::resources::{decoded, entries, split_url, uses};
use crate::{offline::Snapshots, summary::Progress};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

fn json_path(path: &Path) -> Result<Value> {
    serde_json::to_value(path).context("invalid_input: catalog path is not valid UTF-8")
}

fn absolute(doc: &Document, reader: &Reader, entry: &mut Value, key: &str) -> Result<()> {
    if let Some(path) = entry[key].as_str().filter(|path| !path.is_empty()) {
        entry[key] = json_path(&reader.local_file(&doc.resolve(path))?.0)?;
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

enum Candidate {
    Local(PathBuf),
    Remote,
    SyntaxUnverified,
}

fn by_reference(declared: &[Value]) -> BTreeMap<(&str, &str), &Value> {
    let mut references = BTreeMap::new();
    for entry in declared {
        if let Some(key) = entry["url"].as_str().zip(entry["kind"].as_str()) {
            references.entry(key).or_insert(entry);
        }
    }
    references
}

fn candidate_path(doc: &Document, entry: Option<&Value>, url: &str) -> Result<Candidate> {
    let cleaned = url.replace(['\r', '\n', '\t'], "");
    let Some(target) = split_url(&cleaned) else {
        return Ok(Candidate::SyntaxUnverified);
    };
    if let Some(path) = entry.and_then(|entry| entry["path"].as_str()) {
        return Ok(Candidate::Local(doc.resolve(path)));
    }
    if !target.scheme.is_empty() || !target.host.is_empty() {
        return Ok(Candidate::Remote);
    }
    let parent = doc.path.parent().context("INPUT_PATH_INVALID")?;
    Ok(Candidate::Local(parent.join(decoded(target.path))))
}

pub(crate) fn local_candidates(doc: &Document, bytes: &[u8]) -> Result<Vec<PathBuf>> {
    let (declared, _) = declarations(doc)?;
    let by_reference = by_reference(&declared);
    let (references, _) = uses(bytes, &by_reference.keys().map(|(url, _)| *url).collect());
    let mut paths = Vec::new();
    for usage in references {
        let entry = by_reference.get(&(usage.url.as_str(), usage.kind)).copied();
        if let Candidate::Local(path) = candidate_path(doc, entry, &usage.url)? {
            paths.push(path);
        }
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
                absolute(doc, reader, image, "source_path")?;
                absolute(doc, reader, image, "artifact")?;
            }
        }
    }

    let (declared, by_id) = declarations(doc)?;
    let by_reference = by_reference(&declared);
    let declared_urls = by_reference.keys().map(|(url, _)| *url).collect();
    let (used, mut warnings) = uses(data, &declared_urls);
    let mut last_kind = BTreeMap::new();
    let mixed_urls: BTreeSet<_> = used
        .iter()
        .filter_map(|usage| {
            last_kind
                .insert(usage.url.as_str(), usage.kind)
                .filter(|previous| *previous != usage.kind)
                .map(|_| usage.url.as_str())
        })
        .collect();
    let historical = doc.content_kind() == "enriched";
    let mut resources = Vec::new();
    let mut unverified = Vec::new();
    let mut recorded_reasons = BTreeSet::new();
    let mut retained = BTreeMap::new();
    if historical {
        for value in &declared {
            let mut value = value.clone();
            absolute(doc, reader, &mut value, "path")?;
            retained.insert(
                value["resource_id"].as_str().unwrap().to_owned(),
                resources.len(),
            );
            resources.push(value);
        }
    }
    for usage in &used {
        let declaration = by_reference.get(&(usage.url.as_str(), usage.kind)).copied();
        let mut entry = declaration.cloned().unwrap_or_else(|| json!({}));
        let id = match entry["resource_id"].as_str() {
            Some(id) => id.to_owned(),
            None if mixed_urls.contains(usage.url.as_str())
                || by_id.get(usage.url.as_str()).is_some_and(|&index| {
                    declared[index]["url"] == usage.url && declared[index]["kind"] != usage.kind
                }) =>
            {
                format!("{}:{}", usage.kind, usage.url)
            }
            None => usage.url.clone(),
        };
        ensure!(
            by_id.get(id.as_str()).is_none_or(|&index| {
                declared[index]["url"] == usage.url && declared[index]["kind"] == usage.kind
            }) && retained.get(&id).is_none_or(|&index| {
                resources[index]["url"] == usage.url && resources[index]["kind"] == usage.kind
            }),
            "RESOURCE_ID_COLLISION"
        );
        entry["resource_id"] = json!(id);
        entry["url"] = json!(usage.url);
        entry["kind"] = json!(usage.kind);
        let local = candidate_path(doc, declaration, &usage.url)?;
        let reason = match local {
            Candidate::Local(path) => {
                let (path, exists) = reader.local_file(&path)?;
                if exists || entry["path"].is_string() {
                    entry["path"] = json_path(&path)?;
                }
                (!exists).then_some("LOCAL_RESOURCE_UNAVAILABLE")
            }
            Candidate::Remote
                if entry["url"]
                    .as_str()
                    .is_some_and(|url| url.starts_with("data:")) =>
            {
                Some("EMBEDDED_BYTES_UNVERIFIED")
            }
            Candidate::SyntaxUnverified => {
                if !warnings.contains(&"RESOURCE_SYNTAX_UNVERIFIED") {
                    warnings.push("RESOURCE_SYNTAX_UNVERIFIED");
                }
                entry
                    .as_object_mut()
                    .expect("resource object")
                    .remove("path");
                Some("RESOURCE_SYNTAX_UNVERIFIED")
            }
            Candidate::Remote => Some("REMOTE_RESOURCE_UNVERIFIED"),
        };
        if let Some(reason) = reason
            && recorded_reasons.insert((id.clone(), reason))
        {
            unverified.push(json!({"resource_id":id,"reason_code":reason}));
        }
        if let std::collections::btree_map::Entry::Vacant(occurrence) = retained.entry(id) {
            occurrence.insert(resources.len());
            resources.push(entry);
        }
    }
    row["path"] = json_path(&reader.bound_target(&doc.path)?)?;
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
    snapshots: &Snapshots,
    reader: &mut Reader,
    progress: &Progress,
) -> Result<Value> {
    let Snapshots {
        incoming: documents,
        order,
        ..
    } = snapshots;
    let mut rows = Vec::new();
    let mut unresolved = 0;
    for identity in order {
        let document = &documents[identity];
        reader.begin_document();
        let mut row = match reader.read(&document.path) {
            Ok(bytes) => catalog_document(document, &bytes, reader)?,
            Err(reason) => json!({
                "path": json_path(&document.path)?,
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
        progress.update("catalog", rows.len(), documents.len(), 0, 0);
    }
    Ok(json!({
        "schema_version":"mdcompare-catalog-result-1",
        "state":if unresolved == 0 {"complete"} else {"partial"},
        "results":rows,"baseline_only":[],
        "counts":{"total":rows.len(),"unresolved":unresolved},
    }))
}
