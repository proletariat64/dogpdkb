use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::Path;
use std::process::{Command, Output, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const SOURCE: &str = "# OCR 示例\r\n\r\n![seed](image.png)\r\n\r\nEnd.\r\n";
const TOKEN: &str = "synthetic-fixture-only";
const RUN_ARGS: &[&str] = &["run", "--plan", "plan.json", "--result", "result.json"];

fn cli(root: &Path, args: &[&str]) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_mdocr"));
    command
        .current_dir(root)
        .env_clear()
        .env("PADDLEOCR_TOKEN", TOKEN)
        .args(args);
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

fn plan(root: &Path, endpoint: &str, inputs: &[&str]) -> String {
    let output = cli(
        root,
        &[
            "plan",
            "--mode",
            "paddle",
            "--plan",
            "plan.json",
            "--endpoint",
            endpoint,
            "--request-budget",
            "4",
            "--concurrency",
            "1",
        ],
    )
    .args(inputs)
    .output()
    .unwrap();
    assert_exit(&output, 0);
    sha(&fs::read(root.join("plan.json")).unwrap())
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

fn assert_admission(root: &Path, plan_hash: &str, count: usize, image: &Path) -> Value {
    let journal = read_json(&root.join("plan.json.execution.json"));
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
fn paddle_run_publishes_bound_evidence_and_recovers_result_without_resubmitting() {
    let root = tempfile::tempdir().unwrap();
    let root = root.path();
    source(root, [12, 34, 56]);
    let (listener, base) = listener();
    let plan_hash = plan(
        root,
        &format!("{base}/jobs"),
        &["--input", "in.md", "--output", "out.md"],
    );
    let child = cli(root, RUN_ARGS)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    serve_success(&listener, &base, || {
        assert_admission(root, &plan_hash, 1, &root.join("image.png"));
    });
    assert_exit(&child.wait_with_output().unwrap(), 0);
    drop(listener);

    let mirror = root.join("result.json");
    let result = read_json(&mirror);
    assert_eq!(result["schema_version"], "mdocr-result-2");
    assert_eq!(result["status"], "completed");
    assert_eq!(result["plan_sha256"], plan_hash);
    assert_eq!(result["budget"]["requests"], 1);
    assert_eq!(result["requests"][0]["status"], "success");
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
        &["--directory", "source", "--output-dir", "out"],
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

    let journal_path = root.join("plan.json.execution.json");
    let mirror_path = root.join("result.json");
    let journal = fs::read(&journal_path).unwrap();
    let mirror = fs::read(&mirror_path).unwrap();
    let assembled = cli(
        root,
        &[
            "assemble",
            "--plan",
            "plan.json",
            "--journal",
            "plan.json.execution.json",
            "--execution-version",
            env!("CARGO_PKG_VERSION"),
            "--output-dir",
            "assembly",
        ],
    )
    .env_remove("PADDLEOCR_TOKEN")
    .output()
    .unwrap();
    assert_exit(&assembled, 2);
    assert_eq!(fs::read(&journal_path).unwrap(), journal);
    assert_eq!(fs::read(&mirror_path).unwrap(), mirror);
    let assembly = read_json(&root.join("assembly/assembly.json"));
    assert_eq!(assembly["recovery"]["journal_sha256"], sha(&journal));
    assert_eq!(assembly["plan_sha256"], plan_hash);
    assert_eq!(assembly["requests"][1]["status"], "unknown");
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
    let requests = report["requests"].as_array().unwrap();
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0]["status"], "success");
    assert_eq!(requests[1]["status"], "unknown");
    assert_eq!(requests[1]["reason"], "OCR_OUTCOME_UNKNOWN");
    assert_eq!(report["documents"][1]["resources"][0]["status"], "unknown");
    assert!(report["documents"][1]["output"].is_null());
    assert_eq!(fs::read(root.join("out/first/in.md")).unwrap(), recovered);
    assert!(!root.join("out/second/in.md").exists());
}
