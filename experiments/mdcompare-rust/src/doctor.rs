use crate::{
    provider,
    semantic_score::{DEFAULT_EFFORT, DEFAULT_MODEL},
};
use anyhow::{Result, ensure};
use serde_json::json;

const VERSION: &str = env!("CARGO_PKG_VERSION");

pub fn run(
    llm: bool,
    output_json: bool,
    model: Option<&str>,
    effort: Option<&str>,
) -> Result<bool> {
    ensure!(
        llm || (model.is_none() && effort.is_none()),
        "INVALID_ARGUMENTS: --model and --effort require --mode llm"
    );
    let model = model.unwrap_or(DEFAULT_MODEL);
    let effort = effort.unwrap_or(DEFAULT_EFFORT);
    let mut checks = vec![
        json!({ "name": "rust_runtime", "status": "pass", "detail": format!("{} {}", std::env::consts::OS, std::env::consts::ARCH) }),
    ];
    if llm {
        let (status, code, detail) = match provider::check_oauth(model, effort) {
            Ok(true) => (
                "warn",
                "oauth_expired",
                "Stored Pi OAuth is expired; the next live run must refresh it.",
            ),
            Ok(false) => (
                "pass",
                "oauth_stored",
                "Model, effort and stored Pi OAuth are present.",
            ),
            Err("unsupported_model") => (
                "fail",
                "unsupported_model",
                "Select a supported OpenAI or OpenAI Codex Responses model and reasoning effort.",
            ),
            Err("unsupported_effort") => (
                "fail",
                "unsupported_effort",
                "Select a reasoning effort supported by the model.",
            ),
            Err("oauth_required") => (
                "fail",
                "oauth_required",
                "Provide a Pi OAuth credential for the selected provider.",
            ),
            Err("proxy_unavailable") => ("fail", "proxy_unavailable", "Check HTTPS_PROXY."),
            Err(code) => (
                "fail",
                code,
                "The native scoring environment is unavailable.",
            ),
        };
        checks.push(
            json!({ "name": "pi_runtime", "status": status, "code": code, "detail": detail }),
        );
    }
    let ok = checks.iter().all(|item| item["status"] != "fail");
    let mode = if llm { "llm" } else { "offline" };
    if output_json {
        let mut report = json!({
            "schema": "mdcompare-doctor-1",
            "version": VERSION,
            "mode": mode,
            "ok": ok,
            "network_checked": false,
            "checks": checks,
        });
        if llm {
            report["model"] = json!(model);
            report["effort"] = json!(effort);
        }
        println!("{report}");
    } else {
        println!("mdcompare {VERSION} doctor ({mode}, local only)");
        for item in checks {
            let field = |key: &str| -> &str { item[key].as_str().unwrap_or_default() };
            println!(
                "{} {}: {}",
                field("status").to_uppercase(),
                field("name"),
                field("detail")
            );
        }
    }
    Ok(ok)
}
