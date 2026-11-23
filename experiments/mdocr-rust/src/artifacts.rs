use anyhow::{Context, Result, ensure};
use percent_encoding::percent_decode_str;
use regex::Regex;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::fs;
use std::io::{ErrorKind, Write};
use std::ops::Range;
use std::path::{Component, Path, PathBuf};
use std::sync::LazyLock;

pub(crate) struct Plan {
    pub digest: String,
    pub settings: Value,
    pub documents: Vec<Document>,
    pub protected: HashSet<PathBuf>,
}

pub(crate) struct Document {
    pub document_id: String,
    pub path: PathBuf,
    pub output: PathBuf,
    pub sha256: String,
    pub text: String,
    pub images: Vec<Image>,
}

pub(crate) struct Image {
    pub id: String,
    pub resource_id: String,
    pub original_link: String,
    pub source_path: Option<PathBuf>,
    pub sha256: Option<String>,
    pub error: Option<String>,
    pub selected: bool,
    span: Range<usize>,
}

static IMAGE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"\A!\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))(?:\s+(?:"[^\n]*?"|'[^\n]*?'))?\)\z"#)
        .expect("valid image pattern")
});
static SCHEME: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[A-Za-z][A-Za-z0-9+.-]*:").expect("valid URI pattern"));
static RESERVED: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)<!--\s*mdocr:").expect("valid OCR delimiter pattern"));

pub(crate) fn sha(data: &[u8]) -> String {
    format!("{:x}", Sha256::digest(data))
}

pub(crate) fn json_bytes(value: &impl serde::Serialize) -> Result<Vec<u8>> {
    // The public witness serialization uses sorted keys even when a dependency
    // enables serde_json's insertion-order map feature.
    let mut value = serde_json::to_value(value)?;
    value.sort_all_objects();
    let mut data = serde_json::to_vec_pretty(&value)?;
    data.push(b'\n');
    Ok(data)
}

pub(crate) fn resolve(path: &Path) -> Result<PathBuf> {
    let mut resolved = PathBuf::new();
    for part in std::path::absolute(path)?.components() {
        match part {
            Component::Prefix(_) | Component::RootDir => resolved.push(part.as_os_str()),
            Component::CurDir => {}
            Component::ParentDir => {
                resolved.pop();
            }
            Component::Normal(name) => {
                resolved.push(name);
                match fs::symlink_metadata(&resolved) {
                    Ok(_) => {
                        resolved = resolved
                            .canonicalize()
                            .with_context(|| format!("resolve {}", resolved.display()))?;
                    }
                    Err(err) if err.kind() == ErrorKind::NotFound => {}
                    Err(err) => {
                        return Err(err).with_context(|| format!("resolve {}", resolved.display()));
                    }
                }
            }
        }
    }
    Ok(resolved)
}

pub(crate) fn owned_path(path: &Path) -> Result<()> {
    ensure!(
        resolve(path)? == path,
        "execution path changed: {}",
        path.display()
    );
    Ok(())
}

pub(crate) fn read_bound(path: &Path, digest: &str, reason: &str) -> Result<Vec<u8>> {
    checked_path(path, false)?;
    let bytes = fs::read(path).with_context(|| format!("read {}", path.display()))?;
    ensure!(sha(&bytes) == digest, "{reason}: {}", path.display());
    Ok(bytes)
}

pub(crate) fn atomic(path: &Path, data: &[u8], exclusive: bool) -> Result<()> {
    owned_path(path)?;
    let parent = path.parent().context("execution path has no parent")?;
    fs::create_dir_all(parent)?;
    let mut tmp = tempfile::NamedTempFile::new_in(parent)?;
    tmp.write_all(data)?;
    tmp.as_file().sync_all()?;
    if exclusive {
        tmp.persist_noclobber(path).map_err(|e| e.error)?;
    } else {
        tmp.persist(path).map_err(|e| e.error)?;
    }
    #[cfg(unix)]
    fs::File::open(parent)?.sync_all()?;
    Ok(())
}

