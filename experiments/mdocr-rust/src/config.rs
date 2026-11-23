use crate::{artifacts, llm, provider};
use anyhow::{Context, Result, ensure};
use pulldown_cmark::{Event, Parser, Tag};
use reqwest::{Url, header::HeaderValue};
use serde::Deserialize;
use serde_json::{Value, json};
use std::fs;
use std::io::{Cursor, ErrorKind};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

const DEFAULT_MODEL: &str = "openai/gpt-6.1-terra";
const DEFAULT_PROMPT: &str = include_str!("../../../src/mdocr/default_prompt.txt");

#[derive(Clone, Copy, Default, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
enum Mode {
    #[default]
    Llm,
    Paddle,
}

#[derive(Clone, Copy, Default, Deserialize)]
#[serde(rename_all = "lowercase")]
enum Effort {
    Low,
    #[default]
    Medium,
    High,
    Xhigh,
}

impl Effort {
    fn as_str(self) -> &'static str {
        match self {
            Self::Low => "low",
            Self::Medium => "medium",
            Self::High => "high",
            Self::Xhigh => "xhigh",
        }
    }
}

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Options {
    mode: Option<Mode>,
    concurrency: Option<u64>,
    request_budget: Option<u64>,
    admission_seconds: Option<f64>,
    retry_rounds: Option<u64>,
    cache_dir: Option<PathBuf>,
    model: Option<String>,
    effort: Option<Effort>,
    prompt_file: Option<PathBuf>,
    mermaid_repair_rounds: Option<u64>,
    oauth_file: Option<PathBuf>,
    endpoint: Option<String>,
    token_file: Option<PathBuf>,
    proxy_url: Option<String>,
    ca_file: Option<PathBuf>,
}

pub(crate) fn source(work: Option<&Path>, explicit: Option<&Path>) -> Result<Option<PathBuf>> {
    if let Some(path) = explicit {
        return path
            .canonicalize()
            .map(Some)
            .with_context(|| format!("cannot resolve configuration file {}", path.display()));
    }
    let Some(work) = work else {
        return Ok(None);
    };
    let work = work
        .canonicalize()
        .with_context(|| format!("cannot resolve work plan {}", work.display()))?;
    let candidate = work
        .parent()
        .context("work plan has no parent directory")?
        .join("default.toml");
    match fs::symlink_metadata(&candidate) {
        Ok(_) => candidate
            .canonicalize()
            .map(Some)
            .with_context(|| format!("cannot resolve configuration file {}", candidate.display())),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error)
            .with_context(|| format!("cannot inspect configuration file {}", candidate.display())),
    }
}

pub(crate) fn load(work: Option<&Path>, explicit: Option<&Path>) -> Result<Value> {
    load_from(source(work, explicit)?.as_deref())
}

fn load_from(source: Option<&Path>) -> Result<Value> {
    let (options, directory) = if let Some(path) = source {
        let text = read_text_file(path, "configuration")?;
        let options: Options = toml::from_str(&text).map_err(|error| {
            anyhow::anyhow!("invalid TOML in {}: {}", path.display(), error.message())
        })?;
        let directory = path
            .parent()
            .context("configuration has no parent directory")?;
        (options, Some(directory))
    } else {
        (Options::default(), None)
    };
    options.effective(directory)
}

fn read_text_file(path: &Path, label: &str) -> Result<String> {
    let metadata = fs::metadata(path)
        .with_context(|| format!("cannot inspect {label} file {}", path.display()))?;
    ensure!(
        metadata.is_file(),
        "{label} path is not a file: {}",
        path.display()
    );
    fs::read_to_string(path).with_context(|| format!("cannot read {label} file {}", path.display()))
}

fn config_path(
    directory: Option<&Path>,
    path: Option<PathBuf>,
    key: &str,
) -> Result<Option<PathBuf>> {
    let Some(path) = path else {
        return Ok(None);
    };
    ensure!(!path.as_os_str().is_empty(), "{key} cannot be empty");
    let full = directory
        .context("path requires a configuration file")?
        .join(path);
    artifacts::resolve(&full).map(Some)
}

