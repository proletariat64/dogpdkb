use anyhow::{Context, Result, bail, ensure};
use serde::de::{Deserialize, Deserializer, Error, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Value, json};
use std::borrow::Cow;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs::{self, File, Metadata, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Component, Path, PathBuf};

#[derive(Debug, Clone, Hash, Eq, Ord, PartialEq, PartialOrd)]
pub struct Identity {
    pub knowledge_base: String,
    pub document_id: String,
}

pub const METADATA_KEYS: [&str; 3] = ["name", "logical_path", "source_ref"];
pub const MANIFEST_LIMIT: u64 = 16 * 1024 * 1024;

pub struct Document {
    pub identity: Identity,
    pub raw: Value,
    pub path: PathBuf,
    pub metadata: Value,
    pub base_dir: PathBuf,
}

impl Document {
    pub fn resolve(&self, reference: &str) -> PathBuf {
        self.base_dir.join(reference)
    }

    pub fn content_kind(&self) -> &str {
        self.raw["content_kind"]
            .as_str()
            .expect("validated content kind")
    }
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

pub struct Reader {
    limit: u64,
    paths: HashSet<PathBuf>,
    stamps: HashMap<PathBuf, Stamp>,
    cache: HashMap<PathBuf, Vec<u8>>,
    current: Option<Identity>,
    dependencies: BTreeMap<Identity, HashSet<PathBuf>>,
}

impl Reader {
    pub fn new(limit: u64) -> Self {
        Self {
            limit,
            paths: HashSet::new(),
            stamps: HashMap::new(),
            cache: HashMap::new(),
            current: None,
            dependencies: BTreeMap::new(),
        }
    }

    pub fn begin_document(&mut self, identity: &Identity) {
        self.current = Some(identity.clone());
        self.cache.clear();
        self.dependencies.entry(identity.clone()).or_default();
    }

    pub fn paths(&self) -> &HashSet<PathBuf> {
        &self.paths
    }

    pub fn changed_paths(&self) -> HashSet<PathBuf> {
        self.stamps
            .iter()
            .filter(|(path, stamp)| !stamp.current(path))
            .map(|(path, _)| path.clone())
            .collect()
    }

    pub fn affected_documents(&self, changed: &HashSet<PathBuf>) -> BTreeSet<Identity> {
        self.dependencies
            .iter()
            .filter(|(_, paths)| !paths.is_disjoint(changed))
            .map(|(identity, _)| identity.clone())
            .collect()
    }

    pub fn read(&mut self, path: &Path) -> Result<Vec<u8>, &'static str> {
        self.read_with_limit(path, self.limit)
    }

    pub fn read_with_limit(&mut self, path: &Path, limit: u64) -> Result<Vec<u8>, &'static str> {
        let path = std::path::absolute(path).map_err(|_| "INPUT_READ_FAILED")?;
        self.paths.insert(path.clone());
        if let Some(identity) = &self.current {
            self.dependencies
                .entry(identity.clone())
                .or_default()
                .insert(path.clone());
        }
        if self
            .stamps
            .get(&path)
            .is_some_and(|stamp| !stamp.current(&path))
        {
            return Err("INPUT_CHANGED");
        }
        if let Some(bytes) = self.cache.get(&path) {
            return if bytes.len() as u64 > limit {
                Err("FILE_BUDGET_EXCEEDED")
            } else {
                Ok(bytes.clone())
            };
        }
        let (bytes, stamp) = read(&path, limit)?;
        if self
            .stamps
            .get(&path)
            .is_some_and(|previous| previous != &stamp)
        {
            return Err("INPUT_CHANGED");
        }
        self.stamps.insert(path.clone(), stamp);
        if self.current.is_some() {
            self.cache.insert(path, bytes.clone());
        }
        Ok(bytes)
    }
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