pub(crate) fn verify_immutable(path: &Path, data: &[u8]) -> Result<()> {
    owned_path(path)?;
    match fs::read(path) {
        Ok(existing) => ensure!(
            existing == data,
            "OCR artifact has drifted: {}",
            path.display()
        ),
        Err(error) if error.kind() == ErrorKind::NotFound => atomic(path, data, true)?,
        Err(error) => return Err(error).with_context(|| format!("read {}", path.display())),
    }
    Ok(())
}

fn field<'a>(object: &'a Value, key: &str) -> Result<&'a Value> {
    object
        .get(key)
        .with_context(|| format!("plan is missing {key}"))
}

fn string<'a>(object: &'a Value, key: &str) -> Result<&'a str> {
    field(object, key)?
        .as_str()
        .with_context(|| format!("invalid plan {key}"))
}

fn optional_string<'a>(object: &'a Value, key: &str) -> Result<Option<&'a str>> {
    let value = field(object, key)?;
    if value.is_null() {
        Ok(None)
    } else {
        Ok(Some(
            value
                .as_str()
                .with_context(|| format!("invalid plan {key}"))?,
        ))
    }
}

fn absolute(value: &str, kind: &str) -> Result<PathBuf> {
    let path = Path::new(value);
    ensure!(path.is_absolute(), "plan {kind} must be an absolute path");
    owned_path(path)?;
    Ok(path.to_path_buf())
}

fn integer(object: &Value, key: &str) -> Result<u64> {
    field(object, key)?
        .as_u64()
        .with_context(|| format!("invalid plan {key}"))
}

fn validate_settings(settings: &Value) -> Result<Option<PathBuf>> {
    let mode = string(settings, "mode")?;
    let llm = mode == "llm";
    ensure!(llm || mode == "paddle", "invalid plan mode");
    let concurrency = integer(settings, "concurrency")?;
    ensure!((1..=100).contains(&concurrency), "invalid plan concurrency");
    if settings.get("fix").is_some() {
        integer(settings, "fix")?;
    }
    if settings.get("mermaid_repair_rounds").is_some() {
        ensure!(
            integer(settings, "mermaid_repair_rounds")? == 0 || llm,
            "invalid Mermaid repair mode"
        );
    }
    for key in ["request_budget", "token_budget"] {
        if !field(settings, key)?.is_null() {
            integer(settings, key)?;
            ensure!(
                key != "token_budget" || llm,
                "Paddle does not support a token budget"
            );
        }
    }
    let deadline = field(settings, "max_seconds")?
        .as_f64()
        .context("invalid plan deadline")?;
    ensure!(
        deadline.is_finite() && deadline >= 0.0,
        "invalid plan deadline"
    );
    string(settings, "prompt")?;
    let endpoint = optional_string(settings, "endpoint")?;
    if llm {
        ensure!(
            string(settings, "llm_schema")? == "mdocr-image-content-1",
            "unsupported LLM plan schema"
        );
        ensure!(!string(settings, "model")?.is_empty(), "invalid LLM model");
        ensure!(
            matches!(
                string(settings, "effort")?,
                "low" | "medium" | "high" | "xhigh"
            ),
            "invalid LLM effort"
        );
        ensure!(endpoint.is_none(), "invalid endpoint for LLM plan");
    }
    optional_string(settings, "cache_dir")?
        .map(|path| absolute(path, "cache directory"))
        .transpose()
}

fn image_target(text: &str) -> Option<String> {
    let capture = IMAGE.captures(text)?;
    let raw = capture.get(1).or_else(|| capture.get(2))?.as_str();
    let raw = raw.split_once('#').map_or(raw, |(before, _)| before);
    if raw.starts_with('/') || SCHEME.is_match(raw.trim_start_matches(|c: char| c <= ' ')) {
        Some(raw.into())
    } else {
        Some(percent_decode_str(raw).decode_utf8_lossy().into_owned())
    }
}

