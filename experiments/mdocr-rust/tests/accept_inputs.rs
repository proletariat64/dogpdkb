use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::time::{Duration, Instant, UNIX_EPOCH};

use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};

fn command(root: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_mdocrr"))
        .current_dir(root)
        .env_clear()
        .args(args)
        .output()
        .expect("run mdocrr")
}

fn write_png(path: &Path) {
    image::RgbImage::from_pixel(2, 2, image::Rgb([70, 140, 210]))
        .save(path)
        .expect("create PNG fixture");
}

fn json_file(path: &Path) -> Value {
    serde_json::from_slice(&fs::read(path).expect("read JSON file")).expect("parse JSON")
}

fn write_json(root: &Path, name: &str, value: &Value) {
    fs::write(root.join(name), serde_json::to_vec(value).unwrap()).unwrap();
}

fn await_get(listener: &TcpListener) -> (TcpStream, String) {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        match listener.accept() {
            Ok((stream, _)) => {
                stream.set_nonblocking(false).unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .unwrap();
                let mut request = String::new();
                BufReader::new(&stream).read_line(&mut request).unwrap();
                return (stream, request);
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                assert!(Instant::now() < deadline, "image GET did not arrive");
                std::thread::sleep(Duration::from_millis(5));
            }
            Err(error) => panic!("accept image GET: {error}"),
        }
    }
}

fn respond_png(mut stream: TcpStream, png: &[u8]) {
    write!(
        stream,
        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        png.len()
    )
    .unwrap();
    stream.write_all(png).unwrap();
}

fn start_command(root: &Path, args: &[&str]) -> std::process::Child {
    Command::new(env!("CARGO_BIN_EXE_mdocrr"))
        .current_dir(root)
        .env_clear()
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap()
}

fn remote_batch(root: &Path, destinations: &[&str]) -> (TcpListener, Vec<u8>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let input = destinations
        .iter()
        .map(|name| {
            format!(
                "![photo](http://{}/{name})\n",
                listener.local_addr().unwrap()
            )
        })
        .collect::<String>();
    fs::write(root.join("input.md"), input).unwrap();
    write_json(
        root,
        "work.json",
        &json!({"schema_version":"mdocr-work-1","output_dir":"out",
                "documents":[{"path":"input.md","download_remote":true}]}),
    );
    fs::write(root.join("default.toml"), "request_budget = 0\n").unwrap();
    write_png(&root.join("fixture.png"));
    (listener, fs::read(root.join("fixture.png")).unwrap())
}

#[test]
fn slow_download_emits_progress_before_response() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let (listener, png) = remote_batch(root, &["slow.png"]);
    let mut child = start_command(root, &["accept", "--plan", "work.json"]);
    let stderr = child.stderr.take().unwrap();
    let (log, lines) = std::sync::mpsc::channel();
    let reader = std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines() {
            log.send(line.unwrap()).unwrap();
        }
    });
    let (stream, _) = await_get(&listener);
    let heartbeat = lines.recv_timeout(Duration::from_secs(10));
    respond_png(stream, &png);
    let output = child.wait_with_output().unwrap();
    reader.join().unwrap();
    assert!(output.status.success(), "{output:?}");
    assert!(
        heartbeat
            .unwrap()
            .contains("event=download_waiting phase=accept in_flight=1")
    );
}

