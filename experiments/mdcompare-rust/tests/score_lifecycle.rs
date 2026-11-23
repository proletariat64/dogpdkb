use serde_json::{Value, json};
use std::{
    fs,
    io::{BufRead, BufReader, Read, Write},
    net::{TcpListener, TcpStream},
    path::Path,
    process::{Child, Command, Output, Stdio},
    thread,
    time::{Duration, Instant},
};
use tempfile::TempDir;

fn command(dir: &Path, args: &[&str]) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_mdcompare"));
    command.current_dir(dir).env_clear().args(args);
    command
}

fn outcome(output: Output, code: i32) -> Value {
    assert_eq!(
        output.status.code(),
        Some(code),
        "stdout: {}; stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).unwrap()
}

fn invoke(dir: &Path, args: &[&str], code: i32) -> Value {
    outcome(command(dir, args).output().unwrap(), code)
}

fn write_json(path: &Path, value: &Value) {
    fs::write(path, serde_json::to_vec(value).unwrap()).unwrap();
}

fn read_json(dir: &Path, path: &str) -> Value {
    serde_json::from_slice(&fs::read(dir.join(path)).unwrap()).unwrap()
}

fn fixtures() -> TempDir {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("prior.md"),
        "# Transfer limits\n\nPayments above 100 USD require dual approval.\n",
    )
    .unwrap();
    fs::write(
        dir.path().join("current.md"),
        "# Transfer limits\n\nPayments above 50 USD require dual approval.\n",
    )
    .unwrap();
    let doc = |knowledge_base: &str| {
        let snapshot = |path| {
            json!({
                "path": path,
                "content_kind": "raw",
                "metadata": {"name": "Transfer limits", "logical_path": "Limits", "source_ref": "42"}
            })
        };
        json!({
            "knowledge_base": knowledge_base,
            "document_id": "42",
            "baseline": snapshot("prior.md"),
            "incoming": snapshot("current.md"),
        })
    };
    write_json(
        &dir.path().join("work.json"),
        &json!({
            "schema_version": "mdcompare-work-1",
            "operation": "semantic",
            "documents": [doc("book-a"), doc("book-b")],
        }),
    );
    let run = invoke(dir.path(), &["run", "--plan", "work.json"], 0);
    assert!(run["comparison_sha256"].is_string());
    write_json(
        &dir.path().join("score.json"),
        &json!({
            "schema_version": "mdcompare-score-work-1",
            "comparison_plan": "work.json",
            "comparison_sha256": run["comparison_sha256"],
            "documents": [
                {"knowledge_base": "book-b", "document_id": "42"},
                {"knowledge_base": "book-a", "document_id": "42"},
            ],
        }),
    );
    dir
}

fn listener(dir: &Path, budget: usize) -> TcpListener {
    let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
    listener.set_nonblocking(true).unwrap();
    fs::write(
        dir.join("default.toml"),
        format!(
            "[score]\nrequest_budget = {budget}\nconcurrency = 1\nendpoint = \"http://{}/v1/responses\"\n",
            listener.local_addr().unwrap()
        ),
    )
    .unwrap();
    listener
}

fn request(listener: &TcpListener, child: &mut Child) -> (TcpStream, Value) {
    let start = Instant::now();
    let mut socket = loop {
        if let Ok((stream, _)) = listener.accept() {
            break stream;
        }
        if child.try_wait().unwrap().is_some() || start.elapsed() > Duration::from_secs(10) {
            let _ = child.kill();
            let mut stdout = String::new();
            let mut stderr = String::new();
            child
                .stdout
                .as_mut()
                .unwrap()
                .read_to_string(&mut stdout)
                .unwrap();
            child
                .stderr
                .as_mut()
                .unwrap()
                .read_to_string(&mut stderr)
                .unwrap();
            panic!("Expected loopback request. stdout: {stdout}. stderr: {stderr}");
        }
        thread::sleep(Duration::from_millis(10));
    };
    socket
        .set_read_timeout(Some(Duration::from_secs(4)))
        .unwrap();
    let mut reader = BufReader::new(&mut socket);
    let mut length = None;
    loop {
        let mut line = String::new();
        assert!(reader.read_line(&mut line).unwrap() > 0);
        if line == "\r\n" {
            break;
        }
        let lower = line.to_ascii_lowercase();
        assert!(
            !lower.starts_with("authorization:"),
            "Loopback received a credential"
        );
        if let Some(value) = lower.strip_prefix("content-length:") {
            length = Some(value.trim().parse::<usize>().unwrap());
        }
    }
    let mut body = vec![0u8; length.unwrap()];
    reader.read_exact(&mut body).unwrap();
    (socket, serde_json::from_slice(&body).unwrap())
}

fn respond(socket: &mut TcpStream, request: &Value) {
    let packet: Value =
        serde_json::from_str(request["input"][1]["content"][0]["text"].as_str().unwrap()).unwrap();
    let count = packet["operations"].as_array().unwrap().len();
    assert!(count > 0);
    let reply = json!({
        "evaluation_status": "evaluated", "change_score": 4,
        "checked_operations": (0..count).collect::<Vec<_>>(),
        "changes": [{"operation_id": 0, "before": "100", "after": "50", "ambiguity": null}],
        "reason": "lower approval threshold", "missing_evidence": []
    });
    let done = json!({
        "type": "response.completed",
        "response": {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": reply.to_string()}]}]
        }
    });
    let event = format!("data: {done}\n\n");
    write!(
        socket,
        "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{event}",
        event.len()
    ).unwrap();
}

