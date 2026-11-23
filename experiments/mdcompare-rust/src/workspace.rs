use crate::config::{CompareSettings, Config};
use crate::input::{self, Document, Identity, MANIFEST_LIMIT};
use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Component, Path, PathBuf};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Operation {
    Semantic,
    Knowledge,
    Strict,
    Catalog,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkPlan {
    pub schema_version: String,
    pub operation: Operation,
    pub documents: Vec<WorkDocument>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub body_recovery: Option<PathBuf>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkDocument {
    pub knowledge_base: String,
    pub document_id: String,
    pub baseline: Option<Snapshot>,
    pub incoming: Option<Snapshot>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Snapshot {
    pub path: PathBuf,
    pub content_kind: String,
    pub metadata: Metadata,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provenance: Option<Provenance>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub body_scope: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resources: Option<Vec<Value>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ocr: Option<Value>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Metadata {
    pub name: String,
    pub logical_path: String,
    pub source_ref: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sync_timestamp: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Provenance {
    pub version: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case", deny_unknown_fields)]
pub enum InputWitness {
    Bytes { sha256: String },
    Unavailable { reason: String },
}

impl InputWitness {
    fn observe(path: &Path, limit: u64) -> Result<Self> {
        match input::read(path, limit) {
            Ok((bytes, _)) => Ok(Self::Bytes {
                sha256: checksum(&bytes),
            }),
            Err("INPUT_CHANGED") => bail!("input_drift"),
            Err(reason) => Ok(Self::Unavailable {
                reason: reason.into(),
            }),
        }
    }
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FrozenPlan {
    schema_version: String,
    source: PathBuf,
    source_sha256: String,
    pub work: WorkPlan,
    pub settings: CompareSettings,
    pub inputs: BTreeMap<PathBuf, InputWitness>,
    pub presence: BTreeMap<PathBuf, bool>,
    // A work plan and body-recovery declaration share the work-plan limit.
    manifest_inputs: BTreeSet<PathBuf>,
    path_targets: BTreeMap<PathBuf, PathBuf>,
}

impl FrozenPlan {
    pub fn documents(&self) -> Vec<(Option<Document>, Option<Document>)> {
        let directory = self.source.parent().expect("absolute plan path");
        self.work
            .documents
            .iter()
            .map(|row| {
                let document = |snapshot: &Snapshot| {
                    let identity = Identity {
                        knowledge_base: row.knowledge_base.clone(),
                        document_id: row.document_id.clone(),
                    };
                    let mut raw =
                        serde_json::to_value(snapshot).expect("snapshot serialized at acceptance");
                    raw["knowledge_base"] = json!(row.knowledge_base);
                    raw["document_id"] = json!(row.document_id);
                    Document {
                        identity,
                        path: directory.join(&snapshot.path),
                        metadata: raw["metadata"].clone(),
                        raw,
                        base_dir: directory.to_owned(),
                    }
                };
                (
                    row.baseline.as_ref().map(document),
                    row.incoming.as_ref().map(document),
                )
            })
            .collect()
    }

    pub fn verify_inputs(&self) -> Result<()> {
        for (path, target) in &self.path_targets {
            ensure!(input::resolved(path)? == *target, "input_drift");
        }
        for (path, expected) in &self.inputs {
            ensure!(
                InputWitness::observe(path, self.limit(path))? == *expected,
                "input_drift"
            );
        }
        for (path, expected) in &self.presence {
            ensure!(path.is_file() == *expected, "input_drift");
        }
        Ok(())
    }

    fn limit(&self, path: &Path) -> u64 {
        if self.manifest_inputs.contains(path) {
            MANIFEST_LIMIT
        } else {
            self.settings.compare.max_file_bytes
        }
    }

    fn bind_path(&mut self, path: &Path) -> Result<PathBuf> {
        let target = input::resolved(path)?;
        self.path_targets.insert(path.to_owned(), target.clone());
        Ok(target)
    }

    fn observe(&mut self, path: &Path) -> Result<()> {
        let limit = self.limit(path);
        if !self.inputs.contains_key(path) {
            self.inputs
                .insert(path.to_owned(), InputWitness::observe(path, limit)?);
        }
        Ok(())
    }
}

pub struct Workspace {
    pub source: PathBuf,
    pub root: PathBuf,
    pub source_sha256: String,
    source_bytes: Vec<u8>,
}

pub struct Lock {
    _file: File,
}

impl Workspace {
    pub fn locate(plan: &Path) -> Result<Self> {
        let source = fs::canonicalize(plan).context("invalid_input")?;
        ensure!(
            source.extension().is_some_and(|ext| ext == "json"),
            "invalid_input"
        );
        let (source_bytes, _) = input::read(&source, MANIFEST_LIMIT)
            .map_err(anyhow::Error::msg)
            .context("invalid_input")?;
        let root = source.with_extension("mdcompare");
        // A committed or prepared workspace must be an actual private directory.
        match fs::symlink_metadata(&root) {
            Ok(meta) => ensure!(meta.is_dir(), "binding_invalid"),
            Err(e) if e.kind() == ErrorKind::NotFound => {}
            Err(e) => return Err(e).context("binding_invalid"),
        }
        Ok(Self {
            source,
            root,
            source_sha256: checksum(&source_bytes),
            source_bytes,
        })
    }

    pub fn schema_version(&self) -> Result<String> {
        parse_json(&self.source_bytes)?["schema_version"]
            .as_str()
            .map(ToOwned::to_owned)
            .context("invalid_input")
    }

    pub fn creation_lock(&self) -> Result<Lock> {
        open_lock(&self.source.with_extension("mdcompare.accept.lock"), true)
    }

    pub fn lock(&self, name: &str, wait: bool) -> Result<Lock> {
        open_lock(&artifact_path(&self.root, name)?, wait)
    }

    pub fn initialize(&self, files: &[(&str, Vec<u8>)]) -> Result<()> {
        let parent = self.root.parent().context("publish_failed")?;
        let staging = tempfile::tempdir_in(parent).context("publish_failed")?;
        for (name, bytes) in files {
            let destination = artifact_path(staging.path(), name)?;
            atomic_new(&destination, bytes)?;
        }
        File::open(staging.path())?.sync_all()?;
        ensure!(
            !self.root.exists() && self.root.symlink_metadata().is_err(),
            "binding_invalid"
        );
        // A lock serializes writers. NOREPLACE also prevents replacing an
        // unrelated directory created by a writer outside this protocol.
        rename_directory(staging.path(), &self.root)?;
        File::open(parent)?.sync_all()?;
        Ok(())
    }
}

fn rename_directory(source: &Path, destination: &Path) -> Result<()> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;
    let source = CString::new(source.as_os_str().as_bytes()).context("publish_failed")?;
    let destination = CString::new(destination.as_os_str().as_bytes()).context("publish_failed")?;
    // Linux-only portable release. Both paths refer to the same parent filesystem.
    let result = unsafe {
        libc::syscall(
            libc::SYS_renameat2,
            libc::AT_FDCWD,
            source.as_ptr(),
            libc::AT_FDCWD,
            destination.as_ptr(),
            libc::RENAME_NOREPLACE,
        )
    };
    ensure!(result == 0, "publish_failed");
    Ok(())
}

fn open_lock(path: &Path, wait: bool) -> Result<Lock> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)
        .context("binding_invalid")?;
    ensure!(
        file.metadata()?.is_file() && file.metadata()?.nlink() == 1,
        "binding_invalid"
    );
    ensure!(try_lock(&file, wait)?, "workspace_busy");
    Ok(Lock { _file: file })
}

fn try_lock(file: &File, wait: bool) -> Result<bool> {
    let flags = libc::LOCK_EX | if wait { 0 } else { libc::LOCK_NB };
    loop {
        if unsafe { libc::flock(file.as_raw_fd(), flags) } == 0 {
            return Ok(true);
        }
        match std::io::Error::last_os_error().kind() {
            ErrorKind::WouldBlock => return Ok(false),
            ErrorKind::Interrupted => continue,
            _ => bail!("binding_invalid"),
        }
    }
}

pub fn execution_state(workspace: &Workspace) -> Result<Option<bool>> {
    let path = artifact_path(&workspace.root, "execution.lock")?;
    let file = match OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(&path)
    {
        Ok(file) => file,
        Err(e) if e.kind() == ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e).context("binding_invalid"),
    };
    ensure!(
        file.metadata()?.is_file() && file.metadata()?.nlink() == 1,
        "binding_invalid"
    );
    Ok(Some(!try_lock(&file, false)?))
}

pub fn checksum(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

pub fn parse_json(bytes: &[u8]) -> Result<Value> {
    input::parse_utf8_json(bytes)
}

pub fn read_json(path: &Path) -> Result<Value> {
    let (bytes, _) = input::read(path, u64::MAX).map_err(anyhow::Error::msg)?;
    parse_json(&bytes)
}

pub fn artifact_path(root: &Path, relative: &str) -> Result<PathBuf> {
    let mut path = root.to_owned();
    let parts: Vec<_> = Path::new(relative).components().collect();
    ensure!(
        !parts.is_empty()
            && !relative
                .split('/')
                .any(|part| matches!(part, "" | "." | "..")),
        "binding_invalid"
    );
    ensure!(
        fs::symlink_metadata(root).is_ok_and(|m| m.is_dir()),
        "binding_invalid"
    );
    for (idx, component) in parts.iter().enumerate() {
        ensure!(matches!(component, Component::Normal(_)), "binding_invalid");
        path.push(component.as_os_str());
        match fs::symlink_metadata(&path) {
            Ok(meta) if idx == parts.len() - 1 => {
                ensure!(meta.is_file() && meta.nlink() == 1, "binding_invalid")
            }
            Ok(meta) => ensure!(meta.is_dir(), "binding_invalid"),
            Err(e) if e.kind() == ErrorKind::NotFound => {}
            Err(e) => return Err(e).context("binding_invalid"),
        }
    }
    Ok(path)
}

fn stage_file(path: &Path, bytes: &[u8]) -> Result<tempfile::NamedTempFile> {
    let parent = path.parent().context("publish_failed")?;
    let mut temp = tempfile::NamedTempFile::new_in(parent).context("publish_failed")?;
    temp.write_all(bytes).context("publish_failed")?;
    temp.as_file().sync_all().context("publish_failed")?;
    Ok(temp)
}

fn atomic_new(path: &Path, bytes: &[u8]) -> Result<()> {
    stage_file(path, bytes)?
        .persist_noclobber(path)
        .context("publish_failed")?;
    File::open(path.parent().context("publish_failed")?)?
        .sync_all()
        .context("publish_failed")
}

pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    if let Ok(meta) = fs::symlink_metadata(path) {
        ensure!(meta.is_file() && meta.nlink() == 1, "binding_invalid");
    }
    stage_file(path, bytes)?
        .persist(path)
        .context("publish_failed")?;
    File::open(path.parent().context("publish_failed")?)?
        .sync_all()
        .context("publish_failed")
}

fn validate_snapshot(snapshot: &Snapshot, operation: Operation) -> Result<()> {
    ensure!(!snapshot.path.as_os_str().is_empty(), "invalid_input");
    ensure!(
        matches!(snapshot.content_kind.as_str(), "raw" | "enriched"),
        "invalid_input"
    );
    ensure!(
        snapshot.body_scope.as_ref().is_none_or(Value::is_object),
        "invalid_input"
    );
    ensure!(
        snapshot.ocr.as_ref().is_none_or(Value::is_object),
        "invalid_input"
    );
    ensure!(
        snapshot
            .resources
            .as_ref()
            .is_none_or(|r| r.iter().all(Value::is_object)),
        "invalid_input"
    );
    ensure!(
        operation != Operation::Strict || snapshot.resources.is_some(),
        "invalid_input"
    );
    Ok(())
}

fn validate_work(value: Value) -> Result<WorkPlan> {
    let rows = value["documents"].as_array().context("invalid_input")?;
    for row in rows {
        ensure!(
            row.get("baseline").is_some() && row.get("incoming").is_some(),
            "invalid_input"
        );
    }
    let work: WorkPlan = serde_json::from_value(value).context("invalid_input")?;
    ensure!(work.schema_version == "mdcompare-work-1", "invalid_input");
    ensure!(
        work.body_recovery.is_none() || work.operation == Operation::Semantic,
        "invalid_input"
    );
    let mut identities = BTreeSet::new();
    for row in &work.documents {
        ensure!(
            !row.knowledge_base.trim().is_empty()
                && !row.document_id.trim().is_empty()
                && identities.insert((&row.knowledge_base, &row.document_id)),
            "invalid_input"
        );
        ensure!(
            row.baseline.is_some() || row.incoming.is_some(),
            "invalid_input"
        );
        ensure!(
            work.operation != Operation::Catalog || row.baseline.is_none(),
            "invalid_input"
        );
        for snapshot in [&row.baseline, &row.incoming].into_iter().flatten() {
            validate_snapshot(snapshot, work.operation)?;
        }
    }
    Ok(work)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Recovery {
    schema_version: String,
    documents: Vec<RecoveryDocument>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RecoveryDocument {
    knowledge_base: String,
    document_id: String,
    side: String,
    source_copy: PathBuf,
    insertions: Vec<Insertion>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Insertion {
    byte_offset: usize,
    text: String,
}

fn add_recovery(frozen: &mut FrozenPlan, path: &Path) -> Result<()> {
    let (bytes, _) = input::read(path, MANIFEST_LIMIT)
        .map_err(anyhow::Error::msg)
        .context("invalid_input")?;
    let recovery: Recovery =
        serde_json::from_value(parse_json(&bytes)?).context("invalid_input")?;
    ensure!(
        recovery.schema_version == "mdcompare-body-recovery-1",
        "invalid_input"
    );
    let mut identities = BTreeSet::new();
    let parent = path.parent().context("invalid_input")?;
    for item in recovery.documents {
        ensure!(
            !item.source_copy.as_os_str().is_empty()
                && matches!(item.side.as_str(), "baseline" | "incoming")
                && frozen.work.documents.iter().any(|row| {
                    row.knowledge_base == item.knowledge_base
                        && row.document_id == item.document_id
                        && if item.side == "baseline" {
                            row.baseline.is_some()
                        } else {
                            row.incoming.is_some()
                        }
                })
                && identities.insert((item.knowledge_base, item.document_id, item.side)),
            "invalid_input"
        );
        ensure!(
            item.insertions.iter().all(|i| !i.text.is_empty())
                && item
                    .insertions
                    .windows(2)
                    .all(|pair| pair[0].byte_offset <= pair[1].byte_offset),
            "invalid_input"
        );
        let witness = frozen.bind_path(&parent.join(item.source_copy))?;
        frozen.observe(&witness)?;
    }
    frozen.manifest_inputs.insert(path.to_owned());
    frozen.inputs.insert(
        path.to_owned(),
        InputWitness::Bytes {
            sha256: checksum(&bytes),
        },
    );
    Ok(())
}

fn validate_settings(operation: Operation, settings: &CompareSettings) -> Result<()> {
    ensure!(
        !settings.compare.normalize_line_endings || operation == Operation::Strict,
        "invalid_config"
    );
    Ok(())
}

fn create_plan(workspace: &Workspace, explicit: Option<&Path>) -> Result<FrozenPlan> {
    let mut work = validate_work(parse_json(&workspace.source_bytes)?)?;
    let directory = workspace.source.parent().context("invalid_input")?;
    let recovery_paths = if let Some(path) = &mut work.body_recovery {
        ensure!(!path.as_os_str().is_empty(), "invalid_input");
        let original = directory.join(&*path);
        *path = input::resolved(&original)?;
        Some((original, path.clone()))
    } else {
        None
    };
    let config = Config::load(Some(&workspace.source), explicit)?;
    let config_source = config.source.clone();
    let settings = config.comparison();
    validate_settings(work.operation, &settings)?;
    let mut frozen = FrozenPlan {
        schema_version: "mdcompare-frozen-work-1".into(),
        source: workspace.source.clone(),
        source_sha256: workspace.source_sha256.clone(),
        work,
        settings,
        inputs: BTreeMap::new(),
        presence: BTreeMap::new(),
        manifest_inputs: BTreeSet::new(),
        path_targets: BTreeMap::new(),
    };
    let mut protected = Vec::new();
    for document in frozen
        .documents()
        .into_iter()
        .flat_map(|(a, b)| [a, b])
        .flatten()
    {
        let path = frozen.bind_path(&document.path)?;
        let declared = input::referenced_paths(&document);
        protected.extend(declared.iter().cloned());
        if frozen.work.operation == Operation::Catalog {
            match input::read(&path, frozen.settings.compare.max_file_bytes) {
                Ok((bytes, _)) => {
                    frozen.inputs.insert(
                        path.clone(),
                        InputWitness::Bytes {
                            sha256: checksum(&bytes),
                        },
                    );
                    for candidate in crate::catalog::local_candidates(&document, &bytes)? {
                        let candidate = frozen.bind_path(&candidate)?;
                        let exists = candidate.is_file();
                        frozen.presence.entry(candidate).or_insert(exists);
                    }
                }
                Err("INPUT_CHANGED") => bail!("input_drift"),
                Err(reason) => {
                    frozen.inputs.insert(
                        path,
                        InputWitness::Unavailable {
                            reason: reason.into(),
                        },
                    );
                }
            }
        } else {
            frozen.observe(&path)?;
            if frozen.work.operation == Operation::Strict {
                for dependency in &declared {
                    let dependency = frozen.bind_path(dependency)?;
                    frozen.observe(&dependency)?;
                }
            }
        }
    }
    if let Some((original, path)) = recovery_paths {
        frozen.path_targets.insert(original, path.clone());
        add_recovery(&mut frozen, &path)?;
    }
    protected.extend(frozen.inputs.keys().cloned());
    protected.extend(frozen.presence.keys().cloned());
    protected.extend(config_source);
    protected.push(workspace.source.clone());
    input::check_output(&workspace.root, protected.iter().map(PathBuf::as_path))
        .context("binding_invalid")?;
    // Refuse an input which depends on the adjacent lock we create.
    input::check_output(
        &workspace.source.with_extension("mdcompare.accept.lock"),
        protected.iter().map(PathBuf::as_path),
    )
    .context("binding_invalid")?;
    Ok(frozen)
}

pub fn accept(plan: &Path, explicit: Option<&Path>) -> Result<(Workspace, FrozenPlan)> {
    let workspace = Workspace::locate(plan)?;
    // Validate the work schema before creating anything around an invalid submission.
    validate_work(parse_json(&workspace.source_bytes)?)?;
    if workspace.root.is_dir() {
        let frozen = load_frozen(&workspace)?;
        check_explicit_config(&workspace, &frozen, explicit)?;
        return Ok((workspace, frozen));
    }
    let prepared = create_plan(&workspace, explicit)?;
    let _lock = workspace.creation_lock()?;
    let frozen = if workspace.root.is_dir() {
        let frozen = load_frozen(&workspace)?;
        check_explicit_config(&workspace, &frozen, explicit)?;
        frozen
    } else {
        let current = Workspace::locate(plan)?;
        ensure!(
            current.source == workspace.source && current.source_sha256 == workspace.source_sha256,
            "input_drift"
        );
        prepared.verify_inputs()?;
        workspace.initialize(&[(
            "plan.json",
            input::json_bytes(&serde_json::to_value(&prepared)?)?,
        )])?;
        prepared
    };
    Ok((workspace, frozen))
}

fn check_explicit_config(
    workspace: &Workspace,
    frozen: &FrozenPlan,
    explicit: Option<&Path>,
) -> Result<()> {
    if let Some(explicit) = explicit {
        let config = Config::load(Some(&workspace.source), Some(explicit))?;
        ensure!(config.comparison() == frozen.settings, "invalid_config");
    }
    Ok(())
}

pub fn load_frozen(workspace: &Workspace) -> Result<FrozenPlan> {
    ensure!(workspace.root.exists(), "workspace_missing");
    decode_frozen(workspace, &read_artifact(&workspace.root, "plan.json")?)
}

fn decode_frozen(workspace: &Workspace, bytes: &[u8]) -> Result<FrozenPlan> {
    let frozen: FrozenPlan = serde_json::from_value(parse_json(bytes).context("binding_invalid")?)
        .context("binding_invalid")?;
    ensure!(
        frozen.schema_version == "mdcompare-frozen-work-1"
            && frozen.source == workspace.source
            && frozen.source_sha256 == workspace.source_sha256,
        "binding_invalid"
    );
    Ok(frozen)
}

pub fn draft(old: &Path, new: &Path, plan: &Path) -> Result<PathBuf> {
    let path = std::path::absolute(plan).context("invalid_input")?;
    ensure!(
        path.extension().is_some_and(|ext| ext == "json"),
        "invalid_input"
    );
    input::check_output(&path, [old, new]).context("binding_invalid")?;
    let identity = checksum(input::resolved(&path)?.as_os_str().as_encoded_bytes());
    let snapshot = |source: &Path| -> Result<Value> {
        let source = input::resolved(source)?;
        let name = source
            .file_stem()
            .and_then(|v| v.to_str())
            .context("invalid_input")?;
        Ok(json!({
            "path": source,
            "content_kind": "raw",
            "metadata": { "name": name, "logical_path": name, "source_ref": format!("local:{identity}") }
        }))
    };
    let value = json!({
        "schema_version": "mdcompare-work-1",
        "operation": "semantic",
        "documents": [{
            "knowledge_base": "local",
            "document_id": identity,
            "baseline": snapshot(old)?,
            "incoming": snapshot(new)?,
        }]
    });
    atomic_new(&path, &input::json_bytes(&value)?)?;
    Ok(path)
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CommitRecord {
    schema_version: String,
    plan_sha256: String,
    artifacts: BTreeMap<String, String>,
}

pub struct Commit {
    pub comparison_sha256: String,
    pub artifacts: BTreeMap<String, String>,
}

fn read_artifact(root: &Path, relative: &str) -> Result<Vec<u8>> {
    let path = artifact_path(root, relative)?;
    let (bytes, _) = input::read(&path, u64::MAX)
        .map_err(anyhow::Error::msg)
        .context("binding_invalid")?;
    Ok(bytes)
}

fn sync_artifact(root: &Path, name: &str) -> Result<()> {
    let path = artifact_path(root, name)?;
    File::open(&path)?.sync_all()?;
    let mut directory = path.parent().context("publish_failed")?;
    loop {
        File::open(directory)?.sync_all()?;
        if directory == root {
            return Ok(());
        }
        directory = directory.parent().context("publish_failed")?;
    }
}

fn verify_artifacts(workspace: &Workspace, record: &CommitRecord, sync: bool) -> Result<()> {
    let plan = read_artifact(&workspace.root, "plan.json")?;
    ensure!(checksum(&plan) == record.plan_sha256, "binding_invalid");
    let frozen = decode_frozen(workspace, &plan)?;
    if sync {
        sync_artifact(&workspace.root, "plan.json")?;
    }
    ensure!(
        record.artifacts.contains_key("result.json"),
        "binding_invalid"
    );
    ensure!(
        (frozen.work.operation == Operation::Semantic)
            == record.artifacts.contains_key("diff-index.json"),
        "binding_invalid"
    );
    let mut index = None;
    for (name, hash) in &record.artifacts {
        ensure!(
            matches!(
                name.as_str(),
                "diff-index.json" | "result.json" | "review.md"
            ),
            "binding_invalid"
        );
        let bytes = read_artifact(&workspace.root, name)?;
        ensure!(checksum(&bytes) == *hash, "binding_invalid");
        if name == "diff-index.json" {
            index = Some(parse_json(&bytes).context("binding_invalid")?);
        }
        if sync {
            sync_artifact(&workspace.root, name)?;
        }
    }
    if let Some(index) = index {
        ensure!(
            index["schema_version"] == "mdcompare-diff-index-1"
                && index["report_sha256"].as_str()
                    == Some(record.artifacts["result.json"].as_str()),
            "binding_invalid"
        );
        let mut names = BTreeSet::new();
        for row in index["documents"].as_array().context("binding_invalid")? {
            let name = row["evidence"].as_str().context("binding_invalid")?;
            let hash = row["sha256"].as_str().context("binding_invalid")?;
            ensure!(
                name.starts_with("evidence/") && names.insert(name),
                "binding_invalid"
            );
            ensure!(
                checksum(&read_artifact(&workspace.root, name)?) == hash,
                "binding_invalid"
            );
            if sync {
                sync_artifact(&workspace.root, name)?;
            }
        }
    }
    Ok(())
}

pub fn verify_commit(workspace: &Workspace) -> Result<Option<Commit>> {
    if !workspace.root.exists() {
        return Ok(None);
    }
    let path = artifact_path(&workspace.root, "commit.json")?;
    if !path.exists() {
        return Ok(None);
    }
    let bytes = read_artifact(&workspace.root, "commit.json")?;
    let record: CommitRecord =
        serde_json::from_value(parse_json(&bytes)?).context("binding_invalid")?;
    ensure!(
        record.schema_version == "mdcompare-commit-1",
        "binding_invalid"
    );
    verify_artifacts(workspace, &record, false)?;
    Ok(Some(Commit {
        comparison_sha256: checksum(&bytes),
        artifacts: record.artifacts,
    }))
}

pub fn publish_commit(workspace: &Workspace, artifacts: &[&str]) -> Result<Commit> {
    let path = artifact_path(&workspace.root, "commit.json")?;
    ensure!(!path.exists(), "binding_invalid");
    let mut record = CommitRecord {
        schema_version: "mdcompare-commit-1".into(),
        plan_sha256: checksum(&read_artifact(&workspace.root, "plan.json")?),
        artifacts: BTreeMap::new(),
    };
    for &name in artifacts {
        record.artifacts.insert(
            name.to_owned(),
            checksum(&read_artifact(&workspace.root, name)?),
        );
    }
    verify_artifacts(workspace, &record, true)?;
    let bytes = input::json_bytes(&serde_json::to_value(&record)?)?;
    atomic_new(&path, &bytes)?;
    Ok(Commit {
        comparison_sha256: checksum(&bytes),
        artifacts: record.artifacts,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    #[test]
    fn rejects_symlink_retarget_after_accept_even_when_both_destinations_are_frozen() -> Result<()>
    {
        let directory = tempfile::tempdir()?;
        let base = directory.path().join("base.md");
        let incoming = directory.path().join("incoming.md");
        let alias = directory.path().join("alias.md");
        let plan = directory.path().join("work.json");
        fs::write(&base, "base")?;
        fs::write(&incoming, "incoming")?;
        symlink(&base, &alias)?;
        draft(&base, &incoming, &plan)?;
        let mut value = read_json(&plan)?;
        value["documents"][0]["baseline"]["path"] = json!(alias);
        fs::write(&plan, input::json_bytes(&value)?)?;
        let (_, accepted) = accept(&plan, None)?;
        fs::remove_file(&alias)?;
        symlink(&incoming, &alias)?;
        ensure!(
            accepted.verify_inputs().is_err(),
            "retargeted input was trusted"
        );
        Ok(())
    }

    #[test]
    fn accepts_no_legacy_or_duplicate_keys_and_never_overwrites_draft() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let base = directory.path().join("base.md");
        let incoming = directory.path().join("incoming.md");
        let plan = directory.path().join("work.json");
        draft(&base, &incoming, &plan)?;
        let previous = fs::read(&plan)?;
        assert!(draft(&base, &incoming, &plan).is_err());
        assert_eq!(fs::read(&plan)?, previous);
        fs::write(
            &plan,
            r#"{"schema_version":"mdcompare-work-1","operation":"strict","documents":[],"documents":[]}"#,
        )?;
        assert!(accept(&plan, None).is_err());
        fs::write(
            &plan,
            r#"{"schema_version":"1.0","operation":"semantic","documents":[]}"#,
        )?;
        assert!(accept(&plan, None).is_err());
        assert!(!plan.with_extension("mdcompare").exists());
        Ok(())
    }

    #[test]
    fn reads_only_committed_generations_and_detects_tampered_sidecars() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let base = directory.path().join("base.md");
        let incoming = directory.path().join("incoming.md");
        let plan = directory.path().join("work.json");
        draft(&base, &incoming, &plan)?;
        let (workspace, _) = accept(&plan, None)?;
        let report = b"{}";
        let sidecar = b"{\"test\":true}";
        fs::create_dir(workspace.root.join("evidence"))?;
        fs::write(workspace.root.join("result.json"), report)?;
        fs::write(workspace.root.join("evidence/000000.json"), sidecar)?;
        fs::write(
            workspace.root.join("diff-index.json"),
            input::json_bytes(&json!({
                "schema_version": "mdcompare-diff-index-1", "report_sha256": checksum(report),
                "documents": [{"evidence": "evidence/000000.json", "sha256": checksum(sidecar)}]
            }))?,
        )?;
        assert!(verify_commit(&workspace)?.is_none());
        let published = publish_commit(&workspace, &["result.json", "diff-index.json"])?;
        assert_eq!(
            verify_commit(&workspace)?.unwrap().comparison_sha256,
            published.comparison_sha256
        );
        fs::write(workspace.root.join("evidence/000000.json"), b"{}")?;
        assert!(verify_commit(&workspace).is_err());
        Ok(())
    }
}