#[test]
fn resume_before_freeze_reuses_only_committed_downloads_and_saved_policy() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let (listener, png) = remote_batch(root, &["first.png", "second.png"]);
    let mut interrupted = start_command(root, &["run", "--plan", "work.json"]);
    let (stream, request) = await_get(&listener);
    assert!(request.starts_with("GET /first.png "));
    respond_png(stream, &png);
    let (stream, request) = await_get(&listener);
    assert!(request.starts_with("GET /second.png "));
    interrupted.kill().unwrap();
    interrupted.wait().unwrap();
    drop(stream);
    let workspace = root.join("work.mdocr");
    assert!(!workspace.join("plan.json").exists());
    assert!(!workspace.join("journal.json").exists());
    let checkpoint_path = workspace.join("download.json");
    assert_eq!(
        json_file(&checkpoint_path)["schema_version"],
        "mdocr-download-1"
    );
    let resume = ["run", "--plan", "work.json", "--resume"];
    let checkpoint_bytes = fs::read(&checkpoint_path).unwrap();
    fs::write(&checkpoint_path, b"{").unwrap();
    let malformed = command(root, &resume);
    assert!(!malformed.status.success());
    assert!(String::from_utf8_lossy(&malformed.stderr).contains("invalid download checkpoint"));
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
    fs::write(&checkpoint_path, checkpoint_bytes).unwrap();

    let original = fs::read(root.join("input.md")).unwrap();
    fs::write(root.join("input.md"), b"source changed").unwrap();
    let rejected = command(root, &resume);
    assert!(!rejected.status.success());
    assert!(String::from_utf8_lossy(&rejected.stderr).contains("SOURCE_DRIFT"));
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
    fs::write(root.join("input.md"), original).unwrap();
    fs::write(root.join("changed.toml"), "request_budget = 1\n").unwrap();
    let mismatch = command(
        root,
        &[
            "run",
            "--plan",
            "work.json",
            "--resume",
            "--config",
            "changed.toml",
        ],
    );
    assert!(!mismatch.status.success());
    assert!(String::from_utf8_lossy(&mismatch.stderr).contains("CONFIG_CHANGED"));

    // The saved zero budget still applies after the default file becomes unusable.
    fs::write(root.join("default.toml"), "not valid TOML").unwrap();
    let sentinel = UNIX_EPOCH + Duration::from_secs(1);
    fs::File::open(&checkpoint_path)
        .unwrap()
        .set_modified(sentinel)
        .unwrap();
    let resumed = start_command(root, &resume);
    let (stream, request) = await_get(&listener);
    assert!(request.starts_with("GET /second.png "), "{request}");
    let checkpoint_mtime = fs::metadata(&checkpoint_path).unwrap().modified().unwrap();
    respond_png(stream, &png);
    let resumed = resumed.wait_with_output().unwrap();
    assert_eq!(resumed.status.code(), Some(2), "{resumed:?}");
    assert_eq!(
        checkpoint_mtime, sentinel,
        "rewrote the durable first image"
    );
    let plan = json_file(&workspace.join("plan.json"));
    assert_eq!(plan["settings"]["request_budget"], 0);
    assert!(
        plan["documents"][0]["images"]
            .as_array()
            .unwrap()
            .iter()
            .all(|image| image["error"].is_null())
    );
    assert_eq!(
        json_file(&workspace.join("result.json"))["budget"]["requests"],
        0
    );
}

#[test]
fn resume_before_freeze_reuses_a_committed_url_for_later_occurrences() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let (listener, png) = remote_batch(root, &["first.png", "second.png", "first.png"]);
    let mut interrupted = start_command(root, &["run", "--plan", "work.json"]);
    let (stream, request) = await_get(&listener);
    assert!(request.starts_with("GET /first.png "));
    respond_png(stream, &png);
    let (stream, request) = await_get(&listener);
    assert!(request.starts_with("GET /second.png "));
    interrupted.kill().unwrap();
    interrupted.wait().unwrap();
    drop(stream);

    let resumed = start_command(root, &["run", "--plan", "work.json", "--resume"]);
    let (stream, request) = await_get(&listener);
    assert!(request.starts_with("GET /second.png "));
    respond_png(stream, &png);
    let output = resumed.wait_with_output().unwrap();
    assert_eq!(output.status.code(), Some(2), "{output:?}");
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
    let checkpoint = json_file(&root.join("work.mdocr/download.json"));
    let images = checkpoint["documents"][0]["images"].as_array().unwrap();
    assert_eq!(images.len(), 3);
    assert!(
        images
            .iter()
            .all(|image| image["outcome"]["status"] == "success")
    );
    assert_eq!(images[0]["outcome"], images[2]["outcome"]);
}

