use anyhow::{Context, Result, bail, ensure};
use serde::de::{Deserialize, Deserializer, Error, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs::{self, File, Metadata, OpenOptions};
use std::io::Read;
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
    frozen: Option<BTreeMap<PathBuf, crate::workspace::InputWitness>>,
    presence: BTreeMap<PathBuf, bool>,
    drifted: bool,
    limit: u64,
    stamps: HashMap<PathBuf, Stamp>,
    cache: HashMap<PathBuf, Vec<u8>>,
    document_started: bool,
}

impl Reader {
    pub fn new(limit: u64) -> Self {
        Self {
            frozen: None,
            presence: BTreeMap::new(),
            drifted: false,
            limit,
            stamps: HashMap::new(),
            cache: HashMap::new(),
            document_started: false,
        }
    }

    pub fn new_frozen(
        limit: u64,
        inputs: &BTreeMap<PathBuf, crate::workspace::InputWitness>,
        presence: &BTreeMap<PathBuf, bool>,
    ) -> Self {
        Self {
            frozen: Some(inputs.clone()),
            presence: presence.clone(),
            ..Self::new(limit)
        }
    }

    pub fn drifted(&self) -> bool {
        self.drifted
    }

    pub fn local_file(&self, path: &Path) -> Result<(PathBuf, bool)> {
        let target = resolved(path).context("input_drift")?;
        let exists = target.is_file();
        if self.frozen.is_some() {
            ensure!(self.presence.get(&target) == Some(&exists), "input_drift");
        }
        Ok((target, exists))
    }

    pub fn begin_document(&mut self) {
        self.document_started = true;
        self.cache.clear();
    }

    pub fn changed_paths(&self) -> HashSet<PathBuf> {
        self.stamps
            .iter()
            .filter(|(path, stamp)| !stamp.current(path))
            .map(|(path, _)| path.clone())
            .collect()
    }

    pub fn read(&mut self, path: &Path) -> Result<Vec<u8>, &'static str> {
        self.read_with_limit(path, self.limit)
    }

    pub fn read_with_limit(&mut self, path: &Path, limit: u64) -> Result<Vec<u8>, &'static str> {
        let path = std::path::absolute(path).map_err(|_| "INPUT_READ_FAILED")?;
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
        let observed = read(&path, limit);
        if let Some(inputs) = &self.frozen {
            use crate::workspace::{InputWitness, checksum};
            let key = resolved(&path).map_err(|_| "INPUT_PATH_INVALID")?;
            let matches = match (&observed, inputs.get(&key)) {
                (Ok((bytes, _)), Some(InputWitness::Bytes { sha256 })) => {
                    checksum(bytes) == *sha256
                }
                (Err(code), Some(InputWitness::Unavailable { reason })) => *code == reason,
                _ => false,
            };
            if !matches {
                self.drifted = true;
                return Err("INPUT_CHANGED");
            }
        }
        let (bytes, stamp) = observed?;
        if self
            .stamps
            .get(&path)
            .is_some_and(|previous| previous != &stamp)
        {
            return Err("INPUT_CHANGED");
        }
        self.stamps.insert(path.clone(), stamp);
        if self.document_started {
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

pub fn parse_utf8_json(bytes: &[u8]) -> Result<Value> {
    let Unique(value) = serde_json::from_slice(bytes).map_err(|err| {
        anyhow::anyhow!(
            "{}",
            if err.to_string().starts_with("DUPLICATE_JSON_FIELD") {
                "DUPLICATE_JSON_FIELD"
            } else {
                "WORK_PLAN_UNREADABLE_OR_INVALID"
            }
        )
    })?;
    Ok(value)
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
    fn late_drift_invalidates_a_consumed_snapshot() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let first_path = directory.path().join("first.md");
        let second_path = directory.path().join("second.md");
        fs::write(&first_path, "first")?;
        fs::write(&second_path, "second")?;
        let mut reader = Reader::new(100);
        reader.begin_document();
        assert_eq!(reader.read(&first_path), Ok(b"first".to_vec()));
        reader.begin_document();
        assert_eq!(reader.read(&second_path), Ok(b"second".to_vec()));
        fs::write(&first_path, "changed first")?;
        assert_eq!(reader.changed_paths(), HashSet::from([first_path.clone()]));
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
