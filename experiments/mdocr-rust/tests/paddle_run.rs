use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::process::{Command, Output, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const SOURCE: &str = "# OCR 示例\r\n\r\n![seed](image.png)\r\n\r\nEnd.\r\n";
const TOKEN: &str = "synthetic-fixture-only";
const RUN_ARGS: &[&str] = &["run", "--plan", "work.json"];

fn cli(root: &Path, args: &[&str]) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_mdocrr"));
    command.current_dir(root).env_clear().args(args);
    command
}

fn assert_exit(output: &Output, code: i32) {
    assert_eq!(
        output.status.code(),
        Some(code),
        "stdout: {}\nstderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

fn read_json(path: &Path) -> Value {
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}

fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn source(root: &Path, color: [u8; 3]) {
    fs::create_dir_all(root).unwrap();
    fs::write(root.join("in.md"), SOURCE).unwrap();
    image::RgbImage::from_pixel(11, 7, image::Rgb(color))
        .save(root.join("image.png"))
        .unwrap();
}

fn plan(root: &Path, endpoint: &str, output_dir: &str, documents: Value) -> String {
    write_plan(root, endpoint, output_dir, documents);
    let output = cli(root, &["accept", "--plan", "work.json"])
        .output()
        .unwrap();
    assert_exit(&output, 0);
    sha(&fs::read(root.join("work.mdocr/plan.json")).unwrap())
}

fn write_plan(root: &Path, endpoint: &str, output_dir: &str, documents: Value) {
    fs::write(
        root.join("work.json"),
        serde_json::to_vec_pretty(&json!({
            "schema_version": "mdocr-work-1",
            "output_dir": output_dir,
            "documents": documents,
        }))
        .unwrap(),
    )
    .unwrap();
    fs::write(root.join("token"), TOKEN).unwrap();
    fs::write(
        root.join("default.toml"),
        format!(
            "mode = \"paddle\"\nendpoint = \"{endpoint}\"\nrequest_budget = 4\nconcurrency = 1\ntoken_file = \"token\"\n"
        ),
    ).unwrap();
}

fn accept(listener: &TcpListener) -> TcpStream {
    let deadline = Instant::now() + Duration::from_secs(15);
    loop {
        match listener.accept() {
            Ok((stream, _)) => {
                stream.set_nonblocking(false).unwrap();
                return stream;
            }
            Err(error)
                if error.kind() == std::io::ErrorKind::WouldBlock && Instant::now() < deadline =>
            {
                thread::sleep(Duration::from_millis(5));
            }
            Err(error) => panic!("Paddle fixture did not receive a request: {error}"),
        }
    }
}

fn read_request(stream: &mut TcpStream) -> (String, String, Vec<u8>) {
    stream
        .set_read_timeout(Some(Duration::from_secs(3)))
        .unwrap();
    let mut input = BufReader::new(stream);
    let mut first = String::new();
    input.read_line(&mut first).unwrap();
    let mut headers = String::new();
    loop {
        let mut line = String::new();
        assert_ne!(input.read_line(&mut line).unwrap(), 0, "truncated headers");
        if line == "\r\n" {
            break;
        }
        headers.push_str(&line);
    }
    let length = headers
        .lines()
        .find_map(|line| {
            let (key, value) = line.split_once(':')?;
            key.eq_ignore_ascii_case("content-length")
                .then(|| value.trim().parse().unwrap())
        })
        .unwrap_or(0);
    let mut body = vec![0; length];
    input.read_exact(&mut body).unwrap();
    (
        first.trim_end().to_owned(),
        headers.to_ascii_lowercase(),
        body,
    )
}

fn respond(mut stream: TcpStream, body: &Value) {
    let body = serde_json::to_vec(body).unwrap();
    write!(
        stream,
        "HTTP/1.1 200 OK\r\nconnection: close\r\ncontent-type: application/json\r\ncontent-length: {}\r\n\r\n",
        body.len()
    )
    .unwrap();
    stream.write_all(&body).unwrap();
}

fn confirm_reset(root: &Path, answer: &[u8]) -> Output {
    let mut child = cli(root, &["reset", "--plan", "work.json"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(answer).unwrap();
    child.wait_with_output().unwrap()
}

fn serve_png(listener: &TcpListener, bytes: &[u8]) {
    let mut stream = accept(listener);
    assert_eq!(read_request(&mut stream).0, "GET /image.png HTTP/1.1");
    write!(
        stream,
        "HTTP/1.1 200 OK\r\nconnection: close\r\ncontent-type: image/png\r\ncontent-length: {}\r\n\r\n",
        bytes.len()
    )
    .unwrap();
    stream.write_all(bytes).unwrap();
}

fn assert_admission(root: &Path, plan_hash: &str, count: usize, image: &Path) -> Value {
    let journal = read_json(&root.join("work.mdocr/journal.json"));
    assert_eq!(journal["plan_sha256"], plan_hash);
    assert_eq!(journal["budget"]["requests"], count);
    let requests = journal["requests"].as_array().unwrap();
    assert_eq!(requests.len(), count);
    let last = requests.last().unwrap();
    assert_eq!(last["status"], "admitted");
    assert_eq!(last["source_sha256"], sha(&fs::read(image).unwrap()));
    journal
}

fn listener() -> (TcpListener, String) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    (listener, base)
}

fn serve_success(listener: &TcpListener, base: &str, on_post: impl FnOnce()) {
    let mut stream = accept(listener);
    let (request, headers, body) = read_request(&mut stream);
    assert_eq!(request, "POST /jobs HTTP/1.1");
    assert!(headers.contains(&format!("authorization: bearer {TOKEN}\r\n")));
    assert!(headers.contains("content-type: multipart/form-data;"));
    let parts = String::from_utf8_lossy(&body);
    assert!(parts.contains("PaddleOCR-VL-1.6"));
    assert!(parts.contains("filename=\"prepared.jpg\""));
    assert!(body.windows(3).any(|bytes| bytes == [0xff, 0xd8, 0xff]));
    on_post();
    respond(stream, &json!({"data":{"jobId":"one"}}));

    let mut stream = accept(listener);
    let (request, headers, _) = read_request(&mut stream);
    assert_eq!(request, "GET /jobs/one HTTP/1.1");
    assert!(headers.contains(&format!("authorization: bearer {TOKEN}\r\n")));
    respond(
        stream,
        &json!({"data":{"state":"done","resultUrl":{"jsonUrl":format!("{base}/result?signature=fixture")}}}),
    );

    let mut stream = accept(listener);
    let (request, headers, _) = read_request(&mut stream);
    assert_eq!(request, "GET /result?signature=fixture HTTP/1.1");
    assert!(!headers.contains("authorization:"));
    respond(
        stream,
        &json!({"result":{"layoutParsingResults":[{"markdown":{"text":"Recognized. ![chart](provider.png)"}}]}}),
    );
}

#[test]
fn reset_reruns_warm_ocr_and_only_purge_discards_the_downloads() {
    let root = tempfile::tempdir().unwrap();
    let root = root.path();
    source(root, [12, 34, 56]);
    source(&root.join("second"), [12, 34, 57]);
    let bytes = fs::read(root.join("image.png")).unwrap();
    let (listener, base) = listener();
    fs::write(root.join("in.md"), format!("![seed]({base}/image.png)\n")).unwrap();
    write_plan(
        root,
        &format!("{base}/jobs"),
        ".",
        json!([
            {"path": "in.md", "output": "out.md", "download_remote": true},
            {"path": "second/in.md", "output": "second.md"},
        ]),
    );
    let config = root.join("default.toml");
    let toml = fs::read_to_string(&config).unwrap();
    fs::write(config, format!("{toml}cache_dir = \"cache\"\n")).unwrap();
    let start = || {
        cli(root, RUN_ARGS)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap()
    };
    let initial = start();
    serve_png(&listener, &bytes);
    serve_success(&listener, &base, || {});
    serve_success(&listener, &base, || {});
    assert_exit(&initial.wait_with_output().unwrap(), 0);
    let workspace = root.join("work.mdocr");
    let checkpoint = fs::read(workspace.join("download.json")).unwrap();
    let cached = || fs::read_dir(root.join("cache")).unwrap().count();
    assert!(cached() > 0);
    let count = || read_json(&workspace.join("result.json"))["budget"]["requests"].clone();
    assert_eq!(count(), 2);

    assert_exit(&confirm_reset(root, b"y\n"), 0);
    assert_eq!(
        fs::read(workspace.join("download.json")).unwrap(),
        checkpoint
    );
    assert!(cached() > 0);
    assert!(!workspace.join("evidence").exists());
    assert!(!root.join("out.md").exists());
    fs::remove_file(root.join("token")).unwrap();
    let first_attempt = cli(root, RUN_ARGS).output().unwrap();
    assert_exit(&first_attempt, 2);
    assert_eq!(count(), 0);
    assert!(workspace.join("reset.json").exists());
    let cancel = cli(root, &["cancel", "--plan", "work.json"])
        .output()
        .unwrap();
    assert_exit(&cancel, 0);
    assert_exit(&cli(root, RUN_ARGS).output().unwrap(), 3);
    fs::write(root.join("token"), TOKEN).unwrap();
    let redo = cli(root, RUN_ARGS)
        .arg("--resume")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    serve_success(&listener, &base, || {
        let active = cli(root, &["reset", "--plan", "work.json"])
            .output()
            .unwrap();
        assert_exit(&active, 1);
        let summary: Value = serde_json::from_slice(&active.stdout).unwrap();
        assert_eq!(summary["reason_code"], "WORKSPACE_ACTIVE");
    });
    serve_success(&listener, &base, || {});
    assert_exit(&redo.wait_with_output().unwrap(), 0);
    assert_eq!(count(), 2);
    assert!(!workspace.join("reset.json").exists());
    assert!(root.join("out.md").is_file());

    assert_exit(&confirm_reset(root, b"y\n"), 0);
    let mut killed = start();
    let mut in_flight = accept(&listener);
    assert_eq!(read_request(&mut in_flight).0, "POST /jobs HTTP/1.1");
    killed.kill().unwrap();
    assert!(!killed.wait().unwrap().success());
    drop(in_flight);
    let restarted = start();
    serve_success(&listener, &base, || {});
    assert_exit(&restarted.wait_with_output().unwrap(), 2);
    assert_eq!(count(), 2);
    assert!(workspace.join("reset.json").exists());
    assert!(!root.join("out.md").exists());
    assert!(root.join("second.md").is_file());

    assert_exit(
        &cli(root, &["purge", "--plan", "work.json", "-Y"])
            .output()
            .unwrap(),
        0,
    );
    assert!(!workspace.join("download.json").exists());
    assert!(!workspace.join("images").exists());
    assert!(root.join("second.md").is_file());
    assert_exit(&confirm_reset(root, b"y\n"), 0);
    assert!(!root.join("out.md").exists());
    assert!(!root.join("second.md").exists());
    let redownload = start();
    serve_png(&listener, &bytes);
    serve_success(&listener, &base, || {});
    serve_success(&listener, &base, || {});
    assert_exit(&redownload.wait_with_output().unwrap(), 0);
    assert_eq!(count(), 2);

    let mut other_work = read_json(&root.join("work.json"));
    other_work["documents"][0]["output"] = json!("cache-copy.md");
    other_work["documents"][1]["output"] = json!("cache-copy-second.md");
    fs::write(
        root.join("cache-copy.json"),
        serde_json::to_vec(&other_work).unwrap(),
    )
    .unwrap();
    let reused = cli(root, &["run", "--plan", "cache-copy.json"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    serve_png(&listener, &bytes);
    assert_exit(&reused.wait_with_output().unwrap(), 0);
    let reused = read_json(&root.join("cache-copy.mdocr/result.json"));
    assert_eq!(reused["budget"]["requests"], 0);
    assert_eq!(reused["documents"][0]["resources"][0]["status"], "reused");
}

#[test]
fn output_preflight_failure_blocks_provider_admission() {
    let home = tempfile::tempdir().unwrap();
    let root = home.path();
    source(root, [12, 34, 56]);
    let (listener, base) = listener();
    let output = root.join("out");
    fs::create_dir(&output).unwrap();
    let config = fs::Permissions::from_mode(0o500);
    fs::set_permissions(&output, config).unwrap();
    if tempfile::tempdir_in(&output).is_ok() {
        // A privileged test runner can bypass directory permissions.
        return;
    }
    plan(
        root,
        &format!("{base}/jobs"),
        "out",
        json!([{"path": "in.md"}]),
    );
    let failed = cli(root, RUN_ARGS).output().unwrap();
    assert_exit(&failed, 1);
    assert!(String::from_utf8_lossy(&failed.stderr).contains("output filesystem"));
    assert!(!root.join("work.mdocr/journal.json").exists());
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
}

#[test]
fn paddle_run_publishes_bound_evidence_and_recovers_result_without_resubmitting() {
    let root = tempfile::tempdir().unwrap();
    let root = root.path();
    source(root, [12, 34, 56]);
    let (listener, base) = listener();
    let plan_hash = plan(
        root,
        &format!("{base}/jobs"),
        ".",
        json!([{"path": "in.md", "output": "out.md"}]),
    );
    let log_path = root.join("mdocrr.log");
    let child = cli(root, RUN_ARGS)
        .stdout(Stdio::piped())
        .stderr(fs::File::create(&log_path).unwrap())
        .spawn()
        .unwrap();
    serve_success(&listener, &base, || {
        assert_admission(root, &plan_hash, 1, &root.join("image.png"));
        let log = fs::read_to_string(&log_path).unwrap();
        assert!(
            log.lines().any(|line| {
                line.contains("event=admitted ") && line.contains("in_flight=1 requests=1/4")
            }),
            "the admission must be visible before the provider responds: {log}"
        );
        assert!(
            log.lines()
                .any(|line| { line.contains("event=started ") && line.contains("documents=0/1") })
        );
    });
    assert_exit(&child.wait_with_output().unwrap(), 0);
    drop(listener);

    let mirror = root.join("work.mdocr/result.json");
    let result = read_json(&mirror);
    assert_eq!(result["schema_version"], "mdocr-batch-result-1");
    assert_eq!(result["status"], "completed");
    assert_eq!(result["plan_sha256"], plan_hash);
    assert_eq!(result["budget"]["requests"], 1);
    assert_eq!(result["budget"]["request_limit"], 4);
    assert_eq!(
        read_json(&root.join("work.mdocr/journal.json"))["requests"][0]["status"],
        "success"
    );
    let document = &result["documents"][0];
    assert_eq!(document["status"], "completed");
    assert_eq!(document["ocr"]["contract"], "mdocr-ocr-2");
    assert_eq!(document["ocr"]["configuration"]["mode"], "paddle");
    let image = &document["ocr"]["images"][0];
    assert_eq!(
        image["source_path"],
        json!(root.join("image.png").canonicalize().unwrap())
    );
    assert_eq!(
        image["source_sha256"],
        sha(&fs::read(root.join("image.png")).unwrap())
    );
    let artifact = fs::read_to_string(image["artifact"].as_str().unwrap()).unwrap();
    assert_eq!(image["artifact_sha256"], sha(artifact.as_bytes()));
    assert!(artifact.lines().any(|line| {
        line.starts_with("> Original image: [source](<") && line.ends_with("image.png>)")
    }));
    assert!(artifact.contains("Recognized. [PaddleOCR image region: chart]"));
    let markdown = fs::read_to_string(root.join("out.md")).unwrap();
    assert_eq!(document["output_sha256"], sha(markdown.as_bytes()));
    assert_eq!(
        markdown.replace(&format!("\n\n{artifact}\n\n"), "![seed](image.png)"),
        SOURCE
    );

    fs::remove_file(&mirror).unwrap();
    assert_exit(&cli(root, RUN_ARGS).output().unwrap(), 0);
    assert_eq!(read_json(&mirror), result);
}

#[test]
fn paddle_run_assembles_accepted_work_without_replaying_a_crash_admission() {
    let root = tempfile::tempdir().unwrap();
    let root = root.path();
    let first = root.join("source/first");
    let second = root.join("source/second");
    source(&first, [12, 34, 56]);
    source(&second, [12, 34, 57]);
    let (listener, base) = listener();
    let plan_hash = plan(
        root,
        &format!("{base}/jobs"),
        "out",
        json!([
            {"path": "source/first/in.md", "output": "first/in.md"},
            {"path": "source/second/in.md", "output": "second/in.md"},
        ]),
    );
    let mut child = cli(root, RUN_ARGS)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    serve_success(&listener, &base, || {
        assert_admission(root, &plan_hash, 1, &first.join("image.png"));
    });
    let mut stream = accept(&listener);
    let (request, _, _) = read_request(&mut stream);
    assert_eq!(request, "POST /jobs HTTP/1.1");
    let state = assert_admission(root, &plan_hash, 2, &second.join("image.png"));
    assert_eq!(state["requests"][0]["status"], "success");

    child.kill().unwrap();
    assert!(!child.wait().unwrap().success());
    drop(stream);
    drop(listener);

    let journal_path = root.join("work.mdocr/journal.json");
    let mirror_path = root.join("work.mdocr/result.json");
    let journal = fs::read(&journal_path).unwrap();
    let mirror = fs::read(&mirror_path).unwrap();
    fs::remove_file(root.join("token")).unwrap();
    let assembled = cli(
        root,
        &[
            "assemble",
            "--plan",
            "work.json",
            "--output-dir",
            "assembly",
        ],
    )
    .output()
    .unwrap();
    assert_exit(&assembled, 2);
    assert_eq!(fs::read(&journal_path).unwrap(), journal);
    assert_eq!(fs::read(&mirror_path).unwrap(), mirror);
    let assembly = read_json(&root.join("assembly/assembly.json"));
    assert_eq!(assembly["recovery"]["journal_sha256"], sha(&journal));
    assert_eq!(assembly["plan_sha256"], plan_hash);
    assert_eq!(assembly["schema_version"], "mdocr-assembly-1");
    assert_eq!(assembly["documents"][0]["status"], "completed");
    assert_eq!(
        assembly["documents"][1]["resources"][0]["status"],
        "unknown"
    );
    let recovered = fs::read(root.join("assembly/documents/0.md")).unwrap();
    assert_eq!(assembly["documents"][0]["output_sha256"], sha(&recovered));
    assert!(!root.join("assembly/documents/1.md").exists());

    let mut resume = cli(root, RUN_ARGS);
    assert_exit(&resume.arg("--retry-failed").output().unwrap(), 2);
    let report = read_json(&mirror_path);
    assert_eq!(report["status"], "partial");
    let history = read_json(&journal_path);
    let requests = history["requests"].as_array().unwrap();
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0]["status"], "success");
    assert_eq!(requests[1]["status"], "unknown");
    assert_eq!(requests[1]["reason"], "OCR_OUTCOME_UNKNOWN");
    assert_eq!(report["documents"][1]["resources"][0]["status"], "unknown");
    assert!(report["documents"][1]["output"].is_null());
    assert_eq!(fs::read(root.join("out/first/in.md")).unwrap(), recovered);
    assert!(!root.join("out/second/in.md").exists());

    assert_exit(
        &cli(root, &["purge", "--plan", "work.json", "-Y"])
            .output()
            .unwrap(),
        0,
    );
    let declined = confirm_reset(root, b"n\n");
    assert_exit(&declined, 2);
    let summary: Value = serde_json::from_slice(&declined.stdout).unwrap();
    assert_eq!(summary["unresolved_requests"], true);
    assert_eq!(
        summary["warnings"][0]["reason_code"],
        "REPEATED_PROVIDER_REQUEST_COST"
    );
    assert!(
        summary["warnings"][0]["message"]
            .as_str()
            .unwrap()
            .contains("can repeat those requests and their cost")
    );
    assert!(
        String::from_utf8_lossy(&declined.stderr).contains("repeat those requests and their cost")
    );
    assert_eq!(fs::read(root.join("out/first/in.md")).unwrap(), recovered);
    assert_exit(&confirm_reset(root, b"y\n"), 0);
    assert!(!root.join("out/first/in.md").exists());
    assert_eq!(
        fs::read(root.join("assembly/documents/0.md")).unwrap(),
        recovered
    );
}

#[test]
fn cancellation_settles_in_flight_work_and_requires_explicit_resume() {
    let root = tempfile::tempdir().unwrap();
    let root = root.path();
    let first = root.join("source/first");
    let second = root.join("source/second");
    source(&first, [12, 34, 56]);
    source(&second, [12, 34, 57]);
    let (listener, base) = listener();
    let plan_hash = plan(
        root,
        &format!("{base}/jobs"),
        "out",
        json!([
            {"path": "source/first/in.md", "output": "first/in.md"},
            {"path": "source/second/in.md", "output": "second/in.md"},
        ]),
    );
    let child = cli(root, RUN_ARGS)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    serve_success(&listener, &base, || {
        assert_admission(root, &plan_hash, 1, &first.join("image.png"));
        let purge = cli(root, &["purge", "--plan", "work.json", "-Y"])
            .output()
            .unwrap();
        assert_exit(&purge, 1);
        assert_eq!(
            serde_json::from_slice::<Value>(&purge.stdout).unwrap()["reason_code"],
            "WORKSPACE_ACTIVE"
        );
        let cancel = cli(root, &["cancel", "--plan", "work.json"])
            .output()
            .unwrap();
        assert_exit(&cancel, 0);
        let report: Value = serde_json::from_slice(&cancel.stdout).unwrap();
        assert_eq!(report["status"], "cancel_requested");
    });

    let output = child.wait_with_output().unwrap();
    assert_exit(&output, 3);
    let logs = String::from_utf8(output.stderr).unwrap();
    assert!(logs.contains("event=admitted"));
    assert!(logs.contains("event=settled"));
    assert!(logs.contains("event=canceled"));
    assert!(!logs.contains(TOKEN));

    let report_path = root.join("work.mdocr/result.json");
    let report = read_json(&report_path);
    assert_eq!(report["status"], "canceled");
    assert_eq!(report["budget"]["requests"], 1);
    assert_eq!(report["documents"][0]["status"], "completed");
    assert_eq!(
        report["documents"][1]["resources"][0]["status"],
        "unattempted"
    );
    assert_eq!(
        report["documents"][1]["resources"][0]["reason_code"],
        "BATCH_CANCELED"
    );
    assert!(root.join("out/first/in.md").is_file());
    assert!(!root.join("out/second/in.md").exists());
    assert!(
        listener
            .accept()
            .is_err_and(|error| error.kind() == std::io::ErrorKind::WouldBlock)
    );

    let duplicate_cancel = cli(root, &["cancel", "--plan", "work.json"])
        .output()
        .unwrap();
    assert_exit(&duplicate_cancel, 0);
    assert_exit(&cli(root, RUN_ARGS).output().unwrap(), 3);
    assert_eq!(read_json(&report_path), report);

    let resumed = cli(root, RUN_ARGS)
        .arg("--resume")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    serve_success(&listener, &base, || {
        assert_admission(root, &plan_hash, 2, &second.join("image.png"));
    });
    assert_exit(&resumed.wait_with_output().unwrap(), 0);
    assert_eq!(read_json(&report_path)["status"], "completed");
    assert_eq!(read_json(&report_path)["budget"]["requests"], 2);
    assert!(root.join("out/second/in.md").is_file());
}

#[test]
fn retry_failed_retries_a_paddle_job_with_a_terminal_failure() {
    let root = tempfile::tempdir().unwrap();
    let root = root.path();
    source(root, [12, 34, 56]);
    let (listener, base) = listener();
    let plan_hash = plan(
        root,
        &format!("{base}/jobs"),
        ".",
        json!([{"path": "in.md", "output": "out.md"}]),
    );

    let child = cli(root, RUN_ARGS)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stream = accept(&listener);
    assert_eq!(read_request(&mut stream).0, "POST /jobs HTTP/1.1");
    assert_admission(root, &plan_hash, 1, &root.join("image.png"));
    respond(stream, &json!({"data":{"jobId":"one"}}));
    let mut stream = accept(&listener);
    assert_eq!(read_request(&mut stream).0, "GET /jobs/one HTTP/1.1");
    respond(stream, &json!({"data":{"state":"failed"}}));
    assert_exit(&child.wait_with_output().unwrap(), 2);
    let result = read_json(&root.join("work.mdocr/result.json"));
    assert_eq!(result["documents"][0]["resources"][0]["status"], "failed");
    assert!(!root.join("out.md").exists());

    let child = cli(root, RUN_ARGS)
        .arg("--retry-failed")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    serve_success(&listener, &base, || {
        assert_admission(root, &plan_hash, 2, &root.join("image.png"));
    });
    assert_exit(&child.wait_with_output().unwrap(), 0);
    assert!(root.join("out.md").exists());
}

#[test]
fn ocr_in_a_list_preserves_the_original_list_and_nested_blocks() {
    let root = tempfile::tempdir().unwrap();
    let root = root.path();
    source(root, [12, 34, 56]);
    let list = "- First ![seed](image.png) and its continuation\n  on the next line.\n  - Nested list content\n- Second item\n";
    fs::write(root.join("in.md"), format!("{list}\nOutside the list.\n")).unwrap();
    let (listener, base) = listener();
    plan(
        root,
        &format!("{base}/jobs"),
        ".",
        json!([{"path": "in.md", "output": "out.md"}]),
    );
    let child = cli(root, RUN_ARGS)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    serve_success(&listener, &base, || {});
    assert_exit(&child.wait_with_output().unwrap(), 0);
    let output = fs::read_to_string(root.join("out.md")).unwrap();
    assert!(output.starts_with(list), "{output}");
    assert!(output[list.len()..].contains("Recognized."), "{output}");
    let original_visible: Vec<_> = pulldown_cmark::Parser::new(list).collect();
    let actual_visible: Vec<_> = pulldown_cmark::Parser::new(&output)
        .take(original_visible.len())
        .collect();
    assert_eq!(actual_visible, original_visible);
}
