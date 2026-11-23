use anyhow::{Context, Result, bail, ensure};
use serde::de::{Deserialize, Deserializer, Error, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Value, json};
use std::collections::BTreeMap;
use std::fs::{self, File, Metadata, OpenOptions};
use std::io::Read;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Component, Path, PathBuf};

pub type Identity = (String, String);
pub const METADATA_KEYS: [&str; 3] = ["name", "logical_path", "source_ref"];

pub struct Document {
    pub raw: bool,
    pub path: PathBuf,
    pub metadata: Value,
    pub has_evidence: bool,
    pub evidence_paths: Vec<PathBuf>,
}

#[derive(PartialEq, Eq)]
pub struct Stamp(u64, u64, u64, i64, i64, i64, i64);

impl From<Metadata> for Stamp {
    fn from(v: Metadata) -> Self {
        Self(
            v.dev(),
            v.ino(),
            v.size(),
            v.mtime(),
            v.mtime_nsec(),
            v.ctime(),
            v.ctime_nsec(),
        )
    }
}

impl Stamp {
    pub fn current(&self, path: &Path) -> bool {
        fs::metadata(path).is_ok_and(|m| Self::from(m) == *self)
    }
}

pub fn read(path: &Path, limit: u64) -> Result<(Vec<u8>, Stamp), &'static str> {
    const READ_FAILED: &str = "INPUT_READ_FAILED";
    let metadata = fs::metadata(path).map_err(|_| READ_FAILED)?;
    if !metadata.is_file() {
        return Err("INPUT_NOT_REGULAR_FILE");
    }
    let before = Stamp::from(metadata);
    let mut options = OpenOptions::new();
    options.read(true).custom_flags(libc::O_NONBLOCK);
    let file = options.open(path).map_err(|_| READ_FAILED)?;
    let check = |file: &File| file.metadata().is_ok_and(|m| Stamp::from(m) == before);
    if !check(&file) {
        return Err("INPUT_CHANGED");
    }
    if before.2 > limit {
        return Err("FILE_BUDGET_EXCEEDED");
    }
    let mut data = Vec::new();
    (&file)
        .take(limit.saturating_add(1))
        .read_to_end(&mut data)
        .map_err(|_| READ_FAILED)?;
    if !check(&file) || !before.current(path) {
        return Err("INPUT_CHANGED");
    }
    if data.len() as u64 > limit {
        return Err("FILE_BUDGET_EXCEEDED");
    }
    Ok((data, before))
}

struct Unique(Value);
impl<'de> Deserialize<'de> for Unique {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct Json;
        impl<'de> Visitor<'de> for Json {
            type Value = Unique;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("JSON without repeated object keys")
            }
            fn visit_bool<E: Error>(self, v: bool) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_i64<E: Error>(self, v: i64) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_u64<E: Error>(self, v: u64) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_f64<E: Error>(self, v: f64) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_str<E: Error>(self, v: &str) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_unit<E: Error>(self) -> Result<Unique, E> {
                Ok(Unique(Value::Null))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut a: A) -> Result<Unique, A::Error> {
                let mut values = Vec::new();
                while let Some(Unique(v)) = a.next_element()? {
                    values.push(v);
                }
                Ok(Unique(Value::Array(values)))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut a: A) -> Result<Unique, A::Error> {
                let mut values = Map::new();
                while let Some((key, Unique(value))) = a.next_entry::<String, Unique>()? {
                    if values.insert(key, value).is_some() {
                        return Err(Error::custom("DUPLICATE_JSON_FIELD"));
                    }
                }
                Ok(Unique(Value::Object(values)))
            }
        }
        d.deserialize_any(Json)
    }
}

fn evidence_paths(v: &Value, root: &Path, paths: &mut Vec<PathBuf>) {
    match v {
        Value::Array(values) => {
            for v in values {
                evidence_paths(v, root, paths);
            }
        }
        Value::Object(map) => {
            for (key, v) in map {
                if let Some(v) = v.as_str() {
                    if matches!(
                        key.as_str(),
                        "path" | "file" | "artifact" | "prepared_image"
                    ) || key.ends_with("_path")
                        || key.ends_with("_file")
                    {
                        paths.push(root.join(v));
                    }
                } else {
                    evidence_paths(v, root, paths);
                }
            }
        }
        _ => {}
    }
}