fn credential_reference(
    directory: Option<&Path>,
    path: Option<PathBuf>,
    key: &str,
) -> Result<Option<PathBuf>> {
    let Some(path) = path else {
        return Ok(None);
    };
    ensure!(!path.as_os_str().is_empty(), "{key} cannot be empty");
    let full = directory
        .context("credential path requires a configuration file")?
        .join(path);
    let name = full
        .file_name()
        .with_context(|| format!("{key} must name a file"))?;
    let parent = full
        .parent()
        .context("credential path has no parent directory")?;
    // Freeze the reference, so rotating a file or replacing a final symlink at
    // that reference does not change the accepted configuration.
    Ok(Some(artifacts::resolve(parent)?.join(name)))
}

fn validate_url(value: &str, name: &str) -> Result<String> {
    let url = Url::parse(value)
        .ok()
        .filter(|url| {
            matches!(url.scheme(), "http" | "https")
                && url.host().is_some()
                && url.username().is_empty()
                && url.password().is_none()
                && url.query().is_none()
                && url.fragment().is_none()
        })
        .with_context(|| {
            format!("{name} must be an HTTP(S) URL without credentials, query, or fragment")
        })?;
    if name == "proxy_url" {
        ensure!(url.path() == "/", "proxy_url must not contain a path");
    }
    Ok(url.to_string())
}

impl Options {
    fn effective(self, directory: Option<&Path>) -> Result<Value> {
        let mode = self.mode.unwrap_or_default();
        if mode == Mode::Llm {
            ensure!(
                self.endpoint.is_none() && self.token_file.is_none(),
                "endpoint and token_file require Paddle mode"
            );
        } else {
            ensure!(
                self.model.is_none()
                    && self.effort.is_none()
                    && self.prompt_file.is_none()
                    && self.mermaid_repair_rounds.is_none()
                    && self.oauth_file.is_none(),
                "model, effort, prompt_file, mermaid_repair_rounds, and oauth_file require LLM mode"
            );
        }

        let concurrency = self.concurrency.unwrap_or(3);
        ensure!(
            (1..=100).contains(&concurrency),
            "concurrency must be from 1 to 100"
        );
        let admission_seconds = self.admission_seconds.unwrap_or(900.0);
        let duration = Duration::try_from_secs_f64(admission_seconds)
            .context("admission_seconds must be a nonnegative finite duration")?;
        ensure!(
            Instant::now().checked_add(duration).is_some(),
            "admission_seconds is too large"
        );
        let cache_dir = config_path(directory, self.cache_dir, "cache_dir")?;
        let ca_file = config_path(directory, self.ca_file, "ca_file")?;
        let proxy_url = self
            .proxy_url
            .as_deref()
            .map(|proxy| validate_url(proxy, "proxy_url"))
            .transpose()?;

        let mut settings = json!({
            "mode": if mode == Mode::Llm { "llm" } else { "paddle" },
            "concurrency": concurrency,
            "request_budget": self.request_budget,
            "admission_seconds": admission_seconds,
            "retry_rounds": self.retry_rounds.unwrap_or(0),
            "cache_dir": cache_dir,
            "endpoint": null,
            "token_file": null,
            "oauth_file": null,
            "prompt_file": null,
            "proxy_url": proxy_url,
            "ca_file": ca_file,
        });
        if mode == Mode::Llm {
            let model = self.model.as_deref().unwrap_or(DEFAULT_MODEL);
            ensure!(
                llm::model_supported(model),
                "model must name an openai/ or openai-codex/ route"
            );
            let prompt_file = config_path(directory, self.prompt_file, "prompt_file")?;
            let prompt = if let Some(path) = &prompt_file {
                let prompt = read_text_file(path, "prompt")?;
                ensure!(
                    !prompt.trim().is_empty(),
                    "prompt_file must contain a prompt"
                );
                prompt
            } else {
                DEFAULT_PROMPT.to_owned()
            };
            settings["model"] = json!(model);
            settings["effort"] = json!(self.effort.unwrap_or_default().as_str());
            settings["llm_schema"] = json!("mdocr-image-content-1");
            settings["prompt_sha256"] = json!(artifacts::sha(prompt.as_bytes()));
            settings["prompt"] = json!(prompt);
            settings["prompt_file"] = json!(prompt_file);
            settings["mermaid_repair_rounds"] = json!(self.mermaid_repair_rounds.unwrap_or(3));
            settings["oauth_file"] = json!(credential_reference(
                directory,
                self.oauth_file,
                "oauth_file"
            )?);
        } else {
            let endpoint = validate_url(
                self.endpoint.as_deref().unwrap_or(crate::paddle::JOB_URL),
                "endpoint",
            )?;
            settings["endpoint"] = json!(endpoint);
            settings["token_file"] = json!(credential_reference(
                directory,
                self.token_file,
                "token_file"
            )?);
        }
        Ok(settings)
    }
}

