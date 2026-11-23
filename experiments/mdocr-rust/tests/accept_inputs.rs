use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};

fn command(root: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_mdocr"))
        .current_dir(root)
        .env_clear()
        .args(args)
        .output()
        .expect("run mdocr")
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
