use std::path::Path;

use serde_json::{Value, json};

use crate::{
    config::{Config, Scope},
    provider::{OAuthReadiness, Provider},
    summary::{Outcome, Progress, Summary},
};

fn check(name: &str, status: &str, reason_code: Option<&str>, hint: Option<&str>) -> Value {
    json!({"name": name, "status": status, "reason_code": reason_code, "hint": hint})
}

pub(crate) fn run(config: Option<&Path>, progress: &Progress) -> Outcome {
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
                "llm": {"status": "not_checked", "inference": "not_checked",
                    "provider_status": "not_checked", "oauth_remote_status": "not_checked",
                    "model_status": "not_checked"},
            }))
            .exit(1);
    };

    let score = settings.score;
    let provider = Provider::new(&score);
    let readiness = provider.diagnostics();
    let backend = readiness.backend;
    let transport = readiness.transport;
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

    let scoring_enabled = readiness.scoring_enabled;
    checks.push(match readiness.oauth {
        OAuthReadiness::Passed => check("llm_oauth", "passed", None, None),
        OAuthReadiness::Skipped(code) => check(
            "llm_oauth",
            "skipped",
            Some(code),
            (code == "oauth_not_configured")
                .then_some("Set [score].oauth_file to a local OAuth credentials file to check it."),
        ),
        OAuthReadiness::Failed(code) => check(
            "llm_oauth",
            "failed",
            Some(code),
            Some(match code {
                "oauth_expired" => "Refresh the saved OAuth credential before checking the model.",
                "oauth_file_unreadable" => {
                    "Check the path and permissions of [score].oauth_file."
                }
                "oauth_file_invalid" => "Use a valid OAuth credentials JSON file.",
                "oauth_provider_missing" => {
                    "Log in to the configured provider and set [score].oauth_file to its credential file."
                }
                "oauth_credential_invalid" => {
                    "Log in again to replace the invalid credential for this provider."
                }
                _ => "Set [score].oauth_file to a readable OAuth credentials file.",
            }),
        ),
    });
    let skip = readiness.probe_skip;
    let probe = skip.is_none().then(|| {
        progress.update("probe", 0, 0, 1, 1);
        let outcome = provider.probe();
        progress.update("probe", 0, 0, 1, 0);
        outcome
    });
    let http_status = probe.as_ref().and_then(|outcome| outcome.http_status);
    let failure = probe.as_ref().and_then(|outcome| {
        outcome.reason.or_else(|| {
            outcome
                .reply
                .as_deref()
                .is_none_or(|reply| reply.trim().is_empty())
                .then_some("incomplete_model_response")
        })
    });
    let inference = if let Some(reason) = skip {
        checks.push(check("llm_inference", "skipped", Some(reason), None));
        "not_checked"
    } else if let Some(reason) = failure {
        checks.push(check(
            "llm_inference",
            "failed",
            Some(reason),
            Some(match reason {
                "oauth_rejected" if score.endpoint.is_some() => {
                    "Check the local test endpoint. The probe sends no credentials to it."
                }
                "oauth_rejected" => "Log in again to the configured provider.",
                "provider_access_denied" => {
                    "Check the provider account permissions for this model."
                }
                "model_rejected" => "Check model availability for this provider and account.",
                "rate_limited" => "Check the provider rate limit or quota.",
                "provider_unavailable" => {
                    "The endpoint returned a server error. Check the provider status."
                }
                "probe_timeout" => {
                    "The online probe exceeded 20 seconds. Check connectivity and provider latency."
                }
                "provider_unreachable" => "Check connectivity, TLS and proxy settings.",
                "incomplete_model_response" => {
                    "The model did not finish a nonempty text reply within the probe limits."
                }
                _ => "Review the provider, model and local OAuth configuration.",
            }),
        ));
        "failed"
    } else {
        checks.push(check("llm_inference", "passed", None, None));
        "passed"
    };
    let provider_status = match (http_status, failure) {
        (_, Some("probe_timeout")) => "timeout",
        (Some(_), _) => "responded",
        (_, Some("provider_unreachable")) => "unreachable",
        _ => "not_checked",
    };
    let oauth_remote_status = if score.endpoint.is_some() {
        "not_applicable"
    } else {
        match http_status {
            Some(401) => "rejected",
            Some(200..=299) => "accepted",
            _ => "not_verified",
        }
    };
    let model_status = match (inference, failure) {
        ("passed", _) => "passed",
        (_, Some("model_rejected")) => "rejected",
        ("not_checked", _) => "not_checked",
        _ => "not_verified",
    };
    let failed = checks
        .iter()
        .filter(|check| check["status"] == "failed")
        .count();
    let skipped = checks
        .iter()
        .filter(|check| check["status"] == "skipped")
        .count();
    let reason = checks
        .iter()
        .find(|check| check["status"] == "failed")
        .and_then(|check| check["reason_code"].as_str());
    let status = if inference == "passed" {
        "online"
    } else if inference == "failed" {
        "probe_failed"
    } else if failed > 0 {
        "prerequisites_failed"
    } else if !scoring_enabled {
        if score.request_budget.is_some() {
            "disabled"
        } else {
            "not_configured"
        }
    } else {
        "local_prerequisites_passed"
    };
    let mut summary = Summary::new("doctor", if failed == 0 { "complete" } else { "partial" })
        .count("checks", checks.len() - skipped)
        .count("failed", failed)
        .count("warnings", 0)
        .count("skipped", skipped)
        .count("probe_attempts", usize::from(probe.is_some()));
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
            "transport": if score.endpoint.is_some() { "explicit_endpoint" } else { "provider_default" },
            "oauth_file_configured": score.oauth_file.is_some(),
            "proxy_configured": score.proxy.is_some(),
            "ca_file_configured": score.ca_file.is_some(),
            "inference": inference,
            "provider_status": provider_status,
            "http_status": http_status,
            "oauth_remote_status": oauth_remote_status,
            "model_status": model_status,
        },
        "checks": checks,
    })).exit(if failed == 0 { 0 } else { 2 })
}
