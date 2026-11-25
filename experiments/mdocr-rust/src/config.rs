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
            let diagnostic = redacted_toml_error(&text, &error);
            anyhow::anyhow!("invalid TOML in {}: {diagnostic}", path.display())
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

fn redacted_toml_error(text: &str, error: &toml::de::Error) -> String {
    let message = error.message();
    let kind = if message.starts_with("unknown field") {
        "unknown field"
    } else if message.starts_with("unknown variant") {
        "unsupported value"
    } else if message.starts_with("invalid type") {
        "invalid type"
    } else if message.starts_with("duplicate") {
        "duplicate field"
    } else {
        "invalid syntax or value"
    };
    let Some(prefix) = error.span().and_then(|span| text.get(..span.start)) else {
        return kind.to_owned();
    };
    let line = prefix.bytes().filter(|&byte| byte == b'\n').count() + 1;
    let column = prefix
        .rsplit('\n')
        .next()
        .unwrap_or_default()
        .chars()
        .count()
        + 1;
    format!("{kind} at line {line}, column {column}")
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

fn with_hint(mut check: Value, key: &str, hint: &str) -> Value {
    check["config_key"] = json!(key);
    check["hint"] = json!(hint);
    check
}

fn oauth_check(auth: &Result<llm::DoctorAuth<'_>, &'static str>) -> Value {
    let Err(reason) = auth else {
        return check("credentials", "pass", "oauth_credential_readable");
    };
    let (status, hint) = match *reason {
        "credential_not_configured" => ("warn", "Set oauth_file to check the provider live."),
        "oauth_file_missing" => ("fail", "Check that the configured OAuth file exists."),
        "oauth_file_unreadable" => ("fail", "Check that the configured OAuth file is readable."),
        "oauth_credential_expired" => {
            ("fail", "Renew the selected provider login before retrying.")
        }
        "clock_unavailable" => ("fail", "Check the system clock."),
        _ => (
            "fail",
            "Check that the OAuth file contains a valid credential for this provider.",
        ),
    };
    with_hint(check("credentials", status, reason), "oauth_file", hint)
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
    let oauth = (mode == "llm").then(|| llm::DoctorAuth::load(&settings));
    let credential = if let Some(auth) = &oauth {
        oauth_check(auth)
    } else if settings["token_file"].is_null() {
        with_hint(
            check("credentials", "fail", "credential_file_required"),
            "token_file",
            "Set token_file to a readable Paddle token file.",
        )
    } else {
        match paddle_token(&settings) {
            Ok(_) => check("credentials", "pass", "credential_file_readable"),
            Err(_) => with_hint(
                check("credentials", "fail", "credential_file_invalid"),
                "token_file",
                "Check the path, readability, and token format.",
            ),
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
        with_hint(
            check("ca_file", "fail", "ca_file_invalid"),
            "ca_file",
            "Check that the CA file contains readable PEM certificates.",
        )
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
    let local_passed = checks.iter().all(|check| check["status"] != "fail");
    let probe = match oauth {
        None => llm::Probe::skipped(
            "paddle_ping_unsupported",
            "mode",
            "Paddle only supports OCR job submission. Use an LLM configuration for PING.",
        ),
        Some(_) if settings["request_budget"].as_u64() == Some(0) => llm::Probe::skipped(
            "request_budget_zero",
            "request_budget",
            "Set a positive budget or omit the setting to permit the probe.",
        ),
        Some(_) if !local_passed => llm::Probe::skipped(
            "local_checks_failed",
            "configuration",
            "Resolve the failed local checks first.",
        ),
        Some(Err(reason)) => llm::Probe::skipped(
            reason,
            "oauth_file",
            "Set oauth_file to check the provider live.",
        ),
        Some(Ok(auth)) => auth.ping(&settings),
    };
    let passed = local_passed && !probe.failed();
    let status = if !passed {
        "unready"
    } else if !probe.attempted() {
        "local_only"
    } else {
        "ready"
    };
    let config_source = match source {
        Some(path) => json!(path),
        None => json!("built-in"),
    };
    let mut report = json!({
        "status": status,
        "config_source": config_source,
        "mode": mode,
        "network_checked": probe.attempted(),
        "checks": checks,
        "probe": probe.report(),
    });
    if mode == "llm" {
        report["model"] = settings["model"].clone();
        report["effort"] = settings["effort"].clone();
        report["provider"] = json!(
            settings["model"]
                .as_str()
                .and_then(|model| model.split_once('/'))
                .map(|(route, _)| route)
        );
    }
    Ok((if passed { 0 } else { 2 }, report))
}

pub(crate) fn validate_settings(settings: &Value) -> Result<Option<PathBuf>> {
    let mode = string(settings, "mode")?;
    let llm = mode == "llm";
    ensure!(llm || mode == "paddle", "invalid plan mode");
    let concurrency = integer(settings, "concurrency")?;
    ensure!((1..=100).contains(&concurrency), "invalid plan concurrency");
    integer(settings, "retry_rounds")?;
    if settings
        .get("mermaid_repair_rounds")
        .is_some_and(|n| !n.is_null())
    {
        ensure!(
            integer(settings, "mermaid_repair_rounds")? == 0 || llm,
            "invalid Mermaid repair mode"
        );
    }
    if !field(settings, "request_budget")?.is_null() {
        integer(settings, "request_budget")?;
    }
    let deadline = field(settings, "admission_seconds")?
        .as_f64()
        .context("invalid plan deadline")?;
    ensure!(
        deadline.is_finite() && deadline >= 0.0,
        "invalid plan deadline"
    );
    let endpoint = optional_string(settings, "endpoint")?;
    if llm {
        string(settings, "prompt")?;
        ensure!(
            string(settings, "llm_schema")? == "mdocr-image-content-1",
            "unsupported LLM plan schema"
        );
        ensure!(!string(settings, "model")?.is_empty(), "invalid LLM model");
        ensure!(
            matches!(
                string(settings, "effort")?,
                "low" | "medium" | "high" | "xhigh"
            ),
            "invalid LLM effort"
        );
        ensure!(endpoint.is_none(), "invalid endpoint for LLM plan");
    }
    optional_string(settings, "cache_dir")?
        .map(|path| absolute(path, "cache directory"))
        .transpose()
}

fn field<'a>(object: &'a Value, key: &str) -> Result<&'a Value> {
    object
        .get(key)
        .with_context(|| format!("plan is missing {key}"))
}

fn string<'a>(object: &'a Value, key: &str) -> Result<&'a str> {
    field(object, key)?
        .as_str()
        .with_context(|| format!("invalid plan {key}"))
}