#[test]
fn resume_before_freeze_refetches_only_unusable_or_unrecorded_images() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let (listener, intact) = remote_batch(
        root,
        &[
            "intact.png",
            "missing.png",
            "damaged.png",
            "failed.png",
            "pending.png",
        ],
    );
    let mut variants = Vec::new();
    for color in [[10, 30, 90], [20, 40, 100]] {
        let fixture = root.join("variant.png");
        image::RgbImage::from_pixel(2, 2, image::Rgb(color))
            .save(&fixture)
            .unwrap();
        variants.push(fs::read(&fixture).unwrap());
    }
    let mut interrupted = start_command(root, &["run", "--plan", "work.json"]);
    for (name, bytes) in [
        ("intact.png", &intact),
        ("missing.png", &variants[0]),
        ("damaged.png", &variants[1]),
    ] {
        let (stream, request) = await_get(&listener);
        assert!(request.starts_with(&format!("GET /{name} ")), "{request}");
        respond_png(stream, bytes);
    }
    let (mut stream, request) = await_get(&listener);
    assert!(request.starts_with("GET /failed.png "), "{request}");
    stream
        .write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        .unwrap();
    drop(stream);
    let (stream, request) = await_get(&listener);
    assert!(request.starts_with("GET /pending.png "), "{request}");
    interrupted.kill().unwrap();
    interrupted.wait().unwrap();
    drop(stream);

    let checkpoint = json_file(&root.join("work.mdocr/download.json"));
    let images = &checkpoint["documents"][0]["images"];
    fs::remove_file(images[1]["outcome"]["source_path"].as_str().unwrap()).unwrap();
    fs::write(
        images[2]["outcome"]["source_path"].as_str().unwrap(),
        b"corrupt",
    )
    .unwrap();
    assert_eq!(images[3]["outcome"]["status"], "failure");

    let resumed = start_command(root, &["run", "--plan", "work.json", "--resume"]);
    for (name, bytes) in [
        ("missing.png", &variants[0]),
        ("damaged.png", &variants[1]),
        ("failed.png", &intact),
        ("pending.png", &intact),
    ] {
        let (stream, request) = await_get(&listener);
        assert!(request.starts_with(&format!("GET /{name} ")), "{request}");
        respond_png(stream, bytes);
    }
    let resumed = resumed.wait_with_output().unwrap();
    assert_eq!(resumed.status.code(), Some(2), "{resumed:?}");
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
    let frozen = json_file(&root.join("work.mdocr/plan.json"));
    assert!(
        frozen["documents"][0]["images"]
            .as_array()
            .unwrap()
            .iter()
            .all(|image| image["error"].is_null())
    );
}