pub fn manifest(path: &Path) -> Result<(BTreeMap<Identity, Document>, Stamp)> {
    let (bytes, stamp) = read(path, 16 * 1024 * 1024).map_err(anyhow::Error::msg)?;
    let Unique(value) = serde_json::from_slice(&bytes).map_err(|err| {
        anyhow::anyhow!(
            "{}",
            if err.to_string().starts_with("DUPLICATE_JSON_FIELD") {
                "DUPLICATE_JSON_FIELD"
            } else {
                "MANIFEST_UNREADABLE_OR_INVALID"
            }
        )
    })?;
    ensure!(value["schema_version"] == "1.0", "MANIFEST_SCHEMA_INVALID");
    let root = path.parent().context("MANIFEST_SCHEMA_INVALID")?;
    let mut documents = BTreeMap::new();
    for row in value["documents"]
        .as_array()
        .context("MANIFEST_SCHEMA_INVALID")?
    {
        let field = |k| {
            row[k]
                .as_str()
                .filter(|s| !s.is_empty())
                .context("DOCUMENT_SCHEMA_INVALID")
        };
        let id = (
            field("knowledge_base")?.to_owned(),
            field("document_id")?.to_owned(),
        );
        let path = root.join(field("path")?);
        let raw = match row["content_kind"].as_str() {
            Some("raw") => true,
            Some("historical_enriched") => false,
            _ => bail!("CONTENT_KIND_INVALID"),
        };
        let metadata = &row["metadata"];
        ensure!(
            METADATA_KEYS.iter().all(|&k| metadata[k].is_string())
                && metadata.get("sync_timestamp").is_none_or(Value::is_string),
            "METADATA_SCHEMA_INVALID"
        );
        let resources = &row["resources"];
        let items = resources
            .as_array()
            .filter(|a| a.iter().all(Value::is_object))
            .context("RESOURCE_SCHEMA_INVALID")?;
        let ocr = &row["ocr"];
        let provenance = row.get("provenance");
        ensure!(
            (ocr.is_null() || ocr.is_object()) && provenance.is_none_or(Value::is_object),
            "PROVENANCE_SCHEMA_INVALID"
        );
        let mut paths = Vec::new();
        for v in [resources, ocr].into_iter().chain(provenance) {
            evidence_paths(v, root, &mut paths);
        }
        let doc = Document {
            raw,
            path,
            metadata: metadata.clone(),
            evidence_paths: paths,
            has_evidence: !items.is_empty()
                || !ocr.is_null()
                || provenance
                    .and_then(Value::as_object)
                    .is_some_and(|o| !o.is_empty()),
        };
        ensure!(documents.insert(id, doc).is_none(), "DUPLICATE_DOCUMENT_ID");
    }
    Ok((documents, stamp))
}

fn resolved(path: &Path) -> Result<PathBuf> {
    let path = std::path::absolute(path)?;
    let mut output = PathBuf::new();
    for part in path.components() {
        match part {
            Component::ParentDir => {
                output.pop();
            }
            Component::CurDir => {}
            _ => {
                output.push(part);
                match fs::canonicalize(&output) {
                    Ok(path) => output = path,
                    Err(e)
                        if matches!(
                            e.kind(),
                            std::io::ErrorKind::NotFound | std::io::ErrorKind::NotADirectory
                        ) => {}
                    Err(_) => bail!("INPUT_PATH_INVALID"),
                }
            }
        }
    }
    Ok(output)
}

pub fn check_output<'a>(output: &Path, inputs: impl IntoIterator<Item = &'a Path>) -> Result<()> {
    let destination = resolved(output)?;
    let inode = |path: &Path| fs::metadata(path).ok().map(|m| (m.dev(), m.ino()));
    let output_inode = inode(output);
    for path in inputs {
        let path_resolved = resolved(path)?;
        ensure!(
            !destination.starts_with(&path_resolved)
                && !path_resolved.starts_with(&destination)
                && (output_inode.is_none() || output_inode != inode(path)),
            "REPORT_INPUT_COLLISION"
        );
    }
    Ok(())
}
