use crate::artifacts::{atomic, json_bytes, owned_path, read_bound, sha};
use crate::plan::{self, Work};
use crate::runner::{PublicationReceipt, publication_state};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::{Value, json};
use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};

pub(crate) struct Workspace {
    pub work: PathBuf,
    pub root: PathBuf,
    pub download: PathBuf,
    pub plan: PathBuf,
    pub journal: PathBuf,
    pub result: PathBuf,
    pub images: PathBuf,
    pub evidence: PathBuf,
    pub lock: PathBuf,
    pub control: PathBuf,
    pub cancel: PathBuf,
}

#[derive(Debug)]
pub(crate) struct Purged;

impl std::fmt::Display for Purged {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("the batch workspace has been purged")
    }
}

impl std::error::Error for Purged {}

#[derive(Debug)]
pub(crate) enum CleanupPending {
    Reset,
    Purge,
}

impl CleanupPending {
    pub(crate) fn reason(&self) -> &'static str {
        match self {
            Self::Reset => "WORKSPACE_RESET_PENDING",
            Self::Purge => "WORKSPACE_PURGE_PENDING",
        }
    }
}

impl std::fmt::Display for CleanupPending {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Reset => formatter.write_str("finish the interrupted workspace reset"),
            Self::Purge => formatter.write_str("finish the interrupted workspace purge"),
        }
    }
}

impl std::error::Error for CleanupPending {}

#[derive(Clone, Deserialize, Serialize, Eq, PartialEq)]
struct Cleanup {
    work_path: PathBuf,
    #[serde(default)]
    work_sha256: String,
    #[serde(default)]
    unresolved_requests: bool,
    #[serde(default)]
    outputs: Vec<PublicationReceipt>,
}

#[derive(Clone, Copy, Deserialize, Serialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
enum ResetPhase {
    Resetting,
    Ready,
}

#[derive(Deserialize, Serialize)]
struct ResetIntent {
    status: ResetPhase,
    after_purge: bool,
    #[serde(flatten)]
    cleanup: Cleanup,
}

#[derive(Clone, Copy, Deserialize, Serialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
enum PurgePhase {
    Purging,
    Purged,
}

#[derive(Deserialize, Serialize)]
struct PurgeSeal {
    status: PurgePhase,
    #[serde(flatten)]
    cleanup: Cleanup,
}

impl Workspace {
    pub(crate) fn new(work: &Path) -> Result<Self> {
        let work = work.canonicalize().context("cannot open the work plan")?;
        ensure!(
            work.is_file() && work.extension().is_some_and(|ext| ext == "json"),
            "the work plan must be an existing .json file"
        );
        let root = work.with_extension("mdocr");
        owned_path(&root)?;
        Ok(Self {
            work,
            download: root.join("download.json"),
            plan: root.join("plan.json"),
            journal: root.join("journal.json"),
            result: root.join("result.json"),
            images: root.join("images"),
            evidence: root.join("evidence"),
            lock: root.join(".execution.lock"),
            control: root.join(".control.lock"),
            cancel: root.join(".cancel.json"),
            root,
        })
    }

    fn owner(&self) -> PathBuf {
        self.root.join(".owner.json")
    }

    fn purge_marker(&self) -> PathBuf {
        self.root.join(".purged.json")
    }

    fn reset_marker(&self) -> PathBuf {
        self.root.join("reset.json")
    }

    pub(crate) fn ensure_live(&self) -> Result<()> {
        let marker = self.purge_marker();
        owned_path(&marker)?;
        if marker.try_exists()? {
            return Err(Purged.into());
        }
        self.reset_active()?;
        Ok(())
    }

    pub(crate) fn reset_active(&self) -> Result<bool> {
        let Some(intent) = self.read_reset()? else {
            return Ok(false);
        };
        if intent.status == ResetPhase::Resetting {
            return Err(CleanupPending::Reset.into());
        }
        Ok(true)
    }

    pub(crate) fn complete_reset(&self) -> Result<()> {
        if self.reset_active()? {
            remove_file(&self.reset_marker())?;
            sync_parent(&self.reset_marker())?;
        }
        Ok(())
    }

    pub(crate) fn verify_reset_binding(&self, work_digest: &str) -> Result<()> {
        if let Some(intent) = self.read_reset()? {
            intent.cleanup.verify_digest(work_digest)?;
        }
        Ok(())
    }

    fn read_reset(&self) -> Result<Option<ResetIntent>> {
        let intent: Option<ResetIntent> = read_state(&self.reset_marker())?;
        if let Some(intent) = &intent {
            intent.cleanup.verify_path(self)?;
            ensure!(
                valid_digest(&intent.cleanup.work_sha256),
                "invalid reset work digest"
            );
        }
        Ok(intent)
    }

