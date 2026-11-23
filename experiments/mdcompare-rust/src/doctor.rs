use std::path::Path;

use serde_json::{Value, json};

use crate::{
    config::{Config, Scope},
    provider,
    summary::{Outcome, Summary},
};

fn check(name: &str, status: &str, reason_code: Option<&str>, hint: Option<&str>) -> Value {
    json!({"name": name, "status": status, "reason_code": reason_code, "hint": hint})
}

pub(crate) fn run(config: Option<&Path>) -> Outcome {
    let runtime = json!({
        "version": env!("CARGO_PKG_VERSION"),
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
    });
    let configuration = if config.is_some() {
        "explicit"
    } else {
        "defaults"
    };
    let Ok(settings) = Config::load(None, config, Scope::Doctor) else {
        return Summary::new("doctor", "error")
            .reason("invalid_config")
            .count("checks", 1)
            .count("failed", 1)
            .diagnostics(json!({
                "runtime": runtime,
                "configuration": configuration,
                "checks": [check("config", "failed", Some("invalid_config"),
                    Some("Check the TOML file path, syntax, and options."))],
                "llm": {"status": "not_checked", "inference": "not_checked"},
            }))
            .exit(1);
    };

    let score = settings.score;
    let connection = provider::Connection {
        oauth_file: score.oauth_file,
        proxy: score.proxy,
        ca_file: score.ca_file,
        endpoint: score.endpoint,
    };
    let backend = provider::check_model(&score.model, &score.effort);
    let transport = provider::check_transport_settings(&connection);
    let mut checks = vec![check("config", "passed", None, None)];
    checks.push(match backend {
        Ok(_) => check("llm_model", "passed", None, None),
        Err(code) => check(
            "llm_model",
            "failed",
            Some(code),
            Some("Set a supported model and reasoning effort in [score]."),
        ),
    });
    checks.push(match transport {
        Ok(()) => check("llm_transport", "passed", None, None),
        Err(code) => check("llm_transport", "failed", Some(code), Some(match code {
            "ca_unavailable" => "Check that [score].ca_file is a readable PEM certificate bundle.",
            "proxy_unavailable" => "Check the URL syntax in [score].proxy.",
            _ => "Omit [score].endpoint for the provider default, or use a local HTTP test endpoint without a proxy.",
        })),
    });

    let scoring_enabled = score.request_budget.is_some_and(|budget| budget > 0);
    let oauth = if connection.endpoint.is_some() {
        check("llm_oauth", "skipped", Some("local_test_endpoint"), None)
    } else if connection.oauth_file.is_none() && !scoring_enabled {
        check(
            "llm_oauth",
            "skipped",
            Some("oauth_not_configured"),
            Some("Set [score].oauth_file to a local OAuth credentials file to check it."),
        )
    } else if backend.is_err() {
        check("llm_oauth", "skipped", Some("unsupported_model"), None)
    } else {
        match provider::check_saved_oauth(&score.model, &score.effort, &connection) {
            Ok(false) => check("llm_oauth", "passed", None, None),
            Ok(true) => check(
                "llm_oauth",
                "warning",
                Some("oauth_expired"),
                Some("The saved access token has expired. Refresh is only attempted by score."),
            ),
            Err(code) => check(
                "llm_oauth",
                "failed",
                Some(code),
                Some(
                    "Set [score].oauth_file to a readable credential file with an OAuth entry for this provider.",
                ),
            ),
        }
    };
    checks.push(oauth);
    checks.push(check(
        "llm_inference",
        "skipped",
        Some("offline_doctor"),
        None,
    ));
    let failed = checks
        .iter()
        .filter(|check| check["status"] == "failed")
        .count();
    let warnings = checks
        .iter()
        .filter(|check| check["status"] == "warning")
        .count();
    let skipped = checks
        .iter()
        .filter(|check| check["status"] == "skipped")
        .count();
    let reason = ["failed", "warning"].into_iter().find_map(|status| {
        checks
            .iter()
            .find(|check| check["status"] == status)
            .and_then(|check| check["reason_code"].as_str())
    });
    let status = if failed > 0 {
        "prerequisites_failed"
    } else if !scoring_enabled {
        if score.request_budget.is_some() {
            "disabled"
        } else {
            "not_configured"
        }
    } else if warnings > 0 {
        "refresh_required"
    } else {
        "local_prerequisites_passed"
    };
    let mut summary = Summary::new("doctor", if failed == 0 { "complete" } else { "partial" })
        .count("checks", checks.len() - skipped)
        .count("failed", failed)
        .count("warnings", warnings)
        .count("skipped", skipped);
    if let Some(reason) = reason {
        summary = summary.reason(reason);
    }
    summary.diagnostics(json!({
        "runtime": runtime,
        "configuration": configuration,
        "llm": {
            "operation": "semantic_score",
            "status": status,
            "provider": backend.ok(),
            "model": backend.ok().map(|_| score.model),
            "reasoning_effort": score.effort,
            "request_budget": score.request_budget,
            "concurrency": score.concurrency,
            "max_evidence_bytes": score.max_evidence_bytes,
            "transport": if connection.endpoint.is_some() { "explicit_endpoint" } else { "provider_default" },
            "oauth_file_configured": connection.oauth_file.is_some(),
            "proxy_configured": connection.proxy.is_some(),
            "ca_file_configured": connection.ca_file.is_some(),
            "inference": "not_checked",
        },
        "checks": checks,
    })).exit(if failed == 0 { 0 } else { 2 })
}
