use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Output, Stdio};

const MDOCRR: &str = env!("CARGO_BIN_EXE_mdocrr");

fn command(root: &Path) -> Command {
    let mut command = Command::new(MDOCRR);
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
        assert!(text.contains("mdocrr"), "{text}");
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
fn publication_receipt_recovers_both_sides_of_output_creation() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().canonicalize().unwrap();
    let root = root.as_path();
    draft(root);
    let run_args = ["run", "--plan", "work.json"];
    let first = run(root, &run_args);
    assert!(first.status.success(), "{first:?}");

    let journal_path = root.join("work.mdocr/journal.json");
    let result_path = root.join("work.mdocr/result.json");
    let output_path = root.join("output.md");
    let expected_output = b"Body without images.\n";
    let mut journal: Value = serde_json::from_slice(&fs::read(&journal_path).unwrap()).unwrap();
    let receipt = &journal["publication_receipts"][root
        .join("input.md")
        .canonicalize()
        .unwrap()
        .to_str()
        .unwrap()];
    assert_eq!(
        receipt,
        &serde_json::json!({
            "path": output_path,
            "sha256": format!("{:x}", Sha256::digest(expected_output)),
        })
    );

    journal["status"] = "running".into();
    journal["documents"] = serde_json::json!([]);
    let interrupted_journal = serde_json::to_vec(&journal).unwrap();
    for output_already_written in [false, true] {
        fs::remove_file(&result_path).unwrap();
        fs::write(&journal_path, &interrupted_journal).unwrap();
        if !output_already_written {
            fs::remove_file(&output_path).unwrap();
        }
        let recovered = run(root, &run_args);
        assert!(recovered.status.success(), "{recovered:?}");
        assert_eq!(result(&recovered)["status"], "completed");
        assert_eq!(result(&recovered)["counts"]["requests"], 0);
        assert_eq!(fs::read(&output_path).unwrap(), expected_output);
        assert!(result_path.exists());
    }
}

#[test]
fn publication_refuses_changed_or_unowned_outputs() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    draft(root);
    let run_args = ["run", "--plan", "work.json"];
    let first = run(root, &run_args);
    assert!(first.status.success(), "{first:?}");
    let output = root.join("output.md");
    let original = fs::read(&output).unwrap();
    let journal_path = root.join("work.mdocr/journal.json");
    let saved = fs::read(&journal_path).unwrap();

    fs::write(&output, b"Operator change.\n").unwrap();
    let drift = run(root, &run_args);
    assert_eq!(drift.status.code(), Some(1), "{drift:?}");
    assert!(String::from_utf8_lossy(&drift.stderr).contains("OCR artifact has drifted"));
    assert_eq!(fs::read(&output).unwrap(), b"Operator change.\n");
    assert_eq!(fs::read(&journal_path).unwrap(), saved);

    fs::write(&output, &original).unwrap();
    let mut journal: Value = serde_json::from_slice(&saved).unwrap();
    let document_id = root.join("input.md").canonicalize().unwrap();
    journal["publication_receipts"][document_id.to_str().unwrap()]["path"] =
        serde_json::json!(document_id);
    fs::write(&journal_path, serde_json::to_vec(&journal).unwrap()).unwrap();
    let wrong_path = run(root, &run_args);
    assert_eq!(wrong_path.status.code(), Some(1), "{wrong_path:?}");
    assert!(
        String::from_utf8_lossy(&wrong_path.stderr)
            .contains("publication receipt does not match its frozen document")
    );
    assert_eq!(fs::read(&output).unwrap(), original);
    assert_eq!(fs::read(document_id).unwrap(), original);

    journal["publication_receipts"] = serde_json::json!({});
    let without_receipt = serde_json::to_vec(&journal).unwrap();
    fs::write(&journal_path, &without_receipt).unwrap();
    let unowned = run(root, &run_args);
    assert_eq!(unowned.status.code(), Some(1), "{unowned:?}");
    assert!(
        String::from_utf8_lossy(&unowned.stderr).contains("preexisting unowned document output")
    );
    assert_eq!(fs::read(output).unwrap(), original);
    assert_eq!(fs::read(journal_path).unwrap(), without_receipt);
}

