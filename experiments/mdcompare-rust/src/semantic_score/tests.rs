use super::*;
use std::{
    fs,
    sync::atomic::{AtomicBool, AtomicUsize, Ordering::SeqCst},
    time::{Duration, Instant},
};

fn prepared(root: &Path) -> Result<Options> {
    let evidence = root.join("evidence");
    fs::create_dir(&evidence)?;
    let report = root.join("report.json");
    let diff_index = root.join("diff.json");
    let mut results = Vec::new();
    let mut documents = Vec::new();
    for id in ["1", "2", "3", "4"] {
        results.push(json!({
            "knowledge_base": "book",
            "document_id": id,
            "semantic_status": "CHANGED",
            "comparison_complete": true,
            "action": "COMPILE",
            "extraction": {"available": true},
        }));
        let source = json!({"content_kind": "raw", "metadata": {}});
        let old = json!({"kind": "paragraph", "context": [], "key": "old", "text": "Previous requirement"});
        let new = json!({"kind": "paragraph", "context": [], "key": id, "text": id});
        let data = serde_json::to_vec(&json!({
            "knowledge_base": "book",
            "document_id": id,
            "source_inputs": {"baseline": source, "incoming": source},
            "old_units": [old],
            "new_units": [new],
            "changes": [{"operation": "modify", "old_ref": 0, "new_ref": 0}],
        }))?;
        let name = format!("{id}.json");
        fs::write(evidence.join(&name), &data)?;
        documents.push(json!({
            "knowledge_base": "book",
            "document_id": id,
            "evidence": format!("evidence/{name}"),
            "sha256": digest(&data),
        }));
    }
    let report_bytes = serde_json::to_vec(&json!({
        "schema_version": "semantic-1.0",
        "scope": "authored_body",
        "results": results,
    }))?;
    fs::write(&report, &report_bytes)?;
    fs::write(
        &diff_index,
        serde_json::to_vec(&json!({
            "schema_version": "mdcompare-semantic-diff-1",
            "report_sha256": digest(&report_bytes),
            "evidence_directory": "evidence",
            "protected_inputs": [root.join("source.md")],
            "documents": documents,
        }))?,
    )?;
    Ok(Options {
        report,
        diff_index,
        output: root.join("score.json"),
        model: DEFAULT_MODEL.to_owned(),
        effort: DEFAULT_EFFORT.to_owned(),
        request_budget: 3,
        document_ids: vec![],
        concurrency: 2,
        max_evidence_bytes: 65536,
    })
}

fn wait_for(mut condition: impl FnMut() -> bool) -> bool {
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(5) {
        if condition() {
            return true;
        }
        thread::sleep(Duration::from_millis(10));
    }
    false
}

fn read_json(path: &Path) -> Value {
    fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}

fn scored() -> Outcome {
    Outcome {
        reply: Some(
            json!({
                "evaluation_status": "evaluated",
                "change_score": 2,
                "checked_operations": [0],
                "changes": [],
                "reason": "Wording only",
                "missing_evidence": [],
            })
            .to_string(),
        ),
        usage: Some(12),
        http_status: None,
        reason: None,
    }
}

#[test]
fn scorer_admits_durably_drains_rate_limited_tier_and_never_replays() -> Result<()> {
    let tmp = tempfile::tempdir()?;
    let opts = prepared(tmp.path())?;
    let journal = suffixed(&opts.output, ".execution.json")?;
    let started = AtomicUsize::new(0);
    let exited_second = AtomicBool::new(false);
    let valid = AtomicBool::new(true);
    let assessor = |_: &str, _: &str, packet: &str| {
        let request: Value = serde_json::from_str(packet).unwrap_or_default();
        let id = request["new_units"][0]["text"].as_str().unwrap_or_default();
        let attempts = read_json(&journal)["attempts"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        let was_admitted = attempts
            .iter()
            .any(|row| row["document_id"] == id && row["status"] == "admitted");
        valid.fetch_and(was_admitted, SeqCst);
        started.fetch_add(1, SeqCst);
        match id {
            "1" => {
                valid.fetch_and(wait_for(|| started.load(SeqCst) == 2), SeqCst);
                Outcome {
                    reply: None,
                    usage: None,
                    http_status: Some(429),
                    reason: Some("rate_limited"),
                }
            }
            "2" => {
                valid.fetch_and(wait_for(|| read_json(&journal)["concurrency"] == 1), SeqCst);
                exited_second.store(true, SeqCst);
                scored()
            }
            "3" => {
                valid.fetch_and(exited_second.load(SeqCst), SeqCst);
                scored()
            }
            _ => {
                valid.store(false, SeqCst);
                scored()
            }
        }
    };
    assert!(!run_with_assessor(&opts, &assessor)?);
    assert!(valid.load(SeqCst));
    assert_eq!(started.load(SeqCst), 3);
    let result = read_json(&opts.output);
    assert_eq!(result["execution"]["concurrency"], 1);
    assert_eq!(result["execution"]["requests"], 3);
    assert_eq!(result["scores"][0]["document_id"], "2");
    assert_eq!(result["scores"][1]["document_id"], "3");
    assert_eq!(result["unscored"][0]["reason"], "rate_limited");
    assert_eq!(result["unscored"][1]["reason"], "REQUEST_BUDGET_EXHAUSTED");

    let mut state = read_json(&journal);
    state["attempts"][2]["status"] = json!("admitted");
    let stale = state["attempts"][2]
        .as_object_mut()
        .context("Missing fixture admission")?;
    stale.remove("reply");
    stale.remove("score");
    stale.remove("ambiguous");
    fs::write(&journal, serde_json::to_vec(&state)?)?;
    assert!(!run_with_assessor(&opts, &assessor)?);
    assert_eq!(started.load(SeqCst), 3);
    let result = read_json(&opts.output);
    assert_eq!(result["unscored"][1]["document_id"], "3");
    assert_eq!(result["unscored"][1]["reason"], "unknown_outcome");
    Ok(())
}
