use serde_json::{Value, json};
use std::fs;
use std::process::Command;

fn compare(
    original: &str,
    incoming: &str,
    scope: Option<Value>,
    insertion: Option<(usize, &str)>,
) -> (Value, Value) {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path();
    fs::write(root.join("old.md"), original).unwrap();
    fs::write(root.join("new.md"), incoming).unwrap();
    let snapshot = |path| {
        json!({
            "path": path, "content_kind":"raw",
            "metadata":{"name":"Comparison","logical_path":"Body","source_ref":"book:test"}
        })
    };
    let mut old = snapshot("old.md");
    if let Some(scope) = scope {
        old["body_scope"] = scope;
    }
    let mut plan = json!({
        "schema_version":"mdcompare-work-1", "operation":"semantic",
        "documents":[{"knowledge_base":"book","document_id":"test",
                      "baseline":old,"incoming":snapshot("new.md")}],
    });
    if let Some((offset, insertion)) = insertion {
        fs::write(root.join("old-copy.md"), original).unwrap();
        fs::write(
            root.join("recovery.json"),
            json!({
                "schema_version":"mdcompare-body-recovery-1",
                "documents":[{"knowledge_base":"book","document_id":"test","side":"baseline",
                              "source_copy":"old-copy.md",
                              "insertions":[{"byte_offset":offset,"text":insertion}]}],
            })
            .to_string(),
        )
        .unwrap();
        plan["body_recovery"] = json!("recovery.json");
    }
    fs::write(root.join("work.json"), plan.to_string()).unwrap();
    let result = Command::new(env!("CARGO_BIN_EXE_mdcomparer"))
        .args(["run", "--plan", "work.json"])
        .current_dir(root)
        .env_clear()
        .output()
        .unwrap();
    assert!(
        matches!(result.status.code(), Some(0 | 2)),
        "{} {}",
        String::from_utf8_lossy(&result.stderr),
        String::from_utf8_lossy(&result.stdout)
    );
    let report: Value =
        serde_json::from_slice(&fs::read(root.join("work.mdcompare/result.json")).unwrap())
            .unwrap();
    let evidence = serde_json::from_slice(
        &fs::read(root.join("work.mdcompare/evidence/000000.json")).unwrap(),
    )
    .unwrap();
    (report["results"][0].clone(), evidence)
}

#[test]
fn exact_occurrences_take_priority_over_context_moves() {
    let before = "# A\n\nSame text.\n\nSame text.\n";
    let after = "# B\n\nSame text.\n\n# A\n\nSame text.\n\nSame text.\n";
    let (_, evidence) = compare(before, after, None, None);
    let operations = evidence["changes"].as_array().unwrap();
    assert_eq!(operations[1]["operation"], "add");
    assert_eq!(operations[1]["old_ref"], Value::Null);
    assert_eq!(operations[3]["old_ref"], 1);
    assert_eq!(operations[4]["old_ref"], 2);
}

#[test]
fn recovery_at_unicode_line_boundary_reports_a_source_line() {
    let original = "Intro.\u{2028}Policy.";
    let offset = original.find("Policy.").unwrap();
    let (report, evidence) = compare(original, "Intro.\n\nPolicy.", None, Some((offset, "\n")));
    assert_ne!(report["action"], "DEFER");
    assert_eq!(evidence["recovery"][0]["source_line"], 2);
    assert_eq!(evidence["old_units"][1]["start_line"], 2);
    assert_eq!(evidence["old_units"][1]["projected_start_line"], 3);
}

#[test]
fn recovery_projects_original_declared_marker_positions() {
    let original = "Intro.\n\n<!-- mdocr:ocr-begin -->\nnoise\n<!-- mdocr:ocr-end -->\n\nPolicy.\n";
    let scope = json!({"version":"generated-regions-v1",
                        "regions":[{"kind":"mdocr_ocr","start_line":3,"end_line":5}]});
    let offset = original.find("<!-- mdocr:ocr-begin -->").unwrap();
    let (report, evidence) = compare(
        original,
        "Intro.\n\nPolicy.\n",
        Some(scope),
        Some((offset, "\n")),
    );
    assert_eq!(report["action"], "SKIP", "{report}");
    assert_eq!(evidence["old_units"][1]["start_line"], 7);
    assert_eq!(evidence["old_units"][1]["projected_start_line"], 8);
    assert!(!evidence["old_units"].to_string().contains("noise"));

    let projected_only = json!({"version":"generated-regions-v1",
                                 "regions":[{"kind":"mdocr_ocr","start_line":4,"end_line":6}]});
    let (invalid, _) = compare(
        original,
        "Intro.\n\nPolicy.\n",
        Some(projected_only),
        Some((offset, "\n")),
    );
    assert_eq!(invalid["action"], "DEFER", "{invalid}");
    assert_eq!(
        invalid["reason_codes"],
        json!(["BODY_SCOPE_LOCATION_MISMATCH"])
    );
}

#[test]
fn legacy_transcription_footer_is_scoped_to_explicit_recovery() {
    let original = "规则\n\n> **图片转写：**\n> OCR noise\n业务正文\n";
    let incoming = "规则\n\n业务正文\n";
    let offset = original.find("业务正文").unwrap();
    let (unrepaired, _) = compare(original, incoming, None, None);
    assert_eq!(unrepaired["action"], "COMPILE");
    let (repaired, evidence) = compare(original, incoming, None, Some((offset, "> 转写说明：\n")));
    assert_eq!(repaired["action"], "SKIP", "{repaired}");
    assert_eq!(evidence["recovery"][0]["source_line"], 5);
    assert_eq!(evidence["old_units"][1]["start_line"], 5);
    assert_eq!(evidence["old_units"][1]["projected_start_line"], 6);
    assert!(!evidence["old_units"].to_string().contains("noise"));
}

#[test]
fn explicit_recovery_can_close_a_legacy_generated_comment() {
    let original = "Intro.\n\n<!-- 这是一张图片，ocr 内容为：\nOCR noise\nPolicy.\n";
    let offset = original.find("Policy.").unwrap();
    let (row, evidence) = compare(
        original,
        "Intro.\n\nPolicy.\n",
        None,
        Some((offset, "-->\n")),
    );
    assert_eq!(row["action"], "SKIP", "{row}");
    assert!(!evidence["old_units"].to_string().contains("noise"));
}