#[test]
fn frozen_staged_image_repair_requires_identical_bytes_and_explicit_resume_after_cancel() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let (listener, png) = remote_batch(root, &["photo.png"]);
    let accept = ["accept", "--plan", "work.json"];
    let started = start_command(root, &accept);
    let (stream, request) = await_get(&listener);
    assert!(request.starts_with("GET /photo.png "));
    respond_png(stream, &png);
    assert!(started.wait_with_output().unwrap().status.success());
    let workspace = root.join("work.mdocr");
    let frozen = workspace.join("plan.json");
    let frozen_bytes = fs::read(&frozen).unwrap();
    let plan = json_file(&frozen);
    let staged = PathBuf::from(
        plan["documents"][0]["images"][0]["source_path"]
            .as_str()
            .unwrap(),
    );
    assert!(command(root, &accept).status.success());
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
    assert!(
        command(root, &["cancel", "--plan", "work.json"])
            .status
            .success()
    );

    fs::write(&staged, b"corrupt staged bytes").unwrap();
    let ordinary = command(root, &["run", "--plan", "work.json"]);
    assert!(matches!(ordinary.status.code(), Some(1 | 3)));
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
    let assembly = command(
        root,
        &[
            "assemble",
            "--plan",
            "work.json",
            "--output-dir",
            "recovered",
        ],
    );
    assert!(!assembly.status.success());
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );

    let resumed = start_command(root, &["run", "--plan", "work.json", "--resume"]);
    let (stream, request) = await_get(&listener);
    assert!(request.starts_with("GET /photo.png "));
    respond_png(stream, &png);
    let outcome = resumed.wait_with_output().unwrap();
    assert_eq!(outcome.status.code(), Some(2), "{outcome:?}");
    assert_eq!(fs::read(&staged).unwrap(), png);
    assert_eq!(fs::read(&frozen).unwrap(), frozen_bytes);

    fs::remove_file(&staged).unwrap();
    image::RgbImage::from_pixel(2, 2, image::Rgb([210, 140, 70]))
        .save(root.join("changed.png"))
        .unwrap();
    let changed = fs::read(root.join("changed.png")).unwrap();
    let started = start_command(root, &accept);
    let (stream, request) = await_get(&listener);
    assert!(request.starts_with("GET /photo.png "));
    respond_png(stream, &changed);
    let outcome = started.wait_with_output().unwrap();
    assert_eq!(outcome.status.code(), Some(1), "{outcome:?}");
    assert!(String::from_utf8_lossy(&outcome.stderr).contains("IMAGE_DRIFT"));
    assert_eq!(fs::read(&frozen).unwrap(), frozen_bytes);
    assert_eq!(fs::read_dir(workspace.join("images")).unwrap().count(), 0);
}

#[test]
fn draft_uses_relative_paths_and_does_not_create_a_workspace() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    fs::create_dir_all(root.join("sources/nested")).unwrap();
    fs::create_dir(root.join("jobs")).unwrap();
    fs::write(root.join("sources/z.md"), "last").unwrap();
    fs::write(root.join("sources/nested/a.md"), "first").unwrap();
    let args = [
        "draft",
        "--input-dir",
        "sources",
        "--output-dir",
        "output",
        "--plan",
        "jobs/work.json",
    ];
    let result = command(root, &args);
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let work = json_file(&root.join("jobs/work.json"));
    assert_eq!(work["output_dir"], "../output");
    assert_eq!(
        work["documents"],
        json!([
            {"path":"../sources/nested/a.md","output":"nested/a.md"},
            {"path":"../sources/z.md","output":"z.md"}
        ])
    );
    assert!(!root.join("jobs/work.mdocr").exists());
    assert!(!root.join("output").exists());
    assert!(!command(root, &args).status.success());
}

#[test]
fn accept_reads_markdown_and_html_and_stages_inline_bytes() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    write_png(&root.join("图(1).png"));
    let png = fs::read(root.join("图(1).png")).unwrap();
    let data = format!("data:image/png;base64,{}", STANDARD.encode(&png));
    let document = format!(
        concat!(
            "金额 ![inline](图(1).png) ![collapsed][] [![linked](图(1).png)](https://link.invalid/)\n\n",
            "[collapsed]: <图(1).png>\n\n",
            "<div><IMG SRC='图(1).png'><img alt=\"a > b\" src=\"{}\"></div>\n\n",
            "\\![escaped](missing.png) `![code](missing.png)`\n\n",
            "```mermaid\nflowchart TD\n  ![ignored](missing.png)\n```\n\n",
            "<!-- mdocr:ocr-begin v=2 id=x -->\n\n![generated](missing.png)\n\n<!-- mdocr:ocr-end -->\n",
        ),
        data
    );
    fs::write(root.join("input.md"), document).unwrap();
    write_json(
        root,
        "work.json",
        &json!({"schema_version":"mdocr-work-1","output_dir":"out",
                "documents":[{"path":"input.md"}]}),
    );
    let args = ["accept", "--plan", "work.json"];
    let accepted = command(root, &args);
    assert!(
        accepted.status.success(),
        "{}",
        String::from_utf8_lossy(&accepted.stderr)
    );
    let frozen = root.join("work.mdocr/plan.json");
    let plan = json_file(&frozen);
    let occurrences = plan["documents"][0]["images"].as_array().unwrap();
    assert_eq!(occurrences.len(), 5, "{occurrences:#?}");
    assert!(occurrences.iter().all(|image| image["error"].is_null()));
    let inline = occurrences.last().unwrap();
    let staged = PathBuf::from(inline["source_path"].as_str().unwrap());
    assert!(staged.starts_with(root.join("work.mdocr/images")));
    assert_eq!(fs::read(staged).unwrap(), png);
    assert!(
        inline["resource_id"]
            .as_str()
            .unwrap()
            .starts_with("data:sha256:")
    );
    assert!(!fs::read_to_string(&frozen).unwrap().contains(&data));
    let before = fs::read(&frozen).unwrap();
    assert!(command(root, &args).status.success());
    assert_eq!(fs::read(frozen).unwrap(), before);
    assert!(!root.join("out").exists());
}

