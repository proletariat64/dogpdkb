use serde_json::{Value, json};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::process::{Command, Output};
use std::thread;
use std::time::Duration;

fn doctor(endpoint: &str, budget: &str, pretty: bool) -> Output {
    let dir = tempfile::tempdir().unwrap();
    let config = dir.path().join("doctor.toml");
    std::fs::write(
        &config,
        format!("[score]\nmodel = 'openai/gpt-6.1-terra'\neffort = 'high'\nendpoint = '{endpoint}'\n{budget}"),
    )
    .unwrap();
    let mut command = Command::new(env!("CARGO_BIN_EXE_mdcomparer"));
    command.env_clear().args(["doctor", "--config"]).arg(config);
    if pretty {
        command.arg("--pretty");
    }
    command.output().unwrap()
}

fn run_probe(status: u16, response: String, pretty: bool) -> (Output, Value) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let endpoint = format!("http://{}/responses", listener.local_addr().unwrap());
    let server = thread::spawn(move || {
        listener.set_nonblocking(true).unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let (mut stream, _) = loop {
            match listener.accept() {
                Ok(connection) => break connection,
                Err(error)
                    if error.kind() == std::io::ErrorKind::WouldBlock
                        && std::time::Instant::now() < deadline =>
                {
                    thread::sleep(Duration::from_millis(10))
                }
                Err(error) => panic!("expected a probe connection: {error}"),
            }
        };
        stream.set_nonblocking(false).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut reader = BufReader::new(&mut stream);
        let mut length = None;
        loop {
            let mut line = String::new();
            assert_ne!(reader.read_line(&mut line).unwrap(), 0);
            if let Some(value) = line.to_ascii_lowercase().strip_prefix("content-length:") {
                length = Some(value.trim().parse().unwrap());
            }
            if line == "\r\n" {
                break;
            }
        }
        let mut body = vec![0; length.unwrap()];
        reader.read_exact(&mut body).unwrap();
        write!(
            stream,
            "HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{response}",
            response.len()
        )
        .unwrap();
        assert_eq!(
            listener.accept().unwrap_err().kind(),
            std::io::ErrorKind::WouldBlock
        );
        serde_json::from_slice(&body).unwrap()
    });
    let output = doctor(&endpoint, "", pretty);
    assert_ne!(output.status.code(), Some(1), "{output:?}");
    (output, server.join().unwrap())
}

#[test]
fn completed_text_is_live_even_when_it_is_not_pong() {
    let mut compact = None;
    for (text, pretty) in [("PONG", false), ("Yes, I am here.", true)] {
        let event = json!({"type":"response.completed","response":{"status":"completed",
            "output":[{"type":"message","content":[{"type":"output_text","text":text}]}]}});
        let (output, request) = run_probe(200, format!("data: {event}\n\n"), pretty);
        assert!(output.status.success(), "{output:?}");
        assert_eq!(request["input"][1]["content"][0]["text"], "PING");
        assert_eq!(request["model"], "gpt-6.1-terra");
        assert_eq!(request["reasoning"]["effort"], "high");
        assert_eq!(request["store"], false);
        assert!(request["text"]["format"].is_null());
        let report: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(report["counts"]["probe_attempts"], 1);
        assert_eq!(report["diagnostics"]["llm"]["provider_status"], "responded");
        assert_eq!(report["diagnostics"]["llm"]["http_status"], 200);
        assert_eq!(report["diagnostics"]["llm"]["model_status"], "passed");
        assert_eq!(
            report["diagnostics"]["llm"]["oauth_remote_status"],
            "not_applicable"
        );
        if let Some(compact) = compact.take() {
            assert_eq!(report, compact);
            assert!(output.stdout.starts_with(b"{\n"));
        } else {
            compact = Some(report);
        }
        assert!(!String::from_utf8(output.stdout).unwrap().contains(text));
    }
}

#[test]
fn live_failures_keep_the_http_status_and_actionable_reason() {
    for (status, body, reason, model) in [
        (401, "PRIVATE_REPLY", "oauth_rejected", "not_verified"),
        (
            403,
            "PRIVATE_REPLY",
            "provider_access_denied",
            "not_verified",
        ),
        (
            404,
            r#"{"error":{"code":"model_not_found","message":"PRIVATE_REPLY"}}"#,
            "model_rejected",
            "rejected",
        ),
        (429, "PRIVATE_REPLY", "rate_limited", "not_verified"),
        (200, "", "incomplete_model_response", "not_verified"),
    ] {
        let (output, _) = run_probe(status, body.into(), false);
        assert_eq!(output.status.code(), Some(2), "{output:?}");
        let report: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(report["reason_code"], reason);
        assert_eq!(report["diagnostics"]["llm"]["http_status"], status);
        assert_eq!(report["diagnostics"]["llm"]["model_status"], model);
        if status == 401 {
            assert!(
                report["diagnostics"]["checks"][4]["hint"]
                    .as_str()
                    .unwrap()
                    .contains("local test endpoint")
            );
        }
        assert!(
            !String::from_utf8(output.stdout)
                .unwrap()
                .contains("PRIVATE_REPLY")
        );
    }
}

#[test]
fn explicit_zero_budget_does_not_connect() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let endpoint = format!("http://{}/responses", listener.local_addr().unwrap());
    let output = doctor(&endpoint, "request_budget = 0", false);
    assert!(output.status.success());
    let report: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(report["counts"]["probe_attempts"], 0);
    assert_eq!(report["diagnostics"]["llm"]["inference"], "not_checked");
    assert_eq!(
        report["diagnostics"]["checks"][4]["reason_code"],
        "request_budget_zero"
    );
    listener.set_nonblocking(true).unwrap();
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
}
