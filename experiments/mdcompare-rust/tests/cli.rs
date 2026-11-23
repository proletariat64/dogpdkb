use std::process::{Command, Output};

fn run(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_mdcomparer"))
        .env_clear()
        .args(args)
        .output()
        .expect("execute mdcomparer")
}

#[test]
fn help_grows_from_workflow_to_command_to_full_contract() {
    let root = run(&["--help"]);
    let run_help = run(&["run", "--help"]);
    let full = run(&["help", "all"]);
    for output in [&root, &run_help, &full] {
        assert!(output.status.success(), "{:?}", output);
        assert!(output.stderr.is_empty());
    }
    let root = String::from_utf8(root.stdout).unwrap();
    let run_help = String::from_utf8(run_help.stdout).unwrap();
    let full = String::from_utf8(full.stdout).unwrap();
    assert!(root.contains("mdcomparer draft"));
    assert!(root.contains("mdcomparer run --plan"));
    assert!(run_help.contains("--config"));
    assert!(run_help.contains("Exit codes"));
    assert!(full.contains("mdcompare-work-1"));
    assert!(full.contains("mdcompare-summary-1"));
    assert!(full.contains("mdcompare-score-work-1"));
    assert!(!root.contains("mdcompare-score-work-1"));
}

#[test]
fn removed_commands_and_options_are_usage_errors() {
    for args in [
        vec!["compare"],
        vec!["knowledge"],
        vec!["semantic"],
        vec!["catalog"],
        vec!["semantic-score"],
        vec!["semantic-route"],
        vec![
            "run",
            "--plan",
            "work.json",
            "--baseline",
            "PRIVATE-MARKDOWN",
        ],
        vec!["run", "--help", "--full"],
        vec!["doctor", "--mode", "offline"],
    ] {
        let output = run(&args);
        assert_eq!(output.status.code(), Some(64), "{args:?} {output:?}");
        assert!(output.stdout.is_empty(), "{args:?}");
        let stderr = String::from_utf8(output.stderr).unwrap();
        assert!(stderr.contains("mdcomparer --help"), "{args:?}");
        assert!(!stderr.contains("PRIVATE-MARKDOWN"));
    }
}

#[test]
fn doctor_reports_summary_and_progress_under_an_empty_environment() {
    let output = run(&["doctor"]);
    assert!(output.status.success(), "{output:?}");
    let summary: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(summary["schema_version"], "mdcompare-summary-1");
    assert_eq!(summary["command"], "doctor");
    assert_eq!(summary["state"], "complete");
    assert!(summary["reason_code"].is_null());
    assert_eq!(summary["counts"]["checks"], 3);
    assert_eq!(summary["diagnostics"]["llm"]["status"], "not_configured");
    assert_eq!(summary["diagnostics"]["llm"]["provider"], "openai");
    assert_eq!(summary["diagnostics"]["llm"]["inference"], "not_checked");
    let stderr = String::from_utf8(output.stderr).unwrap();
    assert!(stderr.contains("timestamp_ms="));
    assert!(stderr.contains("command=doctor event=progress"));
    assert!(stderr.contains("command=doctor event=finish"));
    assert!(stderr.contains("reason=complete"));
}

#[test]
fn pretty_doctor_indents_the_same_json_and_reports_saved_oauth_state() {
    let directory = tempfile::tempdir().unwrap();
    let config = directory.path().join("scoring.toml");
    std::fs::write(
        &config,
        "[score]\nrequest_budget = 2\noauth_file = 'oauth.json'\n",
    )
    .unwrap();
    let oauth = directory.path().join("oauth.json");
    std::fs::write(&oauth, r#"{"openai":{"type":"oauth","access":"PRIVATE-TOKEN","refresh":"PRIVATE-REFRESH","expires":1}}"#).unwrap();
    let args = ["doctor", "--config", config.to_str().unwrap()];
    let compact = run(&args);
    let pretty = run(&[args[0], args[1], args[2], "--pretty"]);
    assert_eq!(compact.status.code(), Some(2));
    assert_eq!(pretty.status.code(), Some(2));
    let pretty_text = String::from_utf8(pretty.stdout).unwrap();
    assert!(pretty_text.starts_with("{\n  \"schema_version\""));
    assert!(!pretty_text.contains("PRIVATE"));
    let summary: serde_json::Value = serde_json::from_str(&pretty_text).unwrap();
    assert_eq!(
        summary,
        serde_json::from_slice::<serde_json::Value>(&compact.stdout).unwrap()
    );
    assert_eq!(summary["reason_code"], "oauth_expired");
    assert_eq!(
        summary["diagnostics"]["llm"]["status"],
        "prerequisites_failed"
    );
    assert_eq!(summary["diagnostics"]["llm"]["request_budget"], 2);
    assert_eq!(summary["diagnostics"]["llm"]["inference"], "not_checked");

    std::fs::remove_file(&oauth).unwrap();
    let missing = run(&args);
    assert_eq!(missing.status.code(), Some(2));
    let summary: serde_json::Value = serde_json::from_slice(&missing.stdout).unwrap();
    assert_eq!(
        summary["diagnostics"]["llm"]["status"],
        "prerequisites_failed"
    );
    assert_eq!(summary["reason_code"], "oauth_file_unreadable");
}

#[test]
fn invalid_doctor_configuration_does_not_leak_contents() {
    let directory = tempfile::tempdir().unwrap();
    let bad_config = directory.path().join("bad.toml");
    std::fs::write(&bad_config, "private_token = SECRET-MUST-NOT-LEAK").unwrap();
    let output = run(&["doctor", "--config", bad_config.to_str().unwrap()]);
    assert_eq!(output.status.code(), Some(1), "{output:?}");
    let summary: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(summary["state"], "error");
    assert_eq!(summary["reason_code"], "invalid_config");
    assert!(!String::from_utf8(output.stdout).unwrap().contains("SECRET"));
    assert!(!String::from_utf8(output.stderr).unwrap().contains("SECRET"));
}

#[test]
fn doctor_checks_explicit_transport_without_a_budget() {
    let directory = tempfile::tempdir().unwrap();
    let config = directory.path().join("transport.toml");
    std::fs::write(&config, "[score]\nendpoint = \"https://invalid.example\"\n").unwrap();
    let output = run(&["doctor", "--config", config.to_str().unwrap()]);
    assert_eq!(output.status.code(), Some(2), "{output:?}");
    let summary: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(summary["state"], "partial");
    assert_eq!(summary["reason_code"], "unsupported_transport");
}
