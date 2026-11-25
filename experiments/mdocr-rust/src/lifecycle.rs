use crate::artifacts::{atomic, json_bytes};
use crate::workspace::Workspace;
use crate::workspace::lock_control;
use crate::{artifacts, plan, progress, runner, workspace};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::fs;
use std::path::{Path, PathBuf};

const CANCEL_SCHEMA: &str = "mdocr-cancel-1";

fn accept(workspace: &Workspace, explicit: Option<&Path>, repair_canceled: bool) -> Result<Value> {
    artifacts::owned_path(&workspace.plan)?;
    let fresh = !workspace.plan.try_exists()?;
    let summary = plan::accept(workspace, explicit, repair_canceled)?;
    let counts = &summary["counts"];
    progress::emit(
        "accepted",
        format_args!(
            "phase=accept new={fresh} documents={} selected={} staged={} failed={}",
            counts["documents"], counts["selected"], counts["staged"], counts["failed"]
        ),
    );
    Ok(summary)
}

pub(crate) fn accept_batch(plan: &Path, config: Option<&Path>) -> Result<(u8, Value)> {
    let workspace = Workspace::new(plan)?;
    let _lock = workspace.create_and_lock()?;
    let mut output = accept(&workspace, config, true)?;
    output["status"] = json!("accepted");
    Ok((0, output))
}

pub(crate) fn run(
    plan: &Path,
    config: Option<&Path>,
    resume: bool,
    retry_failed: bool,
) -> Result<(u8, Value)> {
    let workspace = Workspace::new(plan)?;
    let _lock = workspace.create_and_lock()?;
    let frozen = workspace.plan.try_exists()?;
    if retry_failed {
        ensure!(frozen, "no accepted batch exists to retry");
    }
    if resume {
        ensure!(
            frozen || workspace.download.try_exists()?,
            "no accepted batch or download checkpoint exists to resume"
        );
    }
    accept(&workspace, config, resume)?;
    runner::run(&workspace, resume, retry_failed)
}

pub(crate) fn cancel(plan: &Path) -> Result<(u8, Value)> {
    let workspace = Workspace::new(plan)?;
    workspace.ensure_live()?;
    workspace.ensure_owner()?;
    runner::cancel(&workspace)
}

pub(crate) fn assemble(plan: &Path, output_dir: PathBuf) -> Result<(u8, Value)> {
    let workspace = Workspace::new(plan)?;
    let _lock = workspace.lock_existing()?;
    runner::assemble(&workspace, output_dir)
}

pub(crate) fn reset(plan: &Path) -> Result<(u8, Value)> {
    workspace::reset(&Workspace::new(plan)?)
}

pub(crate) fn purge(plan: &Path, yes: bool) -> Result<(u8, Value)> {
    workspace::purge(&Workspace::new(plan)?, yes)
}

pub(crate) fn cancel_requested(workspace: &Workspace, digest: &str) -> Result<bool> {
    artifacts::owned_path(&workspace.cancel)?;
    let data = match fs::read(&workspace.cancel) {
        Ok(data) => data,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error.into()),
    };
    let marker: Value = serde_json::from_slice(&data)?;
    ensure!(
        marker["schema_version"] == CANCEL_SCHEMA && marker["plan_sha256"] == digest,
        "cancel marker is bound to a different frozen plan"
    );
    marker["requested"]
        .as_bool()
        .context("invalid cancel marker state")
}

pub(crate) fn write_cancel(workspace: &Workspace, digest: &str, requested: bool) -> Result<()> {
    let marker = json!({
        "schema_version": CANCEL_SCHEMA,
        "plan_sha256": digest,
        "requested": requested,
    });
    atomic(&workspace.cancel, &json_bytes(&marker)?, false)
}

pub(crate) fn frozen_batch_canceled(workspace: &Workspace, digest: &str) -> Result<bool> {
    let _control = lock_control(&workspace.control)?;
    if cancel_requested(workspace, digest)? {
        return Ok(true);
    }
    Ok(runner::journal_status(workspace, digest)?.is_some_and(|status| status == "canceled"))
}
