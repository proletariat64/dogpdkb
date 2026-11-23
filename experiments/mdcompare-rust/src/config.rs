use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    pub compare: CompareConfig,
    pub report: ReportConfig,
    pub score: ScoreConfig,
    pub route: RouteConfig,
    #[serde(skip)]
    pub source: Option<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct CompareConfig {
    pub max_file_bytes: u64,
    pub max_diff_work: usize,
    pub normalize_line_endings: bool,
}

impl Default for CompareConfig {
    fn default() -> Self {
        Self {
            max_file_bytes: 16 * 1024 * 1024,
            max_diff_work: 2_000_000,
            normalize_line_endings: false,
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ReportConfig {
    pub markdown: bool,
    pub include_body_excerpts: bool,
    pub max_excerpt_chars: Option<usize>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CompareSettings {
    pub compare: CompareConfig,
    pub report: ReportConfig,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ScoreConfig {
    pub request_budget: Option<usize>,
    pub model: String,
    pub effort: String,
    pub concurrency: u8,
    pub oauth_file: Option<PathBuf>,
    pub proxy: Option<String>,
    pub ca_file: Option<PathBuf>,
    pub max_evidence_bytes: u64,
    pub endpoint: Option<String>,
}

impl Default for ScoreConfig {
    fn default() -> Self {
        Self {
            request_budget: None,
            model: "openai/gpt-6.1-terra".into(),
            effort: "medium".into(),
            concurrency: 3,
            oauth_file: None,
            proxy: None,
            ca_file: None,
            max_evidence_bytes: 65536,
            endpoint: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct RouteConfig {
    pub compile_above: u8,
    pub compile_ambiguous: bool,
}

impl Default for RouteConfig {
    fn default() -> Self {
        Self {
            compile_above: 3,
            compile_ambiguous: false,
        }
    }
}

#[derive(Clone, Copy)]
pub enum Scope {
    Comparison,
    Score,
    Route,
    Doctor,
}

impl Config {
    pub fn load(plan: Option<&Path>, explicit: Option<&Path>, scope: Scope) -> Result<Self> {
        let fallback = plan.and_then(Path::parent).map(|p| p.join("default.toml"));
        let Some(path) = explicit.or(fallback.as_deref()) else {
            return Ok(Self::default());
        };
        let raw = match fs::read_to_string(path) {
            Ok(raw) => raw,
            Err(e) if e.kind() == ErrorKind::NotFound && explicit.is_none() => {
                return Ok(Self::default());
            }
            Err(_) => anyhow::bail!("invalid_config"),
        };
        let mut config: Self =
            toml::from_str(&raw).map_err(|_| anyhow::anyhow!("invalid_config"))?;
        let absolute = std::path::absolute(path).context("invalid_config")?;
        if matches!(scope, Scope::Score | Scope::Doctor) {
            let directory = absolute.parent().context("invalid_config")?;
            for path in [&mut config.score.oauth_file, &mut config.score.ca_file]
                .into_iter()
                .flatten()
            {
                ensure!(!path.as_os_str().is_empty(), "invalid_config");
                *path = directory.join(&*path);
                // Keep the credential filename stable so rotation is allowed.
                let parent = path.parent().context("invalid_config")?;
                let name = path.file_name().context("invalid_config")?;
                *path = crate::input::resolved(parent)
                    .context("invalid_config")?
                    .join(name);
            }
            ensure!(
                (1..=3).contains(&config.score.concurrency),
                "invalid_config"
            );
            ensure!(config.score.max_evidence_bytes > 0, "invalid_config");
            ensure!(!config.score.model.trim().is_empty(), "invalid_config");
            ensure!(
                matches!(
                    config.score.effort.as_str(),
                    "low" | "medium" | "high" | "xhigh"
                ),
                "invalid_config"
            );
        }
        if matches!(scope, Scope::Comparison | Scope::Doctor) {
            ensure!(config.compare.max_file_bytes > 0, "invalid_config");
            ensure!(
                !config.report.include_body_excerpts || config.report.markdown,
                "invalid_config"
            );
            ensure!(
                config.report.include_body_excerpts || config.report.max_excerpt_chars.is_none(),
                "invalid_config"
            );
        }
        if matches!(scope, Scope::Route | Scope::Doctor) {
            ensure!(
                (1..=5).contains(&config.route.compile_above),
                "invalid_config"
            );
        }
        config.source = Some(absolute);
        Ok(config)
    }

    pub fn comparison(mut self, strict: bool) -> CompareSettings {
        self.compare.normalize_line_endings &= strict;
        let mut report = self.report;
        if report.include_body_excerpts {
            report.max_excerpt_chars = Some(report.max_excerpt_chars.unwrap_or(8000));
        }
        CompareSettings {
            compare: self.compare,
            report,
        }
    }
}
