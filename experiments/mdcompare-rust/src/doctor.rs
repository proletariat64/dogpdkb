use std::path::Path;

use anyhow::{Context, Result};

use crate::{
    config::{Config, Scope},
    provider,
    summary::{Outcome, Summary},
};

pub(crate) fn run(config: Option<&Path>) -> Result<Outcome> {
    let settings = Config::load(None, config, Scope::Doctor).context("invalid_config")?;
    let score = &settings.score;
    let verify_credentials =
        score.oauth_file.is_some() || score.request_budget.is_some_and(|budget| budget > 0);
    let mut summary = Summary::new("doctor", "complete").count("checks", 1);
    let verify_transport =
        score.endpoint.is_some() || score.proxy.is_some() || score.ca_file.is_some();
    if !verify_credentials && !verify_transport {
        return Ok(summary.exit(0));
    }

    let connection = provider::Connection {
        oauth_file: score.oauth_file.clone(),
        proxy: score.proxy.clone(),
        ca_file: score.ca_file.clone(),
        endpoint: score.endpoint.clone(),
    };
    let check = if verify_credentials {
        provider::check_oauth_settings(&score.model, &score.effort, &connection)
    } else {
        provider::check_transport_settings(&connection).map(|()| false)
    };
    match check {
        Ok(expired) => {
            summary = summary.count("checks", 2);
            if expired {
                summary = summary.reason("oauth_expired");
            }
            Ok(summary.exit(0))
        }
        Err(code) => Ok(Summary::new("doctor", "partial")
            .count("checks", 2)
            .count("failed", 1)
            .reason(code)
            .exit(2)),
    }
}