fn image_value(value: &Value, text: &str, chars: &[usize], last: usize) -> Result<Image> {
    let id = string(value, "id")?;
    ensure!(
        !id.is_empty()
            && id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-'),
        "invalid planned image ID"
    );
    let start = chars
        .get(usize::try_from(integer(value, "start")?)?)
        .context("invalid image start")?;
    let end = chars
        .get(usize::try_from(integer(value, "end")?)?)
        .context("invalid image end")?;
    ensure!(
        last <= *start && start < end,
        "invalid or overlapping image coordinates"
    );
    let span = *start..*end;
    let resource_id = string(value, "resource_id")?;
    ensure!(
        image_target(&text[span.clone()]).as_deref() == Some(resource_id),
        "image occurrence no longer agrees with plan"
    );
    let selected = field(value, "selected")?
        .as_bool()
        .context("invalid image selection")?;
    let error = optional_string(value, "error")?;
    ensure!(error != Some(""), "invalid empty image error");
    let source_path = optional_string(value, "source_path")?
        .map(|path| absolute(path, "image source"))
        .transpose()?;
    let digest = optional_string(value, "sha256")?;
    let original_link = string(value, "original_link")?;
    if selected && error.is_none() {
        ensure!(
            Path::new(original_link).is_absolute()
                || original_link
                    .split_once("://")
                    .is_some_and(|(scheme, _)| scheme.eq_ignore_ascii_case("http")
                        || scheme.eq_ignore_ascii_case("https")),
            "invalid original image destination"
        );
        read_bound(
            source_path
                .as_deref()
                .context("selected image has no source path")?,
            digest.context("selected image has no digest")?,
            "IMAGE_DRIFT",
        )?;
    }
    Ok(Image {
        id: id.into(),
        resource_id: resource_id.into(),
        original_link: original_link.into(),
        selected,
        source_path,
        sha256: digest.map(str::to_owned),
        error: error.map(str::to_owned),
        span,
    })
}

fn checked_path(path: &Path, directory: bool) -> Result<()> {
    owned_path(path)?;
    match fs::metadata(path) {
        Ok(metadata) => ensure!(
            if directory {
                metadata.is_dir()
            } else {
                metadata.is_file()
            },
            "unexpected execution path type: {}",
            path.display()
        ),
        Err(error) if error.kind() == ErrorKind::NotFound => {}
        Err(error) => return Err(error).with_context(|| format!("inspect {}", path.display())),
    }
    Ok(())
}