pub(crate) fn paddle_token(settings: &Value) -> Result<String, provider::Failure> {
    let unavailable = || provider::Failure::known("OCR_AUTH_UNAVAILABLE");
    let path = settings["token_file"]
        .as_str()
        .map(Path::new)
        .filter(|path| path.is_absolute())
        .ok_or_else(unavailable)?;
    let text = read_text_file(path, "token").map_err(|_| unavailable())?;
    let token = text.trim();
    if token.is_empty()
        || token.chars().any(char::is_whitespace)
        || HeaderValue::from_str(token).is_err()
    {
        return Err(unavailable());
    }
    Ok(token.to_owned())
}

fn check(name: &str, status: &str, reason: &str) -> Value {
    json!({"name": name, "status": status, "reason_code": reason})
}

fn embedded_image_check() -> bool {
    let mut data = Cursor::new(Vec::new());
    image::DynamicImage::new_rgb8(2, 2)
        .write_to(&mut data, image::ImageFormat::Png)
        .is_ok()
        && llm::prepare(data.get_ref()).is_ok()
}

pub(crate) fn doctor(explicit: Option<&Path>) -> Result<(u8, Value)> {
    let source = source(None, explicit)?;
    let settings = load_from(source.as_deref())?;
    let mode = settings["mode"]
        .as_str()
        .context("configuration missing provider mode")?;
    let credential_key = if mode == "paddle" {
        "token_file"
    } else {
        "oauth_file"
    };
    let credential = if settings[credential_key].is_null() {
        check("credentials", "fail", "credential_file_required")
    } else if mode == "paddle" {
        match paddle_token(&settings) {
            Ok(_) => check("credentials", "pass", "credential_file_readable"),
            Err(_) => check("credentials", "fail", "credential_file_invalid"),
        }
    } else {
        match llm::oauth_expired(&settings) {
            Ok(false) => check("credentials", "pass", "oauth_credential_readable"),
            Ok(true) => check("credentials", "warn", "oauth_credential_expired"),
            Err(_) => check("credentials", "fail", "oauth_credential_invalid"),
        }
    };

    let markdown_ok = Parser::new("![test](image.png)")
        .any(|event| matches!(event, Event::Start(Tag::Image { .. })));
    let mermaid_ok = merman_core::SemanticParser::new()
        .parse_diagram_sync("flowchart TD\nA --> B")
        .is_ok();
    let codec_ok = embedded_image_check();
    let ca = if settings["ca_file"].is_null() {
        check("ca_file", "pass", "embedded_trust_roots")
    } else if provider::ca_certificates(&settings).is_ok() {
        check("ca_file", "pass", "ca_file_readable")
    } else {
        check("ca_file", "fail", "ca_file_invalid")
    };
    let mut checks = vec![
        check("configuration", "pass", "configuration_valid"),
        credential,
        ca,
    ];
    if mode == "llm" {
        checks.push(if settings["prompt_file"].is_null() {
            check("prompt", "pass", "embedded_prompt")
        } else {
            check("prompt", "pass", "prompt_file_readable")
        });
    }
    for (name, ok) in [
        ("markdown", markdown_ok),
        ("image", codec_ok),
        ("mermaid", mermaid_ok),
    ] {
        checks.push(if ok {
            check(name, "pass", "embedded_capability_available")
        } else {
            check(name, "fail", "embedded_capability_failed")
        });
    }
    let ready = checks.iter().all(|check| check["status"] != "fail");
    let config_source = match source {
        Some(path) => json!(path),
        None => json!("built-in"),
    };
    Ok((
        if ready { 0 } else { 2 },
        json!({
            "status": if ready { "ready" } else { "unready" },
            "config_source": config_source,
            "mode": mode,
            "network_checked": false,
            "checks": checks,
        }),
    ))
}