pub fn evidence_paths(v: &Value, root: &Path, paths: &mut Vec<PathBuf>) {
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

pub fn referenced_paths(document: &Document) -> Vec<PathBuf> {
    let mut paths = vec![document.path.clone()];
    for key in ["resources", "ocr", "provenance"] {
        evidence_paths(&document.raw[key], &document.base_dir, &mut paths);
    }
    paths
}

fn json_utf8(bytes: &[u8]) -> Result<Cow<'_, [u8]>> {
    const INVALID: &str = "MANIFEST_UNREADABLE_OR_INVALID";
    if let Some(bytes) = bytes.strip_prefix(b"\xef\xbb\xbf") {
        return Ok(Cow::Borrowed(bytes));
    }
    let (size, little_endian, data) = match bytes {
        [0, 0, 0xfe, 0xff, rest @ ..] => (4, false, rest),
        [0xff, 0xfe, 0, 0, rest @ ..] => (4, true, rest),
        [0xfe, 0xff, rest @ ..] => (2, false, rest),
        [0xff, 0xfe, rest @ ..] => (2, true, rest),
        [0, 0, 0, _, ..] => (4, false, bytes),
        [_, 0, 0, 0, ..] => (4, true, bytes),
        [0, _, ..] => (2, false, bytes),
        [_, 0, ..] => (2, true, bytes),
        _ => return Ok(Cow::Borrowed(bytes)),
    };
    ensure!(data.len().is_multiple_of(size), INVALID);
    let text = if size == 2 {
        let units = data.as_chunks::<2>().0.iter().map(|&chunk| {
            if little_endian {
                u16::from_le_bytes(chunk)
            } else {
                u16::from_be_bytes(chunk)
            }
        });
        char::decode_utf16(units)
            .collect::<Result<String, _>>()
            .context(INVALID)?
    } else {
        data.as_chunks::<4>()
            .0
            .iter()
            .map(|&chunk| {
                let codepoint = if little_endian {
                    u32::from_le_bytes(chunk)
                } else {
                    u32::from_be_bytes(chunk)
                };
                char::from_u32(codepoint).context(INVALID)
            })
            .collect::<Result<String>>()?
    };
    Ok(Cow::Owned(text.into_bytes()))
}

pub fn parse_json(bytes: &[u8]) -> Result<Value> {
    let Unique(value) = serde_json::from_slice(&json_utf8(bytes)?).map_err(|err| {
        anyhow::anyhow!(
            "{}",
            if err.to_string().starts_with("DUPLICATE_JSON_FIELD") {
                "DUPLICATE_JSON_FIELD"
            } else {
                "MANIFEST_UNREADABLE_OR_INVALID"
            }
        )
    })?;
    Ok(value)
}

pub fn load(
    path: &Path,
    reader: &mut Reader,
    body_only: bool,
) -> Result<BTreeMap<Identity, Document>> {
    let bytes = reader
        .read_with_limit(path, MANIFEST_LIMIT)
        .map_err(anyhow::Error::msg)?;
    decode_manifest(
        &parse_json(&bytes)?,
        path.parent().context("MANIFEST_SCHEMA_INVALID")?,
        body_only,
    )
}

pub fn decode_manifest(
    value: &Value,
    root: &Path,
    body_only: bool,
) -> Result<BTreeMap<Identity, Document>> {
    ensure!(value["schema_version"] == "1.0", "MANIFEST_SCHEMA_INVALID");
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
        let id = Identity {
            knowledge_base: field("knowledge_base")?.to_owned(),
            document_id: field("document_id")?.to_owned(),
        };
        let path = root.join(field("path")?);
        ensure!(
            matches!(
                row["content_kind"].as_str(),
                Some("raw" | "historical_enriched")
            ),
            "CONTENT_KIND_INVALID"
        );
        let metadata = &row["metadata"];
        ensure!(
            METADATA_KEYS.iter().all(|&k| metadata[k].is_string())
                && metadata.get("sync_timestamp").is_none_or(Value::is_string),
            "METADATA_SCHEMA_INVALID"
        );
        if !body_only {
            ensure!(
                row["resources"]
                    .as_array()
                    .is_some_and(|items| items.iter().all(Value::is_object)),
                "RESOURCE_SCHEMA_INVALID"
            );
            let ocr = &row["ocr"];
            ensure!(
                (ocr.is_null() || ocr.is_object())
                    && row.get("provenance").is_none_or(Value::is_object),
                "PROVENANCE_SCHEMA_INVALID"
            );
        }
        let doc = Document {
            identity: id.clone(),
            raw: row.clone(),
            path,
            metadata: metadata.clone(),
            base_dir: root.to_owned(),
        };
        ensure!(documents.insert(id, doc).is_none(), "DUPLICATE_DOCUMENT_ID");
    }
    Ok(documents)
}