pub(crate) fn load_plan(
    plan_path: &Path,
    result_path: &Path,
    journal: &Path,
    evidence: &Path,
    locks: &[PathBuf],
) -> Result<Plan> {
    let mut controls = HashSet::new();
    for path in [plan_path, result_path, journal]
        .into_iter()
        .chain(locks.iter().map(PathBuf::as_path))
    {
        checked_path(path, false)?;
        ensure!(
            controls.insert(path.to_path_buf()),
            "overlapping execution artifacts"
        );
    }
    checked_path(evidence, true)?;
    let bytes = fs::read(plan_path).with_context(|| format!("read {}", plan_path.display()))?;
    let plan: Value = serde_json::from_slice(&bytes).context("invalid plan JSON")?;
    ensure!(
        plan["schema_version"] == "mdocr-plan-2",
        "unsupported or invalid mdocr plan"
    );
    let settings = field(&plan, "settings")?;
    let cache = validate_settings(settings)?;
    if let Some(cache) = &cache {
        checked_path(cache, true)?;
    }
    let mut protected = HashSet::new();
    if let Some(inputs) = plan.get("protected_inputs") {
        for input in inputs.as_array().context("invalid protected input list")? {
            protected.insert(absolute(
                input.as_str().context("invalid protected input")?,
                "protected input",
            )?);
        }
    }
    let mut sources = HashSet::new();
    let mut outputs = HashSet::new();
    let mut documents = Vec::new();
    let doc_values = field(&plan, "documents")?
        .as_array()
        .context("invalid plan documents")?;
    for (position, doc) in doc_values.iter().enumerate() {
        let path = absolute(string(doc, "path")?, "source")?;
        let output = absolute(string(doc, "output")?, "output")?;
        checked_path(&output, false)?;
        let document_id = string(doc, "document_id")?;
        ensure!(
            path.to_str() == Some(document_id)
                && sources.insert(path.clone())
                && outputs.insert(output.clone()),
            "duplicate or invalid planned input/output identity"
        );
        protected.insert(path.clone());
        let digest = string(doc, "sha256")?;
        let text = String::from_utf8(read_bound(&path, digest, "SOURCE_DRIFT")?)?;
        let offsets = text
            .char_indices()
            .map(|(offset, _)| offset)
            .chain([text.len()])
            .collect::<Vec<_>>();
        let mut images = Vec::new();
        let mut ids = HashSet::new();
        let mut last = 0;
        let item_dir = evidence.join(position.to_string());
        checked_path(&item_dir, true)?;
        for value in field(doc, "images")?
            .as_array()
            .context("invalid plan image list")?
        {
            let image = image_value(value, &text, &offsets, last)?;
            ensure!(ids.insert(image.id.clone()), "duplicate planned image ID");
            last = image.span.end;
            if let Some(path) = &image.source_path {
                protected.insert(path.clone());
            }
            checked_path(&item_dir.join(format!("{}.md", image.id)), false)?;
            images.push(image);
        }
        documents.push(Document {
            document_id: document_id.into(),
            path,
            output,
            sha256: digest.into(),
            text,
            images,
        });
    }
    for path in controls.iter().chain(&outputs) {
        ensure!(
            !protected.contains(path),
            "output, result, plan, or lock overlaps an input: {}",
            path.display()
        );
        ensure!(
            !(controls.contains(path) && outputs.contains(path)),
            "output overlaps an execution artifact: {}",
            path.display()
        );
        ensure!(
            !path
                .ancestors()
                .skip(1)
                .any(|parent| controls.contains(parent)
                    || outputs.contains(parent)
                    || protected.contains(parent)),
            "execution output is nested under a planned file: {}",
            path.display()
        );
    }
    for path in protected.iter().chain(&outputs).chain(&controls) {
        ensure!(
            !path.starts_with(evidence) && !evidence.starts_with(path),
            "planned path overlaps the execution evidence directory"
        );
        if let Some(cache) = &cache {
            ensure!(
                !path.starts_with(cache) && !cache.starts_with(path),
                "planned path overlaps the OCR cache"
            );
        }
    }
    if let Some(cache) = &cache {
        ensure!(
            !cache.starts_with(evidence) && !evidence.starts_with(cache),
            "OCR cache overlaps the execution evidence directory"
        );
    }
    Ok(Plan {
        digest: sha(&bytes),
        settings: settings.clone(),
        documents,
        protected,
    })
}

pub(crate) fn validate_patch(patch: &Value) -> Result<(), &'static str> {
    let markdown = patch["markdown"].as_str().ok_or("OCR_RESPONSE_INVALID")?;
    let uncertainty = patch["uncertainty"]
        .as_str()
        .ok_or("OCR_RESPONSE_INVALID")?;
    if RESERVED.is_match(markdown) || RESERVED.is_match(uncertainty) {
        return Err("OCR_DELIMITER_RESERVED");
    }
    if patch
        .get("warnings")
        .is_some_and(|warnings| *warnings != json!(["response_mermaid_syntax_invalid"]))
    {
        return Err("OCR_RESPONSE_INVALID");
    }
    if patch["kind"] == "hold" {
        if !markdown.is_empty() || uncertainty.trim().is_empty() {
            return Err("OCR_RESPONSE_INVALID");
        }
    } else if markdown.trim().is_empty() {
        return Err("OCR_EMPTY");
    }
    Ok(())
}