    fn read_seal(&self) -> Result<Option<PurgeSeal>> {
        let seal: Option<PurgeSeal> = read_state(&self.purge_marker())?;
        if let Some(seal) = &seal {
            seal.cleanup.verify_path(self)?;
        }
        Ok(seal)
    }

    pub(crate) fn ensure_owner(&self) -> Result<()> {
        let owner = self.owner();
        owned_path(&owner)?;
        let value = read_object(&owner).context("workspace has no valid ownership record")?;
        ensure!(
            value["schema_version"] == "mdocr-workspace-1"
                && value["work_path"].as_str().map(Path::new) == Some(self.work.as_path()),
            "workspace is owned by a different work plan"
        );
        Ok(())
    }

    pub(crate) fn create_and_lock(&self) -> Result<File> {
        self.ensure_live()?;
        fs::create_dir_all(&self.root).context("cannot create the batch workspace")?;
        let owner = self.owner();
        owned_path(&owner)?;
        if !owner.try_exists()? {
            self.check_unclaimed()?;
        }
        let lock = try_lock(&self.lock)?;
        self.ensure_live()?;
        if !owner.try_exists()? {
            self.check_unclaimed()?;
            atomic(
                &owner,
                &json_bytes(&json!({"schema_version":"mdocr-workspace-1", "work_path":self.work}))?,
                true,
            )?;
        }
        self.ensure_owner()?;
        Ok(lock)
    }

    fn check_unclaimed(&self) -> Result<()> {
        for entry in fs::read_dir(&self.root)? {
            ensure!(
                entry?.path() == self.lock,
                "workspace directory is occupied and has no mdocrr ownership record"
            );
        }
        Ok(())
    }

    pub(crate) fn lock_existing(&self) -> Result<File> {
        self.ensure_live()?;
        self.ensure_owner()?;
        let lock = try_lock(&self.lock)?;
        self.ensure_live()?;
        self.ensure_owner()?;
        Ok(lock)
    }
}

fn open_lock(path: &Path) -> Result<File> {
    owned_path(path)?;
    if path.try_exists()? {
        ensure!(path.is_file(), "workspace lock path must be a file");
    }
    OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(path)
        .context("cannot open a workspace lock")
}

pub(crate) fn try_lock(path: &Path) -> Result<File> {
    let file = open_lock(path)?;
    file.try_lock()
        .context("workspace is active or its lock is unavailable")?;
    Ok(file)
}

pub(crate) fn lock_control(path: &Path) -> Result<File> {
    let file = open_lock(path)?;
    file.lock()
        .context("cannot acquire workspace control lock")?;
    Ok(file)
}

fn read_object(path: &Path) -> Result<Value> {
    let value: Value = read_state(path)?.context("expected a workspace state file")?;
    ensure!(value.is_object(), "invalid workspace state");
    Ok(value)
}

fn read_state<T: DeserializeOwned>(path: &Path) -> Result<Option<T>> {
    owned_path(path)?;
    if !path.try_exists()? {
        return Ok(None);
    }
    ensure!(
        path.is_file(),
        "workspace state must be a file: {}",
        path.display()
    );
    serde_json::from_slice(&fs::read(path)?)
        .with_context(|| format!("read workspace state {}", path.display()))
        .map(Some)
}

fn saved_work_digest(workspace: &Workspace) -> Result<String> {
    for (path, schema) in [
        (&workspace.plan, plan::FROZEN_SCHEMA),
        (&workspace.download, plan::DOWNLOAD_SCHEMA),
    ] {
        if let Some(record) = read_state::<Value>(path)? {
            ensure!(
                record["schema_version"] == schema
                    && record["work_path"].as_str().map(Path::new)
                        == Some(workspace.work.as_path()),
                "workspace state does not match its work plan"
            );
            let digest = record["work_sha256"]
                .as_str()
                .context("missing work digest")?;
            ensure!(valid_digest(digest), "invalid workspace work digest");
            return Ok(digest.to_owned());
        }
    }
    plan::normalize(&workspace.work)?.digest()
}

fn cleanup_state(workspace: &Workspace) -> Result<(String, Cleanup)> {
    let mut cleanup = Cleanup {
        work_path: workspace.work.clone(),
        work_sha256: saved_work_digest(workspace)?,
        unresolved_requests: false,
        outputs: Vec::new(),
    };
    // The journal is authoritative when a process was interrupted before mirroring.
    if let Some(publication) = publication_state(workspace)? {
        cleanup.unresolved_requests = publication.unresolved;
        cleanup.outputs = publication.receipts;
        return Ok((publication.status, cleanup));
    }
    if let Some(record) = read_state::<Value>(&workspace.result)? {
        owned_path(&workspace.plan)?;
        ensure!(workspace.plan.is_file(), "workspace plan is missing");
        let plan = fs::read(&workspace.plan)?;
        ensure!(
            record["plan_sha256"] == sha(&plan),
            "workspace state does not match its plan"
        );
        let state = record["status"]
            .as_str()
            .context("workspace state has no status")?;
        ensure!(
            matches!(state, "running" | "completed" | "partial" | "canceled"),
            "unknown workspace state"
        );
        return Ok((state.to_owned(), cleanup));
    }
    Ok(("accepted".to_owned(), cleanup))
}

