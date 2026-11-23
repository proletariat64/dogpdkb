use std::path::Path;

use anyhow::{Context, Result};

use crate::{
    config::Config,
    provider,
    summary::{Outcome, Summary},
};

pub(crate) fn run(config: Option<&Path>) -> Result<Outcome> {
    let settings = Config::load(None, config).context("invalid_config")?;
    let score = &settings.score;
    let verify_credentials =
        score.oauth_file.is_some() || score.request_budget.is_some_and(|budget| budget > 0);
    let mut summary = Summary::new("doctor", "complete").count("checks", 1);
    if !verify_credentials {
        return Ok(summary.exit(0));
    }

    let connection = provider::Connection {
        oauth_file: score.oauth_file.clone(),
        proxy: score.proxy.clone(),
        ca_file: score.ca_file.clone(),
        endpoint: score.endpoint.clone(),
    };
    match provider::check_oauth_settings(&score.model, &score.effort, &connection) {
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