fn block(image: &Image, mode: &str, patch: &Value) -> Result<String> {
    validate_patch(patch).map_err(anyhow::Error::msg)?;
    ensure!(patch["kind"] != "hold", "cannot publish a held image");
    let digest = image
        .sha256
        .as_deref()
        .context("cannot publish image without digest")?;
    let link = quote(&image.original_link);
    let id = &image.id;
    let markdown = patch["markdown"]
        .as_str()
        .context("invalid OCR Markdown")?
        .trim();
    let uncertainty = patch["uncertainty"]
        .as_str()
        .context("invalid OCR uncertainty")?
        .trim();
    let mut body = markdown.to_owned();
    if !uncertainty.is_empty() {
        body.push('\n');
        for line in uncertainty.replace("\r\n", "\n").split([
            '\r', '\n', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}',
            '\u{2029}',
        ]) {
            body.push_str("\n> ");
            body.push_str(line);
        }
    }
    Ok(format!(
        "<!-- mdocr:ocr-begin v=2 id={id} -->\n<!-- mdocr:meta mode={mode} sha256={digest} -->\n> Original image: [source](<{link}>)\n\n{body}\n<!-- mdocr:ocr-end -->"
    ))
}

fn quote(value: &str) -> String {
    let mut quoted = String::new();
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || b"/:%?&=+@#;,.~!$'*-_".contains(&byte) {
            quoted.push(char::from(byte));
        } else {
            use std::fmt::Write as _;
            write!(quoted, "%{byte:02X}").expect("write to String");
        }
    }
    quoted
}

