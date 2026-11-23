use serde_json::{Value, json};
use std::fs;
use std::process::Command;

#[test]
fn semantic_batch_publishes_ordered_scoped_results_and_pinned_evidence() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("old.md"), "# Limits\n\nUSD 10\n").unwrap();
    fs::write(dir.path().join("new.md"), "# Limits\n\nUSD 20\n").unwrap();
    let snapshot = |path| {
        json!({"path": path, "content_kind":"raw",
            "metadata":{"name":"Limits","logical_path":"Rules","source_ref":"limits"}})
    };
    let work = json!({
        "schema_version":"mdcompare-work-1",
        "operation":"semantic",
        "documents":[
            {"knowledge_base":"z","document_id":"42",
             "baseline":snapshot("old.md"),"incoming":snapshot("new.md")},
            {"knowledge_base":"a","document_id":"42",
             "baseline":snapshot("new.md"),"incoming":snapshot("new.md")},
            {"knowledge_base":"b","document_id":"42",
             "baseline":null,"incoming":snapshot("missing.md")}
        ]
    });
    let plan = dir.path().join("work.json");
    fs::write(&plan, work.to_string()).unwrap();
    let run = || {
        Command::new(env!("CARGO_BIN_EXE_mdcompare"))
            .args(["run", "--plan"])
            .arg(&plan)
            .env_clear()
            .output()
            .unwrap()
    };
    let output = run();
    assert_eq!(
        output.status.code(),
        Some(2),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let summary: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(summary["state"], "partial");
    let root = dir.path().join("work.mdcompare");
    let result: Value =
        serde_json::from_slice(&fs::read(root.join("result.json")).unwrap()).unwrap();
    let rows = result["results"].as_array().unwrap();
    assert_eq!(rows.len(), 3);
    assert_eq!(rows[0]["knowledge_base"], "z");
    assert_eq!(rows[0]["semantic_status"], "CHANGED");
    assert_eq!(rows[1]["knowledge_base"], "a");
    assert_eq!(rows[1]["action"], "SKIP");
    assert_eq!(rows[2]["action"], "DEFER");
    assert!(root.join("evidence/000000.json").is_file());
    assert!(root.join("commit.json").is_file());
    fs::remove_file(dir.path().join("old.md")).unwrap();
    let replay = run();
    assert_eq!(replay.status.code(), Some(2));
    let replay: Value = serde_json::from_slice(&replay.stdout).unwrap();
    assert_eq!(replay["comparison_sha256"], summary["comparison_sha256"]);
}

fn invoke(plan: &std::path::Path, command: &str, extra: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_mdcompare"))
        .args([command, "--plan"])
        .arg(plan)
        .args(extra)
        .env_clear()
        .output()
        .unwrap()
}

fn stdout(output: &std::process::Output, code: i32) -> Value {
    assert_eq!(
        output.status.code(),
        Some(code),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).unwrap()
}

#[test]
fn route_reuses_pinned_scores_and_rejects_tampered_evidence() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    fs::write(root.join("old.md"), "# Limits\n\nSet a limit of 10.\n").unwrap();
    fs::write(root.join("new.md"), "# Limits\n\nSet a limit of 20.\n").unwrap();
    let plan = root.join("work.json");
    let draft = Command::new(env!("CARGO_BIN_EXE_mdcompare"))
        .current_dir(root)
        .args(["draft", "old.md", "new.md", "--plan", "work.json"])
        .output()
        .unwrap();
    stdout(&draft, 0);
    let result = stdout(&invoke(&plan, "run", &[]), 0);
    let index: Value =
        serde_json::from_slice(&fs::read(root.join("work.mdcompare/diff-index.json")).unwrap())
            .unwrap();
    let evidence = &index["documents"][0];
    let scores = root.join("scores.json");
    fs::write(
        &scores,
        json!({
            "schema_version":"mdcompare-external-scores-1",
            "comparison_sha256":result["comparison_sha256"],
            "scores":[{
                "knowledge_base":evidence["knowledge_base"],"document_id":evidence["document_id"],
                "evidence_sha256":evidence["sha256"],"score":3,"ambiguous":true
            }]
        })
        .to_string(),
    )
    .unwrap();
    let scores_path = scores.to_str().unwrap();
    let route = |extra: &[&str]| {
        let mut args = vec!["--scores", scores_path];
        args.extend(extra);
        invoke(&plan, "route", &args)
    };
    let first = stdout(&route(&[]), 0);
    let read_route = |summary: &Value| {
        serde_json::from_slice::<Value>(
            &fs::read(summary["artifacts"]["result"].as_str().unwrap()).unwrap(),
        )
        .unwrap()
    };
    assert_eq!(read_route(&first)["results"][0]["action"], "SKIP");
    let config = root.join("policy.toml");
    fs::write(
        &config,
        "[route]\ncompile_above=3\ncompile_ambiguous=true\n",
    )
    .unwrap();
    let second = stdout(&route(&["--config", config.to_str().unwrap()]), 0);
    assert_eq!(read_route(&second)["results"][0]["action"], "COMPILE");
    assert_ne!(first["artifacts"]["result"], second["artifacts"]["result"]);
    assert_eq!(read_route(&first)["results"][0]["action"], "SKIP");
    fs::write(
        root.join("work.mdcompare")
            .join(evidence["evidence"].as_str().unwrap()),
        b"{}",
    )
    .unwrap();
    assert_eq!(stdout(&route(&[]), 1)["reason_code"], "binding_invalid");
}

#[test]
fn accepted_input_drift_prevents_a_new_commit() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path();
    fs::write(path.join("old.md"), "original").unwrap();
    fs::write(path.join("new.md"), "new").unwrap();
    let plan = path.join("work.json");
    let draft = Command::new(env!("CARGO_BIN_EXE_mdcompare"))
        .current_dir(path)
        .args(["draft", "old.md", "new.md", "--plan", "work.json"])
        .output()
        .unwrap();
    stdout(&draft, 0);
    assert_eq!(
        stdout(&invoke(&plan, "accept", &[]), 0)["state"],
        "accepted"
    );
    fs::write(path.join("old.md"), "changed after acceptance").unwrap();
    assert_eq!(
        stdout(&invoke(&plan, "run", &[]), 1)["reason_code"],
        "input_drift"
    );
    assert!(!path.join("work.mdcompare/commit.json").exists());
}
