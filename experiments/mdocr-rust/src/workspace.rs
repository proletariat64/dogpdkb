use crate::artifacts::{atomic, json_bytes, owned_path, sha};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
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

    pub(crate) fn ensure_live(&self) -> Result<()> {
        let marker = self.purge_marker();
        owned_path(&marker)?;
        if marker.try_exists()? {
            return Err(Purged.into());
        }
        Ok(())
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
    owned_path(path)?;
    ensure!(path.is_file(), "expected a workspace state file");
    let value: Value = serde_json::from_slice(&fs::read(path)?)?;
    ensure!(value.is_object(), "invalid workspace state");
    Ok(value)
}

fn state_before_purge(workspace: &Workspace) -> Result<(String, bool)> {
    // The journal is authoritative when a process was interrupted before mirroring.
    if let Some(publication) = crate::runner::publication_state(workspace)? {
        return Ok((publication.status, publication.unresolved));
    }
    owned_path(&workspace.result)?;
    if workspace.result.try_exists()? {
        let record = read_object(&workspace.result)?;
        let plan = fs::read(&workspace.plan).context("workspace plan is missing")?;
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
        return Ok((state.to_owned(), false));
    }
    Ok(("accepted".to_owned(), false))
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
    let marker = workspace.purge_marker();
    owned_path(&marker)?;
    if !marker.try_exists()? {
        let (state, unresolved) = if force {
            (None, false)
        } else {
            let (state, unresolved) = state_before_purge(workspace)?;
            (Some(state), unresolved)
        };
        if !force {
            if !confirm("Purge this mdocrr workspace?")? {
                return Ok((
                    2,
                    json!({"status":"declined", "workspace_path": workspace.root}),
                ));
            }
            if let Some(state) = state.as_deref().filter(|state| *state != "completed") {
                let mut warning = format!(
                    "Warning: this batch is {state}. Purging deletes the journal and evidence and prevents resume."
                );
                if unresolved {
                    warning
                        .push_str(" Unresolved provider requests may already have been accepted.");
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
        atomic(
            &marker,
            &json_bytes(
                &json!({"status":"purged", "work_path":workspace.work, "previous_status":state}),
            )?,
            true,
        )?;
    } else {
        let recorded = read_object(&marker)?;
        ensure!(
            recorded["status"] == "purged"
                && recorded["work_path"].as_str().map(Path::new) == Some(workspace.work.as_path()),
            "invalid workspace purge marker"
        );
    }

    for path in [
        &workspace.plan,
        &workspace.journal,
        &workspace.result,
        &workspace.cancel,
    ] {
        owned_path(path)?;
        match fs::remove_file(path) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error).context("cannot remove a workspace state file"),
        }
    }
    for path in [&workspace.images, &workspace.evidence] {
        owned_path(path)?;
        match fs::remove_dir_all(path) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(error).context("cannot remove a workspace artifact directory");
            }
        }
    }
    #[cfg(unix)]
    File::open(&workspace.root)?.sync_all()?;
    Ok((
        0,
        json!({"status":"purged", "workspace_path":workspace.root}),
    ))
}