#[test]
fn reset_requires_consent_and_allows_the_same_owned_outputs_to_be_published_again() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    draft(root);
    assert!(run(root, &["run", "--plan", "work.json"]).status.success());
    let workspace = root.join("work.mdocr");
    let output = root.join("output.md");
    let frozen = fs::read(workspace.join("plan.json")).unwrap();
    let download = fs::read(workspace.join("download.json")).unwrap();
    let journal = fs::read(workspace.join("journal.json")).unwrap();

    let declined = with_confirmation(root, &["reset", "--plan", "work.json"], b"n\n");
    assert_eq!(declined.status.code(), Some(2));
    assert_eq!(result(&declined)["status"], "declined");
    assert_eq!(fs::read(workspace.join("journal.json")).unwrap(), journal);
    assert!(output.exists());
    assert!(!workspace.join("reset.json").exists());

    let reset = with_confirmation(root, &["reset", "--plan", "work.json"], b"y\n");
    assert!(reset.status.success(), "{reset:?}");
    assert_eq!(result(&reset)["status"], "reset");
    assert_eq!(result(&reset)["unresolved_requests"], false);
    assert!(
        String::from_utf8_lossy(&reset.stderr).contains(output.to_str().unwrap()),
        "{reset:?}"
    );
    assert!(!output.exists());
    assert!(!workspace.join("journal.json").exists());
    assert!(!workspace.join("result.json").exists());
    assert!(workspace.join("reset.json").is_file());
    assert_eq!(fs::read(workspace.join("plan.json")).unwrap(), frozen);
    assert_eq!(fs::read(workspace.join("download.json")).unwrap(), download);

    assert!(run(root, &["run", "--plan", "work.json"]).status.success());
    assert_eq!(fs::read(output).unwrap(), b"Body without images.\n");
    assert!(!workspace.join("reset.json").exists());
    assert_eq!(
        fs::read(root.join("input.md")).unwrap(),
        b"Body without images.\n"
    );
}

#[test]
fn reset_preserves_previous_ocr_when_the_retained_source_has_changed() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    draft(root);
    assert!(run(root, &["run", "--plan", "work.json"]).status.success());
    let workspace = root.join("work.mdocr");
    let ledger = fs::read(workspace.join("journal.json")).unwrap();
    let output = fs::read(root.join("output.md")).unwrap();
    fs::write(root.join("input.md"), b"Edited since publication.\n").unwrap();

    let reset = with_confirmation(root, &["reset", "--plan", "work.json"], b"y\n");
    assert_eq!(reset.status.code(), Some(1), "{reset:?}");
    assert!(String::from_utf8_lossy(&reset.stderr).contains("SOURCE_DRIFT"));
    assert_eq!(fs::read(workspace.join("journal.json")).unwrap(), ledger);
    assert_eq!(fs::read(root.join("output.md")).unwrap(), output);
    assert!(!workspace.join("reset.json").exists());

    assert!(
        run(root, &["purge", "--plan", "work.json", "-Y"])
            .status
            .success()
    );
    assert!(
        with_confirmation(root, &["reset", "--plan", "work.json"], b"y\n")
            .status
            .success()
    );
    assert!(run(root, &["run", "--plan", "work.json"]).status.success());
    assert_eq!(
        fs::read(root.join("output.md")).unwrap(),
        b"Edited since publication.\n"
    );
}

#[test]
fn interrupted_reset_blocks_execution_and_finishes_from_saved_cleanup_targets() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    draft(root);
    assert!(run(root, &["run", "--plan", "work.json"]).status.success());
    let workspace = root.join("work.mdocr");
    let cancel = workspace.join(".cancel.json");
    fs::create_dir(&cancel).unwrap();
    let stopped = with_confirmation(root, &["reset", "--plan", "work.json"], b"y\n");
    assert_eq!(stopped.status.code(), Some(1));
    assert!(workspace.join("reset.json").is_file());
    assert!(!root.join("output.md").exists());

    let blocked = run(root, &["run", "--plan", "work.json"]);
    assert_eq!(blocked.status.code(), Some(1));
    assert_eq!(result(&blocked)["reason_code"], "WORKSPACE_RESET_PENDING");
    let blocked_purge = run(root, &["purge", "--plan", "work.json", "-Y"]);
    assert_eq!(
        result(&blocked_purge)["reason_code"],
        "WORKSPACE_RESET_PENDING"
    );

    fs::remove_dir(cancel).unwrap();
    let resumed = run(root, &["reset", "--plan", "work.json"]);
    assert!(resumed.status.success(), "{resumed:?}");
    assert_eq!(result(&resumed)["status"], "reset");
    assert!(run(root, &["run", "--plan", "work.json"]).status.success());
    assert!(root.join("output.md").is_file());
}

