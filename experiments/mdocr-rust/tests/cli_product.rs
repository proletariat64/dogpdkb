use serde_json::Value;
use std::fs;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Output, Stdio};

const MDOCR: &str = env!("CARGO_BIN_EXE_mdocr");

fn command(root: &Path) -> Command {
    let mut command = Command::new(MDOCR);
    command
        .current_dir(root)
        .env_clear()
        .env("PATH", "/nonexistent");
    command
}

fn run(root: &Path, args: &[&str]) -> Output {
    command(root).args(args).output().unwrap()
}

fn result(output: &Output) -> Value {
    serde_json::from_slice(&output.stdout)
        .unwrap_or_else(|_| panic!("stdout: {:?}; stderr: {:?}", output.stdout, output.stderr))
}

fn with_confirmation(root: &Path, args: &[&str], reply: &[u8]) -> Output {
    let mut child = command(root)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(reply).unwrap();
    child.wait_with_output().unwrap()
}

fn draft(root: &Path) {
    fs::write(root.join("input.md"), "Body without images.\n").unwrap();
    let output = run(
        root,
        &[
            "draft",
            "--input-single",
            "input.md",
            "--output-single",
            "output.md",
            "--plan",
            "work.json",
        ],
    );
    assert!(output.status.success(), "{:?}", output);
}

#[test]
fn publishes_english_help_and_rejects_undocumented_command_forms() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    for args in [&["--help"][..], &["accept", "--help"], &["help", "all"]] {
        let output = run(root, args);
        assert!(output.status.success(), "{:?}", output);
        assert!(output.stderr.is_empty(), "{:?}", output);
        let text = String::from_utf8(output.stdout).unwrap();
        assert!(text.contains("plan"), "{text}");
    }
    assert_eq!(run(root, &["plan", "--help"]).status.code(), Some(64));
    assert_eq!(
        run(root, &["run", "--work-plan", "work.json"])
            .status
            .code(),
        Some(64)
    );
    assert_eq!(fs::read_dir(root).unwrap().count(), 0);
}

#[test]
fn explicit_completed_purge_prompts_once_and_keeps_the_published_output() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    draft(root);
    assert!(!root.join("work.mdocr").exists());
    let output = run(root, &["run", "--plan", "work.json"]);
    assert!(output.status.success(), "{:?}", output);
    assert_eq!(result(&output)["status"], "completed");
    assert_eq!(
        std::str::from_utf8(&output.stdout).unwrap().lines().count(),
        1
    );
    assert!(root.join("work.mdocr/result.json").exists());

    fs::write(
        root.join("work.mdocr/operator-note"),
        b"outside purge scope",
    )
    .unwrap();
    let purge = with_confirmation(root, &["purge", "--plan", "work.json"], b"\n");
    assert!(purge.status.success(), "{:?}", purge);
    assert_eq!(result(&purge)["status"], "purged");
    assert_eq!(
        String::from_utf8_lossy(&purge.stderr)
            .matches("Y/n")
            .count(),
        1
    );
    assert!(!root.join("work.mdocr/result.json").exists());
    assert!(root.join("work.mdocr/operator-note").exists());
    assert!(root.join("work.json").exists());
    assert_eq!(
        fs::read(root.join("output.md")).unwrap(),
        b"Body without images.\n"
    );

    let rerun = run(root, &["run", "--plan", "work.json"]);
    assert_eq!(rerun.status.code(), Some(1));
    assert_eq!(result(&rerun)["status"], "purged");
    assert!(!root.join("work.mdocr/result.json").exists());
}

#[test]
fn purge_of_an_accepted_workspace_needs_two_confirmations_or_uppercase_y() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    draft(root);
    let accepted = run(root, &["accept", "--plan", "work.json"]);
    assert!(accepted.status.success(), "{:?}", accepted);
    let frozen = root.join("work.mdocr/plan.json");
    let bytes = fs::read(&frozen).unwrap();

    let declined = with_confirmation(root, &["purge", "--plan", "work.json"], b"y\nn\n");
    assert_eq!(declined.status.code(), Some(2), "{:?}", declined);
    assert_eq!(result(&declined)["status"], "declined");
    assert_eq!(
        String::from_utf8_lossy(&declined.stderr)
            .matches("Y/n")
            .count(),
        2
    );
    assert_eq!(fs::read(&frozen).unwrap(), bytes);

    let forced = run(root, &["purge", "--plan", "work.json", "-Y"]);
    assert!(forced.status.success(), "{:?}", forced);
    assert!(!String::from_utf8_lossy(&forced.stderr).contains("Y/n"));
    assert!(!frozen.exists());
}

#[test]
fn run_and_purge_leave_an_unowned_workspace_directory_alone() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    draft(root);
    let workspace = root.join("work.mdocr");
    fs::create_dir(&workspace).unwrap();
    fs::write(workspace.join("private"), "operator data").unwrap();

    let execute = run(root, &["run", "--plan", "work.json"]);
    assert_eq!(execute.status.code(), Some(1), "{:?}", execute);
    let purge = run(root, &["purge", "--plan", "work.json", "-Y"]);
    assert_eq!(purge.status.code(), Some(1), "{:?}", purge);
    assert_eq!(fs::read_dir(&workspace).unwrap().count(), 1);
    assert_eq!(
        fs::read(workspace.join("private")).unwrap(),
        b"operator data"
    );
}

#[test]
fn a_neighboring_default_is_frozen_across_working_directories_and_later_changes() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let batch = root.join("batch");
    fs::create_dir(&batch).unwrap();
    draft(&batch);
    fs::write(
        batch.join("default.toml"),
        "mode = \"paddle\"\nrequest_budget = 0\n",
    )
    .unwrap();
    fs::write(root.join("default.toml"), "should_not_be_loaded = true\n").unwrap();
    let accepted = run(root, &["accept", "--plan", "batch/work.json"]);
    assert!(accepted.status.success(), "{:?}", accepted);
    let frozen = batch.join("work.mdocr/plan.json");
    let bytes = fs::read(&frozen).unwrap();
    let plan: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(plan["settings"]["mode"], "paddle");
    assert_eq!(plan["settings"]["request_budget"], 0);

    fs::write(batch.join("default.toml"), "mode = \"llm\"\n").unwrap();
    let mismatch = run(
        root,
        &[
            "run",
            "--plan",
            "batch/work.json",
            "--config",
            "batch/default.toml",
        ],
    );
    assert_eq!(mismatch.status.code(), Some(1), "{:?}", mismatch);
    assert!(!batch.join("work.mdocr/result.json").exists());
    let resumed = run(root, &["run", "--plan", "batch/work.json"]);
    assert!(resumed.status.success(), "{:?}", resumed);
    assert_eq!(fs::read(frozen).unwrap(), bytes);
}