pub fn resolved(path: &Path) -> Result<PathBuf> {
    resolve_path(path, &mut 40)
}

fn resolve_path(path: &Path, links: &mut usize) -> Result<PathBuf> {
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
                        ) =>
                    {
                        if let Ok(target) = fs::read_link(&output) {
                            *links = links.checked_sub(1).context("INPUT_PATH_INVALID")?;
                            output.pop();
                            output = resolve_path(&output.join(target), links)?;
                        }
                    }
                    Err(_) => bail!("INPUT_PATH_INVALID"),
                }
            }
        }
    }
    Ok(output)
}

pub fn json_bytes(value: &Value) -> Result<Vec<u8>> {
    let mut data = serde_json::to_vec_pretty(value)?;
    data.push(b'\n');
    Ok(data)
}

fn parent(path: &Path) -> &Path {
    path.parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
}

pub fn publish(outputs: &[(PathBuf, Vec<u8>)]) -> Result<()> {
    let mut staged = Vec::with_capacity(outputs.len());
    let mut backups = Vec::with_capacity(outputs.len());
    for (index, (path, data)) in outputs.iter().enumerate() {
        check_output(
            path,
            outputs[..index]
                .iter()
                .map(|(previous, _)| previous.as_path()),
        )?;
        let mut temp =
            tempfile::NamedTempFile::new_in(parent(path)).context("REPORT_WRITE_FAILED")?;
        temp.write_all(data).context("REPORT_WRITE_FAILED")?;
        temp.as_file().sync_all().context("REPORT_WRITE_FAILED")?;
        staged.push(temp);
    }
    for (path, _) in outputs {
        let backup = match fs::symlink_metadata(path) {
            Ok(_) => {
                let directory =
                    tempfile::tempdir_in(parent(path)).context("REPORT_WRITE_FAILED")?;
                fs::hard_link(path, directory.path().join("backup"))
                    .context("REPORT_WRITE_FAILED")?;
                Some(directory)
            }
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => None,
            Err(err) => return Err(err).context("REPORT_WRITE_FAILED"),
        };
        backups.push(backup);
    }
    for (index, (temp, (path, _))) in staged.into_iter().zip(outputs).enumerate() {
        if let Err(err) = temp.persist(path) {
            let mut failed = false;
            for ((previous, _), backup) in outputs[..index].iter().zip(&mut backups).rev() {
                if let Some(backup) = backup.take() {
                    if fs::rename(backup.path().join("backup"), previous).is_err() {
                        let _ = backup.keep();
                        failed = true;
                    }
                } else {
                    failed |= fs::remove_file(previous).is_err();
                }
            }
            ensure!(!failed, "REPORT_ROLLBACK_FAILED");
            return Err(err).context("REPORT_WRITE_FAILED");
        }
    }
    Ok(())
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn late_drift_invalidates_only_documents_that_read_the_path() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let first_path = directory.path().join("first.md");
        let second_path = directory.path().join("second.md");
        fs::write(&first_path, "first")?;
        fs::write(&second_path, "second")?;
        let identity = |id: &str| Identity {
            knowledge_base: "book".into(),
            document_id: id.into(),
        };
        let first = identity("first");
        let second = identity("second");
        let mut reader = Reader::new(100);
        reader.begin_document(&first);
        assert_eq!(reader.read(&first_path), Ok(b"first".to_vec()));
        reader.begin_document(&second);
        assert_eq!(reader.read(&second_path), Ok(b"second".to_vec()));
        fs::write(&first_path, "changed first")?;
        assert_eq!(
            reader.affected_documents(&reader.changed_paths()),
            BTreeSet::from([first])
        );
        assert_eq!(reader.read(&first_path), Err("INPUT_CHANGED"));
        Ok(())
    }

    #[test]
    fn missing_declared_input_is_protected_through_a_dangling_symlink() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let alias = directory.path().join("alias");
        let source = directory.path().join("unavailable.md");
        std::os::unix::fs::symlink("unavailable.md", &alias)?;
        assert_eq!(
            check_output(&alias, [source.as_path()])
                .unwrap_err()
                .to_string(),
            "REPORT_INPUT_COLLISION"
        );
        Ok(())
    }
}
