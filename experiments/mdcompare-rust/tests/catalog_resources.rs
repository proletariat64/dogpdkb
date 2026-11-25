use serde_json::{Value, json};
use std::fs;
use std::path::Path;
use std::process::{Command, Output};

fn invoke(plan: &Path, command: &str) -> Output {
    Command::new(env!("CARGO_BIN_EXE_mdcomparer"))
        .args([command, "--plan"])
        .arg(plan)
        .env_clear()
        .output()
        .unwrap()
}

fn plan(
    root: &Path,
    name: &str,
    resources: Option<Value>,
    ocr: Option<Value>,
) -> std::path::PathBuf {
    let mut incoming = json!({"path":"doc.md","content_kind":"raw",
        "metadata":{"name":"Chart","logical_path":"Chart","source_ref":"chart"}});
    if let Some(resources) = resources {
        incoming["resources"] = resources;
    }
    if let Some(ocr) = ocr {
        incoming["ocr"] = ocr;
    }
    let work = json!({"schema_version":"mdcompare-work-1","operation":"catalog","documents":[
        {"knowledge_base":"book","document_id":"chart","baseline":null,"incoming":incoming}
    ]});
    let plan = root.join(name);
    fs::write(&plan, work.to_string()).unwrap();
    plan
}

fn resources(plan: &Path) -> Vec<Value> {
    let output = invoke(plan, "run");
    assert!(output.status.success(), "{:?}", output);
    let summary: Value = serde_json::from_slice(&output.stdout).unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(summary["artifacts"]["result"].as_str().unwrap()).unwrap(),
    )
    .unwrap();
    report["results"][0]["resources"]
        .as_array()
        .unwrap()
        .clone()
}

#[test]
fn catalog_and_accept_match_url_and_kind_to_distinct_declared_paths() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    fs::write(
        root.join("doc.md"),
        "![chart](chart.png)\n[Download](chart.png)\n",
    )
    .unwrap();
    fs::write(root.join("original.png"), "original image").unwrap();
    fs::write(root.join("download.png"), "attachment bytes").unwrap();
    let work = plan(
        root,
        "declared.json",
        Some(json!([
            {"resource_id":"chart-image","kind":"image","url":"chart.png","path":"original.png"},
            {"resource_id":"chart-download","kind":"attachment","url":"chart.png","path":"download.png"}
        ])),
        None,
    );

    let actual = resources(&work);
    use std::os::unix::fs::MetadataExt;
    assert_eq!(
        fs::metadata(work.with_extension("mdcompare"))
            .unwrap()
            .mode()
            & 0o777,
        0o700
    );
    assert_eq!(
        fs::metadata(work.with_extension("mdcompare.accept.lock"))
            .unwrap()
            .mode()
            & 0o777,
        0o600
    );

    assert_eq!(actual.len(), 2);
    assert_eq!(actual[0]["resource_id"], "chart-image");
    assert_eq!(actual[0]["path"], json!(root.join("original.png")));
    assert_eq!(actual[1]["resource_id"], "chart-download");
    assert_eq!(actual[1]["path"], json!(root.join("download.png")));
}

#[test]
fn implicit_catalog_ids_distinguish_uses_of_the_same_url() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    fs::write(
        root.join("doc.md"),
        "![chart](chart.png)\n[Download](chart.png)\n",
    )
    .unwrap();
    fs::write(root.join("chart.png"), "captured bytes").unwrap();
    let work = plan(root, "inferred.json", None, None);

    let actual = resources(&work);

    assert_eq!(actual.len(), 2);
    assert_ne!(actual[0]["resource_id"], actual[1]["resource_id"]);
    assert_eq!(actual[0]["kind"], "image");
    assert_eq!(actual[1]["kind"], "attachment");
    assert!(
        actual
            .iter()
            .all(|entry| entry["path"] == json!(root.join("chart.png")))
    );
}

#[test]
fn repeated_remote_reference_is_reported_once() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("doc.md"),
        "![chart](https://example.invalid/chart.png)\n![chart](https://example.invalid/chart.png)\n",
    )
    .unwrap();
    let work = plan(dir.path(), "work.json", None, None);
    let output = invoke(&work, "run");
    assert!(output.status.success(), "{output:?}");
    let summary: Value = serde_json::from_slice(&output.stdout).unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(summary["artifacts"]["result"].as_str().unwrap()).unwrap(),
    )
    .unwrap();
    let result = &report["results"][0];
    assert_eq!(result["resources"].as_array().unwrap().len(), 1);
    assert_eq!(result["unverified_resources"].as_array().unwrap().len(), 1);
}

#[test]
fn malformed_resource_target_retains_partial_inventory() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("doc.md"),
        "![chart](https://[invalid]/chart.png)\n",
    )
    .unwrap();
    let work = plan(dir.path(), "work.json", None, None);
    let output = invoke(&work, "run");
    assert_eq!(output.status.code(), Some(2), "{output:?}");
    let summary: Value = serde_json::from_slice(&output.stdout).unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(summary["artifacts"]["result"].as_str().unwrap()).unwrap(),
    )
    .unwrap();
    let row = &report["results"][0];
    assert_eq!(row["status"], "UNKNOWN");
    assert_eq!(row["resources"].as_array().unwrap().len(), 1);
    assert_eq!(
        row["unverified_resources"][0]["reason_code"],
        "RESOURCE_SYNTAX_UNVERIFIED"
    );
}

#[test]
fn catalog_path_encoding_failure_is_reported_without_panicking() {
    use std::ffi::OsString;
    use std::os::unix::{ffi::OsStringExt, fs::symlink};

    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    fs::write(root.join("doc.md"), "Captured report").unwrap();
    let target = OsString::from_vec(b"artifact-\xff".to_vec());
    fs::write(root.join(&target), "captured OCR block").unwrap();
    symlink(target, root.join("alias")).unwrap();
    let work = plan(
        root,
        "ocr.json",
        None,
        Some(json!({"images":[{"artifact":"alias"}]})),
    );

    let output = invoke(&work, "run");

    assert_eq!(output.status.code(), Some(1), "{:?}", output);
    let summary: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(summary["reason_code"], "invalid_input");
}