fn valid_digest(digest: &str) -> bool {
    digest.len() == 64
        && digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

impl Cleanup {
    fn verify_path(&self, workspace: &Workspace) -> Result<()> {
        ensure!(
            self.work_path == workspace.work,
            "cleanup state belongs to a different workspace"
        );
        Ok(())
    }

    fn verify_digest(&self, digest: &str) -> Result<()> {
        ensure!(
            self.work_sha256 == digest,
            "WORK_PLAN_CHANGED: cleanup is bound to a different work plan"
        );
        Ok(())
    }

    fn verify_outputs(&self, work: &Work, workspace: &Workspace) -> Result<()> {
        let paths: HashMap<_, _> = self
            .outputs
            .iter()
            .map(|receipt| (receipt.path.as_path(), receipt))
            .collect();
        ensure!(
            paths.len() == self.outputs.len(),
            "duplicate cleanup receipts"
        );
        let mut matched = 0;
        for output in work.outputs() {
            owned_path(output)?;
            if let Some(receipt) = paths.get(output) {
                ensure!(
                    output != workspace.work
                        && !output.starts_with(&workspace.root)
                        && !workspace.root.starts_with(output),
                    "cleanup receipt overlaps the work plan or workspace"
                );
                ensure!(
                    valid_digest(&receipt.sha256),
                    "invalid cleanup receipt digest"
                );
                verify_output_if_present(receipt)?;
                matched += 1;
            } else {
                ensure!(
                    !output.try_exists()?,
                    "unowned output: {}",
                    output.display()
                );
            }
        }
        ensure!(
            matched == paths.len(),
            "cleanup receipt is outside its work plan outputs"
        );
        Ok(())
    }
}

fn verify_output_if_present(receipt: &PublicationReceipt) -> Result<()> {
    owned_path(&receipt.path)?;
    if receipt.path.try_exists()? {
        read_bound(
            &receipt.path,
            &receipt.sha256,
            "published OCR output has drifted",
        )?;
    }
    Ok(())
}

fn remove_file(path: &Path) -> Result<()> {
    owned_path(path)?;
    allow_missing(fs::remove_file(path))
        .with_context(|| format!("cannot remove {}", path.display()))
}

fn remove_directory(path: &Path) -> Result<()> {
    owned_path(path)?;
    allow_missing(fs::remove_dir_all(path))
        .with_context(|| format!("cannot remove {}", path.display()))
}

fn allow_missing(result: io::Result<()>) -> io::Result<()> {
    result.or_else(|error| {
        if error.kind() == io::ErrorKind::NotFound {
            Ok(())
        } else {
            Err(error)
        }
    })
}

fn sync_parent(path: &Path) -> Result<()> {
    #[cfg(unix)]
    if let Some(parent) = path.parent() {
        match File::open(parent) {
            Ok(directory) => directory.sync_all()?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
    }
    Ok(())
}

const REPEATED_REQUEST_WARNING: &str =
    "Unresolved provider requests may already have been accepted.";

fn reset_summary(workspace: &Workspace, status: &str, intent: &ResetIntent) -> Value {
    let outputs = intent
        .cleanup
        .outputs
        .iter()
        .map(|receipt| &receipt.path)
        .collect::<Vec<_>>();
    json!({
        "status": status,
        "workspace_path": workspace.root,
        "unresolved_requests": intent.cleanup.unresolved_requests,
        "cleanup": {
            "outputs": outputs,
            "ocr_state": ["journal.json", "result.json", ".cancel.json", "evidence/"],
            "downloads_preserved": !intent.after_purge,
        },
    })
}

pub(crate) fn reset(workspace: &Workspace) -> Result<(u8, Value)> {
    workspace.ensure_owner()?;
    let _execution = try_lock(&workspace.lock)?;
    let _control = lock_control(&workspace.control)?;
    workspace.ensure_owner()?;

    let seal = workspace.read_seal()?;
    if seal
        .as_ref()
        .is_some_and(|seal| seal.status == PurgePhase::Purging)
    {
        return Err(CleanupPending::Purge.into());
    }
    let work = plan::normalize(&workspace.work)?;
    let digest = work.digest()?;
    let marker = workspace.reset_marker();
    let pending = workspace.read_reset()?.filter(|intent| {
        intent.status == ResetPhase::Resetting || seal.is_some() && intent.after_purge
    });
    let mut intent = if let Some(intent) = pending {
        ensure!(
            match &seal {
                Some(seal) => intent.after_purge && intent.cleanup == seal.cleanup,
                None => !intent.after_purge,
            },
            "reset intent and purge seal disagree"
        );
        intent.cleanup.verify_digest(&digest)?;
        intent.cleanup.verify_outputs(&work, workspace)?;
        intent
    } else {
        let after_purge = seal.is_some();
        let cleanup = match seal {
            Some(ref seal) => seal.cleanup.clone(),
            None => cleanup_state(workspace)?.1,
        };
        cleanup.verify_digest(&digest)?;
        cleanup.verify_outputs(&work, workspace)?;
        let intent = ResetIntent {
            status: ResetPhase::Resetting,
            after_purge,
            cleanup,
        };
        let mut message = String::from(
            "Reset OCR state (journal.json, result.json, .cancel.json, and evidence/)?",
        );
        for receipt in &intent.cleanup.outputs {
            use std::fmt::Write as _;
            writeln!(message, "\n  Remove {}", receipt.path.display())
                .expect("formatting a string cannot fail");
        }
        if intent.cleanup.unresolved_requests {
            message.push('\n');
            message.push_str(REPEATED_REQUEST_WARNING);
            message.push_str(" Reset can repeat those requests and their cost.");
        }
        if !confirm(&message)? {
            return Ok((2, reset_summary(workspace, "declined", &intent)));
        }
        atomic(&marker, &json_bytes(&intent)?, false)?;
        intent
    };

    if intent.status == ResetPhase::Resetting {
        for receipt in &intent.cleanup.outputs {
            verify_output_if_present(receipt)?;
            remove_file(&receipt.path)?;
            sync_parent(&receipt.path)?;
        }
        for path in [&workspace.journal, &workspace.result, &workspace.cancel] {
            remove_file(path)?;
        }
        remove_directory(&workspace.evidence)?;
        intent.status = ResetPhase::Ready;
        atomic(&marker, &json_bytes(&intent)?, false)?;
    }
    if intent.after_purge {
        let seal = workspace.purge_marker();
        remove_file(&seal)?;
        sync_parent(&seal)?;
    }
    Ok((0, reset_summary(workspace, "reset", &intent)))
}

fn confirm(message: &str) -> Result<bool> {
    {
        let mut stderr = io::stderr().lock();
        write!(stderr, "{message} [Y/n] ")?;
        stderr.flush()?;
    }
    let mut line = String::new();
    if io::stdin().lock().read_line(&mut line)? == 0 {
        return Ok(false);
    }
    Ok(matches!(
        line.trim().to_ascii_lowercase().as_str(),
        "" | "y" | "yes"
    ))
}

pub(crate) fn purge(workspace: &Workspace, force: bool) -> Result<(u8, Value)> {
    workspace.ensure_owner()?;
    let _execution = try_lock(&workspace.lock)?;
    let _control = lock_control(&workspace.control)?;
    workspace.ensure_owner()?;
    workspace.reset_active()?;
    let marker = workspace.purge_marker();
    let mut seal = if let Some(seal) = workspace.read_seal()? {
        seal
    } else {
        let (state, cleanup) = cleanup_state(workspace)?;
        if !force {
            if !confirm("Purge this mdocrr workspace?")? {
                return Ok((
                    2,
                    json!({"status":"declined", "workspace_path": workspace.root}),
                ));
            }
            if state != "completed" {
                let mut warning = format!(
                    "Warning: this batch is {state}. Purging deletes the journal and evidence and prevents resume."
                );
                if cleanup.unresolved_requests {
                    warning.push(' ');
                    warning.push_str(REPEATED_REQUEST_WARNING);
                }
                if !confirm(&warning)? {
                    return Ok((
                        2,
                        json!({"status":"declined", "workspace_path": workspace.root}),
                    ));
                }
            }
        }
        // Seal before removal so an interrupted purge cannot create a fresh run.
        let seal = PurgeSeal {
            status: PurgePhase::Purging,
            cleanup,
        };
        atomic(&marker, &json_bytes(&seal)?, true)?;
        seal
    };
    // An older seal has no digest or phase evidence for reset. Preserve its
    // run barrier, and let a repeated purge finish its workspace-only cleanup.
    if seal.status == PurgePhase::Purging || seal.cleanup.work_sha256.is_empty() {
        for path in [
            &workspace.download,
            &workspace.plan,
            &workspace.journal,
            &workspace.result,
            &workspace.cancel,
            &workspace.reset_marker(),
        ] {
            remove_file(path)?;
        }
        for path in [&workspace.images, &workspace.evidence] {
            remove_directory(path)?;
        }
        seal.status = PurgePhase::Purged;
        atomic(&marker, &json_bytes(&seal)?, false)?;
    }
    Ok((
        0,
        json!({"status":"purged", "workspace_path":workspace.root}),
    ))
}