#[test]
fn accept_rejects_duplicate_keys_and_unknown_selection_without_starting_inference() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    fs::write(root.join("input.md"), "plain document").unwrap();
    let args = ["accept", "--plan", "work.json"];
    for work in [
        r#"{"schema_version":"mdocr-work-1","output_dir":"out","output_dir":"other","documents":[{"path":"input.md"}]}"#,
        r#"{"schema_version":"mdocr-work-1","output_dir":"out","documents":[{"path":"input.md","select_images":["missing.png"]}]}"#,
    ] {
        fs::write(root.join("work.json"), work).unwrap();
        assert!(!command(root, &args).status.success());
        assert!(!root.join("work.mdocr/plan.json").exists());
        assert!(!root.join("work.mdocr/journal.json").exists());
    }
}

#[test]
fn accept_downloads_only_for_the_document_that_grants_permission() {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    };
    use std::time::Duration;

    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    write_png(&root.join("test.png"));
    let png = fs::read(root.join("test.png")).unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let target = format!("http://{}/attached.png", listener.local_addr().unwrap());
    let stop = Arc::new(AtomicBool::new(false));
    let count = Arc::new(AtomicUsize::new(0));
    let server = {
        let stop = Arc::clone(&stop);
        let count = Arc::clone(&count);
        std::thread::spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                let Some(mut stream) = listener.accept().ok().map(|(stream, _)| stream) else {
                    std::thread::sleep(Duration::from_millis(5));
                    continue;
                };
                stream.set_nonblocking(false).unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .unwrap();
                let mut raw = [0; 4096];
                let length = stream.read(&mut raw).unwrap();
                assert!(raw[..length].starts_with(b"GET /attached.png "));
                count.fetch_add(1, Ordering::Relaxed);
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    png.len()
                )
                .unwrap();
                stream.write_all(&png).unwrap();
            }
        })
    };
    fs::write(root.join("allow.md"), format!("![photo]({target})")).unwrap();
    fs::write(root.join("deny.md"), format!("<img src='{target}'>")).unwrap();
    write_json(
        root,
        "work.json",
        &json!({"schema_version":"mdocr-work-1","output_dir":"out","documents":[
            {"path":"allow.md", "select_images": [target], "download_remote":true},
            {"path":"deny.md"}
        ]}),
    );
    let args = ["accept", "--plan", "work.json"];
    let accepted = command(root, &args);
    let repeated = if accepted.status.success() {
        Some(command(root, &args))
    } else {
        None
    };
    stop.store(true, Ordering::Relaxed);
    server.join().unwrap();
    assert!(
        accepted.status.success(),
        "{}",
        String::from_utf8_lossy(&accepted.stderr)
    );
    assert!(repeated.unwrap().status.success());
    let plan = json_file(&root.join("work.mdocr/plan.json"));
    assert!(plan["documents"][0]["images"][0]["error"].is_null());
    assert_eq!(
        plan["documents"][1]["images"][0]["error"],
        "REMOTE_IMAGE_NOT_DOWNLOADED"
    );
    assert_eq!(count.load(Ordering::Relaxed), 1);
}