fn start(dir: &Path, resume: bool) -> Child {
    let mut command = command(dir, &["score", "--plan", "score.json"]);
    if resume {
        command.arg("--resume");
    }
    command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap()
}

#[test]
fn admitted_request_is_not_replayed_after_interruption() {
    let dir = fixtures();
    let listener = listener(dir.path(), 1);
    let mut child = start(dir.path(), false);
    let (_socket, _) = request(&listener, &mut child);
    let journal = read_json(dir.path(), "score.mdcompare/journal.json");
    assert_eq!(journal["attempts"][0]["state"], "admitted");
    child.kill().unwrap();
    child.wait().unwrap();

    let summary = invoke(dir.path(), &["score", "--plan", "score.json"], 2);
    assert_eq!(summary["counts"]["admitted"], 1);
    let report = read_json(dir.path(), "score.mdcompare/result.json");
    assert_eq!(report["rows"][0]["knowledge_base"], "book-b");
    assert_eq!(report["rows"][0]["state"], "unknown");
    assert_eq!(report["rows"][0]["admission"], 1);
    assert_eq!(report["rows"][1]["state"], "not_sent");
    assert_eq!(report["rows"][1]["admission"], Value::Null);
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );

    // A missing initialized journal fails closed even after a report exists.
    fs::remove_file(dir.path().join("score.mdcompare/journal.json")).unwrap();
    let failure = invoke(dir.path(), &["score", "--plan", "score.json"], 1);
    assert_eq!(failure["reason_code"], "binding_invalid");
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
}

#[test]
fn cancellation_waits_for_in_flight_work_and_explicit_resume_only_sends_pending() {
    let dir = fixtures();
    let listener = listener(dir.path(), 2);
    let mut child = start(dir.path(), false);
    let (mut socket, body) = request(&listener, &mut child);
    let state = invoke(dir.path(), &["cancel", "--plan", "score.json"], 0);
    assert_eq!(state["state"], "cancel_requested");
    respond(&mut socket, &body);
    outcome(child.wait_with_output().unwrap(), 3);
    let cancelled = read_json(dir.path(), "score.mdcompare/result.json");
    assert_eq!(cancelled["rows"][0]["state"], "scored");
    assert_eq!(cancelled["rows"][1]["state"], "not_sent");
    invoke(dir.path(), &["score", "--plan", "score.json"], 3);
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );

    let mut child = start(dir.path(), true);
    let (mut socket, body) = request(&listener, &mut child);
    respond(&mut socket, &body);
    let summary = outcome(child.wait_with_output().unwrap(), 0);
    assert_eq!(summary["counts"]["admitted"], 2);
    assert_eq!(summary["counts"]["scored"], 2);
    let complete = read_json(dir.path(), "score.mdcompare/result.json");
    assert_eq!(complete["rows"][0]["admission"], 1);
    assert_eq!(complete["rows"][1]["admission"], 2);
    let initial_digest = complete["journal_sha256"].clone();
    // If the process stopped after journaling the last settlement, report
    // reconstruction reuses the durable journal without another request.
    fs::remove_file(dir.path().join("score.mdcompare/result.json")).unwrap();
    invoke(
        dir.path(),
        &["score", "--plan", "score.json", "--resume"],
        0,
    );
    assert_eq!(
        read_json(dir.path(), "score.mdcompare/result.json")["journal_sha256"],
        initial_digest
    );
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
}

#[test]
fn zero_budget_publishes_proven_unsent_rows_without_credentials() {
    let dir = fixtures();
    fs::write(
        dir.path().join("default.toml"),
        "[score]\nrequest_budget = 0\n",
    )
    .unwrap();
    let summary = invoke(dir.path(), &["score", "--plan", "score.json"], 2);
    assert_eq!(summary["counts"]["admitted"], 0);
    let status = invoke(dir.path(), &["status", "--plan", "score.json"], 0);
    assert_eq!(status["state"], "partial");
    assert_eq!(status["counts"]["admitted"], 0);
    let result = read_json(dir.path(), "score.mdcompare/result.json");
    for row in result["rows"].as_array().unwrap() {
        assert_eq!(row["state"], "not_sent");
        assert_eq!(row["admission"], Value::Null);
        assert_eq!(row["reason_code"], "request_budget_exhausted");
    }
}

#[test]
fn rate_limit_at_minimum_concurrency_leaves_pending_documents_unsent() {
    let dir = fixtures();
    let listener = listener(dir.path(), 2);
    let mut child = start(dir.path(), false);
    let (mut socket, _) = request(&listener, &mut child);
    socket
        .write_all(
            b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        )
        .unwrap();
    let summary = outcome(child.wait_with_output().unwrap(), 2);
    assert_eq!(summary["counts"]["admitted"], 1);
    let result = read_json(dir.path(), "score.mdcompare/result.json");
    assert_eq!(result["rows"][0]["state"], "unscored");
    assert_eq!(result["rows"][0]["reason_code"], "rate_limited");
    assert_eq!(result["rows"][1]["state"], "not_sent");
    assert_eq!(result["rows"][1]["reason_code"], "rate_limited");
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
}
