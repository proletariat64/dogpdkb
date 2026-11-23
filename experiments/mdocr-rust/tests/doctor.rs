use serde_json::Value;
use std::fs;
use std::path::Path;
use std::process::{Command, Output};

fn doctor(directory: &Path, config: Option<&Path>) -> Output {
    doctor_with_args(directory, config, &[])
}

fn doctor_with_args(directory: &Path, config: Option<&Path>, args: &[&str]) -> Output {
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
    command.args(args).output().unwrap()
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
    assert_eq!(summary["status"], "local_only");
    assert_eq!(summary["probe"]["reason_code"], "paddle_ping_unsupported");
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
    assert_eq!(builtin.status.code(), Some(0));
    let builtin: Value = serde_json::from_slice(&builtin.stdout).unwrap();
    assert_eq!(builtin["config_source"], "built-in");
    assert_eq!(builtin["mode"], "llm");
    assert!(builtin["checks"].as_array().unwrap().iter().any(|check| {
        check["name"] == "credentials" && check["reason_code"] == "credential_not_configured"
    }));
    assert_eq!(builtin["probe"]["reason_code"], "credential_not_configured");
    assert_eq!(builtin["network_checked"], false);

    let config = root.path().join("llm.toml");
    fs::write(&config, "oauth_file = \"auth.json\"\n").unwrap();
    let configured = doctor(root.path(), Some(&config));
    assert_eq!(configured.status.code(), Some(2));
    let output: Value = serde_json::from_slice(&configured.stdout).unwrap();
    assert_eq!(output["network_checked"], false);
    assert!(output["checks"].as_array().unwrap().iter().any(|check| {
        check["name"] == "credentials"
            && check["status"] == "fail"
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
fn pretty_preserves_json_and_exit_status_for_local_results() {
    let root = tempfile::tempdir().unwrap();
    let config = root.path().join("config.toml");
    for (contents, code) in [
        (r#"mode = "llm""#, 0),
        (r#"oauth_file = "missing.txt""#, 2),
        ("concurency = 3", 1),
    ] {
        fs::write(&config, contents).unwrap();
        let compact = doctor(root.path(), Some(&config));
        let pretty = doctor_with_args(root.path(), Some(&config), &["--pretty"]);
        assert_eq!(compact.status.code(), Some(code));
        assert_eq!(pretty.status.code(), Some(code));
        assert_eq!(compact.stderr, pretty.stderr);
        assert_eq!(String::from_utf8_lossy(&compact.stdout).lines().count(), 1);
        assert!(String::from_utf8_lossy(&pretty.stdout).lines().count() > 1);
        let compact: Value = serde_json::from_slice(&compact.stdout).unwrap();
        let pretty: Value = serde_json::from_slice(&pretty.stdout).unwrap();
        assert_eq!(compact, pretty);
        assert_eq!(pretty["network_checked"], false);
    }
}

#[test]
fn explicit_zero_budget_skips_usable_oauth_without_a_connection_or_writes() {
    let root = tempfile::tempdir().unwrap();
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let config = root.path().join("config.toml");
    fs::write(
        &config,
        format!(
            "request_budget = 0\noauth_file = \"auth.json\"\nproxy_url = \"http://{}\"\ncache_dir = \"cache\"\n",
            listener.local_addr().unwrap()
        ),
    )
    .unwrap();
    let credentials = br#"{"openai":{"type":"oauth","access":"fixture-access","refresh":"fixture-refresh","expires":9999999999999}}"#;
    let auth = root.path().join("auth.json");
    fs::write(&auth, credentials).unwrap();
    let output = doctor(root.path(), Some(&config));
    assert_eq!(output.status.code(), Some(0));
    let report: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(report["probe"]["reason_code"], "request_budget_zero");
    assert_eq!(report["network_checked"], false);
    assert_eq!(report["probe"]["remote_credentials"], "unverified");
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
    assert_eq!(fs::read(auth).unwrap(), credentials);
    assert_eq!(fs::read_dir(root.path()).unwrap().count(), 2);
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
