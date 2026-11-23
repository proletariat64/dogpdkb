use serde_json::Value;
use std::fs;
use std::path::Path;
use std::process::{Command, Output};

fn doctor(directory: &Path, config: Option<&Path>) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_mdocrr"));
    command
        .env_clear()
        .env("HTTPS_PROXY", "http://127.0.0.1:1")
        .env("PADDLEOCR_TOKEN", "ambient-token")
        .env("PI_CODING_AGENT_DIR", directory)
        .current_dir(directory)
        .arg("doctor");
    if let Some(config) = config {
        command.arg("--config").arg(config);
    }
    command.output().unwrap()
}

#[test]
fn doctor_reads_the_selected_toml_and_uses_its_directory_for_paths() {
    let root = tempfile::tempdir().unwrap();
    let config_dir = root.path().join("policy");
    fs::create_dir(&config_dir).unwrap();
    fs::write(config_dir.join("token.txt"), "fixture-secret\n").unwrap();
    let config = config_dir.join("paddle.toml");
    fs::write(&config, "mode = \"paddle\"\ntoken_file = \"token.txt\"\n").unwrap();
    fs::write(root.path().join("default.toml"), "unexpected = true").unwrap();

    let output = doctor(root.path(), Some(&config));
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let summary: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(summary["mode"], "paddle");
    assert_eq!(summary["config_source"], config.to_str().unwrap());
    assert_eq!(summary["network_checked"], false);
    let checks = summary["checks"].as_array().unwrap();
    assert!(
        checks
            .iter()
            .any(|check| { check["name"] == "credentials" && check["status"] == "pass" })
    );
    for stream in [output.stdout, output.stderr] {
        let text = String::from_utf8(stream).unwrap();
        assert!(!text.contains("fixture-secret"));
        assert!(!text.contains("ambient-token"));
    }
}

#[test]
fn doctor_reports_expired_oauth_without_refreshing_or_reading_ambient_config() {
    let root = tempfile::tempdir().unwrap();
    let credentials = br#"{"openai":{"type":"oauth","access":"fixture-access","refresh":"fixture-refresh","expires":0}}"#;
    let auth = root.path().join("auth.json");
    fs::write(&auth, credentials).unwrap();
    fs::write(root.path().join("default.toml"), "unexpected = true").unwrap();

    let builtin = doctor(root.path(), None);
    assert_eq!(builtin.status.code(), Some(2));
    let builtin: Value = serde_json::from_slice(&builtin.stdout).unwrap();
    assert_eq!(builtin["config_source"], "built-in");
    assert_eq!(builtin["mode"], "llm");
    assert!(builtin["checks"].as_array().unwrap().iter().any(|check| {
        check["name"] == "credentials" && check["reason_code"] == "credential_file_required"
    }));

    let config = root.path().join("llm.toml");
    fs::write(&config, "oauth_file = \"auth.json\"\n").unwrap();
    let configured = doctor(root.path(), Some(&config));
    assert!(configured.status.success());
    let output: Value = serde_json::from_slice(&configured.stdout).unwrap();
    assert_eq!(output["network_checked"], false);
    assert!(output["checks"].as_array().unwrap().iter().any(|check| {
        check["name"] == "credentials"
            && check["status"] == "warn"
            && check["reason_code"] == "oauth_credential_expired"
    }));
    assert_eq!(fs::read(auth).unwrap(), credentials);
    assert!(!root.path().join("auth.json.lock").exists());
    assert!(
        !String::from_utf8(configured.stdout)
            .unwrap()
            .contains("fixture-refresh")
    );
}

#[test]
fn doctor_rejects_invalid_provider_policy_and_reports_a_bad_ca_file() {
    let root = tempfile::tempdir().unwrap();
    let config = root.path().join("configuration.toml");
    for (contents, diagnostic) in [
        ("concurency = 3", "unknown field"),
        ("concurrency = 0", "concurrency"),
        ("admission_seconds = -1", "admission_seconds"),
        (
            "mode = \"paddle\"\nmodel = \"openai/gpt-6.1-terra\"",
            "require LLM mode",
        ),
        (
            "mode = \"llm\"\nendpoint = \"https://example.invalid/\"",
            "require Paddle mode",
        ),
        (
            "proxy_url = \"http://user:private-value@127.0.0.1:1\"",
            "without credentials",
        ),
    ] {
        fs::write(&config, contents).unwrap();
        let invalid = doctor(root.path(), Some(&config));
        assert_eq!(invalid.status.code(), Some(1), "{contents}");
        let stderr = String::from_utf8(invalid.stderr).unwrap();
        assert!(stderr.contains(diagnostic), "{stderr}");
        assert!(!stderr.contains("private-value"));
    }

    fs::write(root.path().join("invalid.pem"), "no certificate\n").unwrap();
    fs::write(&config, "ca_file = \"invalid.pem\"\n").unwrap();
    let checked = doctor(root.path(), Some(&config));
    assert_eq!(checked.status.code(), Some(2));
    let report: Value = serde_json::from_slice(&checked.stdout).unwrap();
    assert!(report["checks"].as_array().unwrap().iter().any(|check| {
        check["name"] == "ca_file"
            && check["status"] == "fail"
            && check["reason_code"] == "ca_file_invalid"
    }));
}