pub(crate) fn publish_document(
    document: &Document,
    evidence_dir: &Path,
    config: &Value,
    records: Vec<Value>,
    patches: &[Option<&Value>],
) -> Result<Value> {
    ensure!(
        document.images.len() == patches.len() && patches.len() == records.len(),
        "incorrect number of OCR occurrences at publication"
    );
    let mode = config["mode"]
        .as_str()
        .context("missing OCR configuration mode")?;
    let mut replacements = Vec::new();
    let mut evidence = Vec::new();
    let mut resources = HashSet::new();
    for ((image, patch), record) in document.images.iter().zip(patches).zip(&records) {
        let successful =
            image.selected && matches!(record["status"].as_str(), Some("success" | "reused"));
        ensure!(
            successful == patch.is_some(),
            "OCR occurrence has inconsistent patch state"
        );
        let Some(patch) = patch else { continue };
        let block = block(image, mode, patch)?;
        if resources.insert(&image.resource_id) {
            let artifact = evidence_dir.join(format!("{}.md", image.id));
            verify_immutable(&artifact, block.as_bytes())?;
            evidence.push(json!({
                "resource_id": image.resource_id,
                "status": "success",
                "source_path": image.source_path,
                "source_sha256": image.sha256,
                "artifact": artifact,
                "artifact_sha256": sha(block.as_bytes()),
            }));
        }
        replacements.push((image.span.clone(), block));
    }
    let mut row = json!({
        "document_id": document.document_id,
        "path": document.path,
        "status": "partial",
        "output": null,
        "resources": records,
    });
    if records.iter().all(|record| {
        matches!(
            record["status"].as_str(),
            Some("success" | "reused" | "unselected")
        )
    }) {
        let mut content = document.text.clone();
        for (span, block) in replacements.into_iter().rev() {
            content.replace_range(span, &format!("\n\n{block}\n\n"));
        }
        verify_immutable(&document.output, content.as_bytes())?;
        let mut ocr = json!({
            "contract": "mdocr-ocr-2",
            "configuration": config,
            "images": evidence,
        });
        if mode == "llm" && !evidence.is_empty() {
            let images = document
                .images
                .iter()
                .filter(|image| image.selected)
                .map(|image| json!([image.id, image.sha256]))
                .collect::<Vec<_>>();
            ocr["context_sha256"] = json!(sha(&json_bytes(&json!([document.sha256, images]))?));
        }
        row["status"] = json!("completed");
        row["output"] = json!(document.output);
        row["output_sha256"] = json!(sha(content.as_bytes()));
        row["ocr"] = ocr;
    }
    Ok(row)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn publication_preserves_utf8_and_binds_the_first_occurrence_of_each_image() -> Result<()> {
        let root = tempfile::tempdir()?;
        let root = resolve(root.path())?;
        let text = "资金 ![单图](img%281%29.png) 对账 ![重复](img%281%29.png) 完成\n";
        let source = root.join("in.md");
        let output = root.join("out.md");
        let image_path = root.join("img(1).png");
        let plan = root.join("plan.json");
        let result = root.join("result.json");
        let journal = root.join("plan.json.execution.json");
        let evidence = root.join("plan.json.evidence");
        let locks = [root.join("plan.json.lock"), root.join("result.json.lock")];
        fs::write(&source, text)?;
        fs::write(&image_path, b"exact image bytes")?;
        let image_sha = sha(b"exact image bytes");
        let source_sha = sha(text.as_bytes());
        let occurrences = ["![单图](img%281%29.png)", "![重复](img%281%29.png)"]
            .into_iter()
            .enumerate()
            .map(|(index, raw)| {
                let start = text[..text.find(raw).expect("known Markdown")].chars().count();
                json!({
                    "id": format!("image-{}", index + 1), "resource_id": "img(1).png",
                    "original_link": image_path, "source_path": image_path, "sha256": image_sha,
                    "start": start, "end": start + raw.chars().count(), "selected": true, "error": null,
                })
            })
            .collect::<Vec<_>>();
        let plan_data = json!({
            "schema_version": "mdocr-plan-2",
            "settings": {
                "mode": "llm", "model": "openai/test", "effort": "low", "llm_schema": "mdocr-image-content-1",
                "prompt": "Read the image", "concurrency": 3, "endpoint": null,
                "token_budget": null, "request_budget": null, "max_seconds": 900, "cache_dir": null,
            },
            "documents": [{
                "document_id": source, "path": source, "output": output,
                "sha256": source_sha, "images": occurrences,
            }],
        });
        fs::write(&plan, json_bytes(&plan_data)?)?;
        let frozen = load_plan(&plan, &result, &journal, &evidence, &locks)?;
        let document = &frozen.documents[0];
        let patch = json!({"markdown": "可见文字", "uncertainty": "字迹较淡\r\n复核第二行"});
        let records = vec![json!({"status": "success"}), json!({"status": "reused"})];
        let row = publish_document(
            document,
            &evidence.join("0"),
            &json!({"mode": "llm"}),
            records,
            &[Some(&patch), Some(&patch)],
        )?;
        let actual = fs::read_to_string(&output)?;
        assert_eq!(row["output_sha256"], sha(actual.as_bytes()));
        assert!(actual.starts_with("资金 \n\n<!-- mdocr:ocr-begin v=2 id=image-1 -->\n"));
        assert!(actual.contains(" 对账 \n\n<!-- mdocr:ocr-begin v=2 id=image-2 -->\n"));
        assert!(actual.contains("img%281%29.png>)\n\n可见文字\n\n> 字迹较淡\n> 复核第二行\n"));
        assert!(actual.ends_with(" 完成\n"));
        let image_evidence = row["ocr"]["images"].as_array().context("image evidence")?;
        assert_eq!(image_evidence.len(), 1);
        let first_block = fs::read(evidence.join("0/image-1.md"))?;
        assert_eq!(image_evidence[0]["artifact_sha256"], sha(&first_block));
        let canonical_context = format!(
            "[\n  \"{source_sha}\",\n  [\n    [\n      \"image-1\",\n      \"{image_sha}\"\n    ],\n    [\n      \"image-2\",\n      \"{image_sha}\"\n    ]\n  ]\n]\n"
        );
        assert_eq!(
            row["ocr"]["context_sha256"],
            sha(canonical_context.as_bytes())
        );
        fs::write(&image_path, b"drifted image bytes")?;
        assert!(
            load_plan(&plan, &result, &journal, &evidence, &locks)
                .err()
                .context("expected drift failure")?
                .to_string()
                .contains("IMAGE_DRIFT")
        );
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn output_and_evidence_writes_reject_symlink_destinations() -> Result<()> {
        let root = tempfile::tempdir()?;
        let root = resolve(root.path())?;
        let original = root.join("original.md");
        fs::write(&original, b"original bytes")?;
        let alias = root.join("alias.md");
        std::os::unix::fs::symlink(&original, &alias)?;
        assert!(verify_immutable(&alias, b"original bytes").is_err());
        assert!(atomic(&alias, b"replacement bytes", false).is_err());
        assert_eq!(fs::read(&original)?, b"original bytes");
        Ok(())
    }
}