fn optional_string<'a>(object: &'a Value, key: &str) -> Result<Option<&'a str>> {
    let value = field(object, key)?;
    if value.is_null() {
        Ok(None)
    } else {
        Ok(Some(
            value
                .as_str()
                .with_context(|| format!("invalid plan {key}"))?,
        ))
    }
}

fn absolute(value: &str, kind: &str) -> Result<PathBuf> {
    let path = Path::new(value);
    ensure!(path.is_absolute(), "plan {kind} must be an absolute path");
    artifacts::owned_path(path)?;
    Ok(path.to_path_buf())
}

fn integer(object: &Value, key: &str) -> Result<u64> {
    field(object, key)?
        .as_u64()
        .with_context(|| format!("invalid plan {key}"))
}

pub(crate) fn rounds(settings: &Value, key: &str) -> u64 {
    settings[key].as_u64().unwrap_or(0)
}

pub(crate) fn evidence(settings: &Value, version: &str) -> Value {
    if settings["mode"] == "llm" {
        json!({
            "mode":"llm", "version":version, "model":settings["model"],
            "effort":settings["effort"], "schema":settings["llm_schema"],
            "prompt_sha256":artifacts::sha(settings["prompt"].as_str().unwrap_or_default().as_bytes())
        })
    } else {
        json!({
            "mode":"paddle", "version":version, "model":crate::paddle::MODEL,
            "jpeg_quality":crate::paddle::JPEG_QUALITY, "endpoint":settings["endpoint"]
        })
    }
}