#[test]
fn interrupted_purge_keeps_its_receipts_and_requires_finishing_before_reset() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    draft(root);
    assert!(run(root, &["run", "--plan", "work.json"]).status.success());
    let workspace = root.join("work.mdocr");
    let images = workspace.join("images");
    if images.exists() {
        fs::remove_dir_all(&images).unwrap();
    }
    fs::write(&images, b"cleanup obstruction").unwrap();
    let stopped = run(root, &["purge", "--plan", "work.json", "-Y"]);
    assert_eq!(stopped.status.code(), Some(1));
    assert!(!workspace.join("journal.json").exists());
    assert!(root.join("output.md").is_file());
    let blocked = run(root, &["reset", "--plan", "work.json"]);
    assert_eq!(blocked.status.code(), Some(1));
    assert_eq!(result(&blocked)["reason_code"], "WORKSPACE_PURGE_PENDING");

    fs::remove_file(images).unwrap();
    let purge = run(root, &["purge", "--plan", "work.json", "-Y"]);
    assert!(purge.status.success(), "{purge:?}");
    let seal = workspace.join(".purged.json");
    let saved = fs::read(&seal).unwrap();
    assert!(
        run(root, &["purge", "--plan", "work.json", "-Y"])
            .status
            .success()
    );
    assert_eq!(fs::read(&seal).unwrap(), saved);
    assert!(root.join("output.md").is_file());
    let reset = with_confirmation(root, &["reset", "--plan", "work.json"], b"y\n");
    assert!(reset.status.success(), "{reset:?}");
    assert!(!root.join("output.md").exists());

    // Re-create the durable state immediately before a successful reset unseals.
    fs::write(&seal, saved).unwrap();
    assert_eq!(
        result(&run(root, &["run", "--plan", "work.json"]))["reason_code"],
        "WORKSPACE_PURGED"
    );
    assert!(
        run(root, &["reset", "--plan", "work.json"])
            .status
            .success()
    );
    assert!(!seal.exists());
    assert!(run(root, &["run", "--plan", "work.json"]).status.success());
}

#[test]
fn reset_refuses_modified_and_unowned_outputs_before_cleanup() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    draft(root);
    assert!(run(root, &["run", "--plan", "work.json"]).status.success());
    let output = root.join("output.md");
    let workspace = root.join("work.mdocr");
    let journal = fs::read(workspace.join("journal.json")).unwrap();
    fs::write(&output, "replacement").unwrap();
    let reset = with_confirmation(root, &["reset", "--plan", "work.json"], b"y\n");
    assert_eq!(reset.status.code(), Some(1));
    assert_eq!(fs::read(workspace.join("journal.json")).unwrap(), journal);
    assert!(!workspace.join("reset.json").exists());

    assert!(
        run(root, &["purge", "--plan", "work.json", "-Y"])
            .status
            .success()
    );
    let reset = with_confirmation(root, &["reset", "--plan", "work.json"], b"y\n");
    assert_eq!(reset.status.code(), Some(1));
    assert_eq!(fs::read(&output).unwrap(), b"replacement");
    assert!(workspace.join(".purged.json").exists());

    let unowned = tempfile::tempdir().unwrap();
    let root = unowned.path();
    draft(root);
    assert!(
        run(root, &["accept", "--plan", "work.json"])
            .status
            .success()
    );
    fs::write(root.join("output.md"), "Body without images.\n").unwrap();
    let reset = with_confirmation(root, &["reset", "--plan", "work.json"], b"y\n");
    assert_eq!(reset.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&reset.stderr).contains("unowned output"));
    assert!(root.join("output.md").is_file());
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
fn purge_keeps_the_saved_binding_if_inputs_disappear_after_reset() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    draft(root);
    let args = &["purge", "--plan", "work.json", "-Y"];
    assert!(run(root, &["run", "--plan", "work.json"]).status.success());
    assert!(run(root, args).status.success());
    assert!(
        with_confirmation(root, &["reset", "--plan", "work.json"], b"y\n")
            .status
            .success()
    );
    fs::remove_file(root.join("input.md")).unwrap();
    let purge = run(root, args);
    assert!(purge.status.success(), "{purge:?}");
    assert_eq!(result(&purge)["status"], "purged");
    fs::write(root.join("input.md"), b"Recreated source.\n").unwrap();
    assert!(
        with_confirmation(root, &["reset", "--plan", "work.json"], b"y\n")
            .status
            .success()
    );
    assert!(run(root, &["run", "--plan", "work.json"]).status.success());
}

#[test]
fn purge_cleans_an_owned_unaccepted_batch_without_authorizing_reset() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    draft(root);
    let work = root.join("work.json");
    let valid = fs::read(&work).unwrap();
    fs::write(&work, b"{").unwrap();
    assert!(!run(root, &["run", "--plan", "work.json"]).status.success());
    let purge = run(root, &["purge", "--plan", "work.json", "-Y"]);
    assert!(purge.status.success(), "{purge:?}");
    assert_eq!(result(&purge)["status"], "purged");
    assert_eq!(fs::read(&work).unwrap(), b"{");

    fs::write(&work, valid).unwrap();
    fs::write(root.join("output.md"), b"Operator output").unwrap();
    let reset = with_confirmation(root, &["reset", "--plan", "work.json"], b"y\n");
    assert_eq!(reset.status.code(), Some(1), "{reset:?}");
    assert_eq!(
        fs::read(root.join("output.md")).unwrap(),
        b"Operator output"
    );
    assert_eq!(
        result(&run(root, &["run", "--plan", "work.json"]))["status"],
        "purged"
    );
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
