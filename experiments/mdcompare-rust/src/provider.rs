use crate::config::ScoreConfig;
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use reqwest::{Certificate, Proxy, Url, blocking::Client, redirect};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    io::{BufRead, BufReader, Read, Write},
    os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt},
    path::{Path, PathBuf},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

const OPENAI_RESPONSES: &str = "https://api.openai.com/v1/responses";
const CODEX_RESPONSES: &str = "https://chatgpt.com/backend-api/codex/responses";
const OPENAI_TOKEN: &str = "https://auth.openai.com/api/accounts/oauth/token";
const CODEX_TOKEN: &str = "https://auth.openai.com/oauth/token";
// Published OAuth client identifier, shared with Pi's Codex login.
const CODEX_CLIENT: &str = "app_EMoamEEZ73f0CkXaXp7hrann";
const DIRECT_SCOPE: &str = "chatgpt.tokens.use.direct";
const DIRECT_RESOURCE: &str = "https://api.openai.com/v1";
const RESPONSE_LIMIT: u64 = 8 * 1024 * 1024;
pub const FORMAT: &str = include_str!("semantic_score_format.json");
pub const PROMPT: &str = include_str!("semantic_score_prompt.txt");

#[derive(Clone, Copy, PartialEq, Eq)]
enum Backend {
    OpenAi,
    Codex,
}

impl Backend {
    fn name(self) -> &'static str {
        match self {
            Self::OpenAi => "openai",
            Self::Codex => "openai-codex",
        }
    }
}

#[derive(Clone, Copy)]
struct Model<'a> {
    backend: Backend,
    id: &'a str,
    effort: &'a str,
    codex_effort_item: bool,
}

fn model<'a>(name: &'a str, effort: &'a str) -> Result<Model<'a>, &'static str> {
    let (provider, id) = name.split_once('/').ok_or("unsupported_model")?;
    let backend = match provider {
        "openai" => Backend::OpenAi,
        "openai-codex" => Backend::Codex,
        _ => return Err("unsupported_model"),
    };
    if !matches!(effort, "low" | "medium" | "high" | "xhigh") {
        return Err("unsupported_effort");
    }
    // Reasoning capabilities from Pi's OpenAI and OpenAI Codex model catalog.
    let recent = matches!(
        id,
        "gpt-6-astra"
            | "gpt-6-luna"
            | "gpt-6-sol"
            | "gpt-6.1-sol"
            | "gpt-6.1-terra"
            | "gpt-6.2-astra"
            | "gpt-6.2-sol"
    );
    let base_five_six = matches!(id, "gpt-5.6-luna" | "gpt-5.6-sol" | "gpt-5.6-terra");
    let allowed = recent
        || match backend {
            Backend::Codex => base_five_six && effort == "xhigh",
            Backend::OpenAi => {
                base_five_six
                    || match id {
                        "gpt-5-pro" => effort == "high",
                        "gpt-5.2-pro" | "gpt-5.4-pro" | "gpt-5.5-pro" => {
                            matches!(effort, "medium" | "high" | "xhigh")
                        }
                        "gpt-5" | "gpt-5-mini" | "gpt-5-nano" | "gpt-5.1" | "o1" | "o1-pro"
                        | "o3" | "o3-mini" | "o3-pro" | "o4-mini" => effort != "xhigh",
                        "chat-latest"
                        | "gpt-5.2"
                        | "gpt-5.3-codex"
                        | "gpt-5.3-codex-spark"
                        | "gpt-5.4"
                        | "gpt-5.4-mini"
                        | "gpt-5.4-nano"
                        | "gpt-5.5"
                        | "gpt-5.6-cyber"
                        | "gpt-6-cyber"
                        | "gpt-daybreak-blue-latest"
                        | "gpt-daybreak-red-latest" => true,
                        _ => false,
                    }
            }
        };
    if !allowed {
        return Err("unsupported_model");
    }
    Ok(Model {
        backend,
        id,
        effort,
        codex_effort_item: backend == Backend::Codex && recent,
    })
}

#[derive(Default)]
struct Connection {
    oauth_file: Option<PathBuf>,
    proxy: Option<String>,
    ca_file: Option<PathBuf>,
    endpoint: Option<String>,
}

impl From<&ScoreConfig> for Connection {
    fn from(config: &ScoreConfig) -> Self {
        Self {
            oauth_file: config.oauth_file.clone(),
            proxy: config.proxy.clone(),
            ca_file: config.ca_file.clone(),
            endpoint: config.endpoint.clone(),
        }
    }
}

impl Connection {
    fn auth_path(&self) -> Result<PathBuf, &'static str> {
        self.oauth_file
            .as_deref()
            .ok_or("oauth_required")?
            .canonicalize()
            .map_err(|_| "oauth_required")
    }

    fn endpoint(&self) -> Result<Option<Url>, &'static str> {
        let Some(url) = &self.endpoint else {
            return Ok(None);
        };
        let url = Url::parse(url).map_err(|_| "unsupported_transport")?;
        let loopback = url.host_str().is_some_and(|host| {
            host.trim_matches(['[', ']'])
                .parse::<std::net::IpAddr>()
                .is_ok_and(|ip| ip.is_loopback())
        });
        if url.scheme() != "http"
            || !loopback
            || !url.username().is_empty()
            || url.password().is_some()
            || self.proxy.is_some()
        {
            return Err("unsupported_transport");
        }
        Ok(Some(url))
    }
}

// Shared local OAuth document and credential interpretation. Scoring maps
// local failures to its existing coarse authentication error; diagnostics retain
// the precise reason without returning credential contents.
fn auth_json(path: &Path) -> Result<Value, &'static str> {
    let (bytes, _) = crate::input::read(path, 1024 * 1024).map_err(|_| "oauth_file_unreadable")?;
    serde_json::from_slice(bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&bytes))
        .map_err(|_| "oauth_file_invalid")
}

fn credential(auth: &Value, backend: Backend) -> Result<&Value, &'static str> {
    if !auth.is_object() {
        return Err("oauth_file_invalid");
    }
    let cred = auth.get(backend.name()).ok_or("oauth_provider_missing")?;
    if cred["type"] != "oauth"
        || nonempty(&cred["access"]).is_none()
        || nonempty(&cred["refresh"]).is_none()
        || cred["expires"].as_f64().is_none()
    {
        return Err("oauth_credential_invalid");
    }
    Ok(cred)
}

fn score_auth_json(path: &Path) -> Result<Value, &'static str> {
    auth_json(path).map_err(|_| "oauth_required")
}

fn score_credential(auth: &Value, backend: Backend) -> Result<&Value, &'static str> {
    credential(auth, backend).map_err(|_| "oauth_required")
}

fn nonempty(value: &Value) -> Option<&str> {
    value.as_str().filter(|text| !text.trim().is_empty())
}

fn now_millis() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
        * 1000.0
}

fn expired(cred: &Value) -> bool {
    cred["expires"]
        .as_f64()
        .is_some_and(|stamp| stamp <= now_millis())
}

fn connection(settings: &Connection) -> Result<(Client, Option<Url>), &'static str> {
    let endpoint = settings.endpoint()?;
    let mut builder = Client::builder()
        .redirect(redirect::Policy::none())
        .retry(reqwest::retry::never())
        .timeout(Duration::from_secs(170))
        .no_proxy();
    if let Some(proxy) = &settings.proxy {
        builder = builder.proxy(Proxy::https(proxy).map_err(|_| "proxy_unavailable")?);
    }
    if let Some(path) = &settings.ca_file {
        let (bytes, _) = crate::input::read(path, 1024 * 1024).map_err(|_| "ca_unavailable")?;
        let certificates = Certificate::from_pem_bundle(&bytes).map_err(|_| "ca_unavailable")?;
        if certificates.is_empty() {
            return Err("ca_unavailable");
        }
        for certificate in certificates {
            builder = builder.add_root_certificate(certificate);
        }
    }
    let client = builder.build().map_err(|_| "proxy_unavailable")?;
    Ok((client, endpoint))
}

pub(crate) enum OAuthReadiness {
    Passed,
    Failed(&'static str),
    Skipped(&'static str),
}

/// Local prerequisites for doctor, kept independent so one failed check does
/// not suppress diagnostics for the others.
pub(crate) struct Readiness {
    pub(crate) backend: Result<&'static str, &'static str>,
    pub(crate) transport: Result<(), &'static str>,
    pub(crate) oauth: OAuthReadiness,
    pub(crate) scoring_enabled: bool,
    pub(crate) probe_skip: Option<&'static str>,
}

/// Interpret a frozen scoring configuration once. Readiness is checked only
/// at the entry point that needs it. Each actual request still obtains fresh
/// transport and credentials.
pub(crate) struct Provider<'a> {
    model: Result<Model<'a>, &'static str>,
    connection: Connection,
    request_budget: Option<usize>,
}

impl<'a> Provider<'a> {
    pub(crate) fn new(config: &'a ScoreConfig) -> Self {
        Self {
            model: model(&config.model, &config.effort),
            connection: config.into(),
            request_budget: config.request_budget,
        }
    }

    pub(crate) fn diagnostics(&self) -> Readiness {
        let backend = self.model.map(|model| model.backend.name());
        let transport = connection(&self.connection).map(drop);
        let scoring_enabled = self.request_budget.is_some_and(|budget| budget > 0);
        let oauth = if self.connection.endpoint.is_some() {
            OAuthReadiness::Skipped("local_test_endpoint")
        } else if self.connection.oauth_file.is_none() && !scoring_enabled {
            OAuthReadiness::Skipped("oauth_not_configured")
        } else if let Ok(model) = self.model {
            match saved_credential(&self.connection, model.backend) {
                Ok(saved) if expired(&saved) => OAuthReadiness::Failed("oauth_expired"),
                Ok(saved)
                    if matches!(model.backend, Backend::Codex)
                        && nonempty(&saved["access"]).and_then(jwt_account).is_none() =>
                {
                    OAuthReadiness::Failed("oauth_credential_invalid")
                }
                Ok(_) => OAuthReadiness::Passed,
                Err(code) => OAuthReadiness::Failed(code),
            }
        } else {
            OAuthReadiness::Skipped("unsupported_model")
        };
        let probe_skip = if self.request_budget == Some(0) {
            Some("request_budget_zero")
        } else if backend.is_err()
            || transport.is_err()
            || matches!(oauth, OAuthReadiness::Failed(_))
        {
            Some("prerequisites_failed")
        } else if self.connection.endpoint.is_none() && self.connection.oauth_file.is_none() {
            Some("oauth_not_configured")
        } else {
            None
        };
        Readiness {
            backend,
            transport,
            oauth,
            scoring_enabled,
            probe_skip,
        }
    }

    pub(crate) fn score_preflight(&self) -> Result<bool, &'static str> {
        let model = self.model?;
        let (_client, endpoint) = connection(&self.connection)?;
        if endpoint.is_some() {
            return Ok(false);
        }
        let auth = score_auth_json(&self.connection.auth_path()?)?;
        Ok(expired(score_credential(&auth, model.backend)?))
    }

    pub(crate) fn assess(&self, packet: &str) -> Outcome {
        self.model
            .and_then(|model| request(model, Purpose::Score(packet), &self.connection))
            .unwrap_or_else(|reason| Outcome::error(reason, None))
    }

    pub(crate) fn probe(&self) -> Outcome {
        self.model
            .and_then(|model| {
                request(
                    model,
                    Purpose::Probe(Duration::from_secs(20)),
                    &self.connection,
                )
            })
            .unwrap_or_else(|reason| Outcome::error(reason, None))
    }
}

fn saved_credential(settings: &Connection, backend: Backend) -> Result<Value, &'static str> {
    let path = settings.oauth_file.as_deref().ok_or("oauth_required")?;
    let auth = auth_json(path)?;
    credential(&auth, backend).cloned()
}

// Pi's proper-lockfile expires an unchanged lock directory after ten
// seconds. Allow enough time for a stale predecessor and a healthy refresh.
const AUTH_LOCK_STALE: Duration = Duration::from_secs(10);
const AUTH_LOCK_WAIT: Duration = Duration::from_secs(17);
const AUTH_REFRESH_TIMEOUT: Duration = Duration::from_secs(6);

struct AuthLock {
    path: PathBuf,
    identity: (u64, u64),
}

impl AuthLock {
    fn acquire(auth_path: &Path) -> Result<Self, &'static str> {
        let mut name = auth_path.file_name().ok_or("auth_error")?.to_owned();
        name.push(".lock");
        let path = auth_path.with_file_name(name);
        let deadline = Instant::now() + AUTH_LOCK_WAIT;
        loop {
            match fs::DirBuilder::new().mode(0o700).create(&path) {
                Ok(()) => {
                    let m = fs::symlink_metadata(&path).map_err(|_| "auth_error")?;
                    return Ok(Self {
                        path,
                        identity: (m.dev(), m.ino()),
                    });
                }
                Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => {
                    if Instant::now() >= deadline {
                        return Err("auth_error");
                    }
                    if Self::remove_stale(&path)? {
                        continue;
                    }
                    thread::sleep(Duration::from_millis(100));
                }
                Err(_) => return Err("auth_error"),
            }
        }
    }

    fn remove_stale(path: &Path) -> Result<bool, &'static str> {
        use std::io::ErrorKind::NotFound;

        let observe = || match fs::symlink_metadata(path) {
            Ok(meta) if meta.is_dir() => Ok(Some((
                meta.dev(),
                meta.ino(),
                meta.modified().map_err(|_| "auth_error")?,
            ))),
            Err(err) if err.kind() == NotFound => Ok(None),
            _ => Err("auth_error"),
        };
        let Some(before) = observe()? else {
            return Ok(true);
        };
        if before.2.elapsed().is_ok_and(|age| age > AUTH_LOCK_STALE) && observe()? == Some(before) {
            return match fs::remove_dir(path) {
                Ok(()) => Ok(true),
                Err(err) if err.kind() == NotFound => Ok(true),
                Err(_) => Err("auth_error"),
            };
        }
        Ok(false)
    }

    fn owns_lock(&self) -> bool {
        fs::symlink_metadata(&self.path)
            .is_ok_and(|m| m.is_dir() && (m.dev(), m.ino()) == self.identity)
    }
}

impl Drop for AuthLock {
    fn drop(&mut self) {
        if self.owns_lock() {
            let _ = fs::remove_dir(&self.path);
        }
    }
}

fn jwt_account(access: &str) -> Option<String> {
    let mut pieces = access.split('.');
    let _head = pieces.next()?;
    let payload = pieces.next()?;
    let _signature = pieces.next()?;
    if pieces.next().is_some() {
        return None;
    }
    let bytes = URL_SAFE_NO_PAD.decode(payload).ok()?;
    let decoded: Value = serde_json::from_slice(&bytes).ok()?;
    nonempty(&decoded["https://api.openai.com/auth"]["chatgpt_account_id"]).map(str::to_owned)
}

fn refresh(client: &Client, backend: Backend, cred: &Value) -> Result<Value, &'static str> {
    let refresh = nonempty(&cred["refresh"]).ok_or("oauth_required")?;
    let mut form = vec![("grant_type", "refresh_token"), ("refresh_token", refresh)];
    let endpoint = match backend {
        Backend::OpenAi => {
            form.push((
                "client_id",
                nonempty(&cred["clientId"]).ok_or("oauth_required")?,
            ));
            form.push(("resource", DIRECT_RESOURCE));
            OPENAI_TOKEN
        }
        Backend::Codex => {
            form.push(("client_id", CODEX_CLIENT));
            CODEX_TOKEN
        }
    };
    // Stay within Pi proper-lockfile's minimum stale interval.
    let response = client
        .post(endpoint)
        .form(&form)
        .timeout(AUTH_REFRESH_TIMEOUT)
        .send()
        .map_err(|_| "auth_error")?
        .error_for_status()
        .map_err(|_| "auth_error")?;
    let mut bytes = Vec::new();
    response
        .take(64 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "auth_error")?;
    if bytes.len() > 64 * 1024 {
        return Err("auth_error");
    }
    let token: Value = serde_json::from_slice(&bytes).map_err(|_| "auth_error")?;
    let access = nonempty(&token["access_token"]).ok_or("auth_error")?;
    let refresh = nonempty(&token["refresh_token"]).ok_or("auth_error")?;
    let seconds = token["expires_in"]
        .as_f64()
        .filter(|seconds| *seconds > 0.0)
        .ok_or("auth_error")?;
    let mut next = cred.clone();
    next["access"] = json!(access);
    next["refresh"] = json!(refresh);
    let margin = if backend == Backend::OpenAi {
        180_000.0
    } else {
        0.0
    };
    next["expires"] = json!((now_millis() + seconds * 1000.0 - margin).round());
    if backend == Backend::OpenAi {
        let scopes: Vec<_> = nonempty(&token["scope"])
            .ok_or("auth_error")?
            .split_whitespace()
            .collect();
        if !scopes.contains(&DIRECT_SCOPE) {
            return Err("auth_error");
        }
        next["scopes"] = json!(scopes);
    } else {
        next["accountId"] = json!(jwt_account(access).ok_or("auth_error")?);
    }
    Ok(next)
}

fn access(client: &Client, backend: Backend, path: &Path) -> Result<String, &'static str> {
    let auth = score_auth_json(path)?;
    let cred = score_credential(&auth, backend)?;
    if !expired(cred) {
        return nonempty(&cred["access"])
            .map(str::to_owned)
            .ok_or("oauth_required");
    }

    let lock = AuthLock::acquire(path)?;
    let mut auth = score_auth_json(path)?;
    let cred = score_credential(&auth, backend)?;
    if expired(cred) {
        let metadata = fs::metadata(path).map_err(|_| "auth_error")?;
        let next = refresh(client, backend, cred)?;
        auth[backend.name()] = next;
        let parent = path.parent().ok_or("auth_error")?;
        let mut temp = tempfile::NamedTempFile::new_in(parent).map_err(|_| "auth_error")?;
        serde_json::to_writer_pretty(temp.as_file_mut(), &auth).map_err(|_| "auth_error")?;
        temp.as_file_mut()
            .write_all(b"\n")
            .map_err(|_| "auth_error")?;
        temp.as_file()
            .set_permissions(fs::Permissions::from_mode(metadata.mode() & 0o777))
            .map_err(|_| "auth_error")?;
        temp.as_file().sync_all().map_err(|_| "auth_error")?;
        if !lock.owns_lock() {
            return Err("auth_error");
        }
        temp.persist(path).map_err(|_| "auth_error")?;
        fs::File::open(parent)
            .and_then(|dir| dir.sync_all())
            .map_err(|_| "auth_error")?;
    }
    nonempty(&score_credential(&auth, backend)?["access"])
        .map(str::to_owned)
        .ok_or("oauth_required")
}

pub struct Outcome {
    pub reply: Option<String>,
    pub usage: Option<u64>,
    pub http_status: Option<u16>,
    pub reason: Option<&'static str>,
}

impl Outcome {
    fn error(reason: &'static str, http_status: Option<u16>) -> Self {
        Self {
            reply: None,
            usage: None,
            http_status,
            reason: Some(reason),
        }
    }
}

fn append_item(reply: &mut String, item: &Value) -> Result<(), &'static str> {
    match item["type"].as_str() {
        Some("message") => {
            for part in item["content"].as_array().ok_or("invalid_response")? {
                match part["type"].as_str() {
                    Some("output_text") => {
                        reply.push_str(part["text"].as_str().ok_or("invalid_response")?)
                    }
                    Some("refusal") => return Err("invalid_response"),
                    _ => {}
                }
            }
        }
        Some("function_call" | "custom_tool_call") => return Err("invalid_response"),
        _ => {}
    }
    Ok(())
}

fn completed(
    event: &Value,
    items: BTreeMap<u64, Value>,
) -> Result<(String, Option<u64>), &'static str> {
    let response = &event["response"];
    if event["type"] == "response.incomplete" || response["status"] == "incomplete" {
        return if response["incomplete_details"]["reason"] == "max_output_tokens" {
            Err("invalid_response")
        } else {
            Err("unknown_outcome")
        };
    }
    if !response.is_object() || !(response["status"].is_null() || response["status"] == "completed")
    {
        return Err("unknown_outcome");
    }
    let mut reply = String::new();
    if let Some(output) = response["output"].as_array() {
        for item in output {
            append_item(&mut reply, item)?;
        }
    }
    if reply.is_empty() {
        for item in items.values() {
            append_item(&mut reply, item)?;
        }
    }
    if reply.is_empty() {
        return Err("invalid_response");
    }
    let usage = &response["usage"];
    let tokens = usage["total_tokens"].as_u64().or_else(|| {
        usage["input_tokens"]
            .as_u64()
            .unwrap_or(0)
            .checked_add(usage["output_tokens"].as_u64().unwrap_or(0))
    });
    Ok((reply, tokens))
}

fn parse_stream(
    mut reader: impl BufRead,
    diagnose: bool,
) -> Result<(String, Option<u64>), &'static str> {
    let mut line = String::new();
    let mut data = String::new();
    let mut items = BTreeMap::new();
    loop {
        line.clear();
        let n = reader.read_line(&mut line).map_err(|_| "unknown_outcome")?;
        if n == 0 {
            return Err("unknown_outcome");
        }
        let line = line.trim_end_matches(['\r', '\n']);
        if let Some(chunk) = line.strip_prefix("data:") {
            if !data.is_empty() {
                data.push('\n');
            }
            data.push_str(chunk.strip_prefix(' ').unwrap_or(chunk));
        } else if line.is_empty() && !data.is_empty() {
            let value: Value = serde_json::from_str(&data).map_err(|_| "unknown_outcome")?;
            match value["type"].as_str() {
                Some("response.completed" | "response.done" | "response.incomplete") => {
                    return completed(&value, items);
                }
                Some("response.failed" | "error") => {
                    if diagnose
                        && [
                            &value["code"],
                            &value["error"]["code"],
                            &value["response"]["error"]["code"],
                        ]
                        .into_iter()
                        .any(is_model_rejection)
                    {
                        return Err("model_rejected");
                    }
                    return Err("unknown_outcome");
                }
                Some("response.output_item.done") => {
                    let index = value["output_index"].as_u64().ok_or("invalid_response")?;
                    if items.insert(index, value["item"].clone()).is_some() {
                        return Err("invalid_response");
                    }
                }
                _ => {}
            }
            data.clear();
        }
    }
}

#[derive(Clone, Copy)]
enum Purpose<'a> {
    Score(&'a str),
    Probe(Duration),
}

fn request(
    model: Model<'_>,
    purpose: Purpose<'_>,
    settings: &Connection,
) -> Result<Outcome, &'static str> {
    let (client, endpoint) = connection(settings)?;
    let token = if endpoint.is_some() {
        None
    } else {
        Some(match purpose {
            Purpose::Score(_) => access(&client, model.backend, &settings.auth_path()?)?,
            Purpose::Probe(_) => {
                let saved = saved_credential(settings, model.backend)?;
                if expired(&saved) {
                    return Err("oauth_expired");
                }
                nonempty(&saved["access"])
                    .ok_or("oauth_credential_invalid")?
                    .to_owned()
            }
        })
    };
    let (prompt, packet, max_output_tokens) = match purpose {
        Purpose::Score(packet) => (PROMPT, packet, 4096),
        Purpose::Probe(_) => ("Reply briefly in plain text.", "PING", 256),
    };
    let mut input = vec![json!({"role": "developer", "content": prompt})];
    input.push(json!({
        "role": "user",
        "content": [{"type": "input_text", "text": packet}],
    }));
    if model.codex_effort_item {
        input.push(json!({"type": "configuration_update", "reasoning": {"effort": model.effort}}));
    }
    let mut body = json!({
        "model": model.id,
        "store": false,
        "stream": true,
        "input": input,
        "max_output_tokens": max_output_tokens,
        "reasoning": {
            "effort": if model.codex_effort_item { "medium" } else { model.effort },
            "summary": "auto",
        },
        "include": ["reasoning.encrypted_content"],
    });
    if matches!(purpose, Purpose::Score(_)) {
        body["text"] = json!({"format": serde_json::from_str::<Value>(FORMAT).expect("valid embedded score format")});
    }
    let mut request = if let Some(endpoint) = endpoint {
        client.post(endpoint)
    } else {
        match model.backend {
            Backend::OpenAi => client.post(OPENAI_RESPONSES),
            Backend::Codex => {
                body["text"]["verbosity"] = json!("low");
                body["tool_choice"] = json!("auto");
                body["parallel_tool_calls"] = json!(true);
                client
                    .post(CODEX_RESPONSES)
                    .header(
                        "chatgpt-account-id",
                        jwt_account(token.as_deref().ok_or("auth_error")?).ok_or("auth_error")?,
                    )
                    .header("originator", "pi")
                    .header("openai-beta", "responses=experimental")
            }
        }
    };
    if let Some(token) = token {
        request = request.bearer_auth(token);
    }
    request = request.header("accept", "text/event-stream").json(&body);
    let started = Instant::now();
    if let Purpose::Probe(timeout) = purpose {
        request = request.timeout(timeout);
    }
    let response = request.send().map_err(|error| match purpose {
        Purpose::Probe(_) if error.is_timeout() => "probe_timeout",
        Purpose::Probe(_) => "provider_unreachable",
        Purpose::Score(_) => "unknown_outcome",
    })?;
    let status = response.status().as_u16();
    if let Purpose::Probe(timeout) = purpose
        && !(200..=299).contains(&status)
    {
        let reason = probe_http_error(status, response, started, timeout);
        return Ok(Outcome::error(reason, Some(status)));
    }
    let reason = match status {
        200..=299 => None,
        401 | 403 => Some("auth_error"),
        429 => Some("rate_limited"),
        500..=599 => Some("unknown_outcome"),
        _ => Some("provider_error"),
    };
    if let Some(reason) = reason {
        return Ok(Outcome::error(reason, Some(status)));
    }
    Ok(
        match parse_stream(
            BufReader::new(response.take(RESPONSE_LIMIT)),
            matches!(purpose, Purpose::Probe(_)),
        ) {
            Ok((reply, usage)) => Outcome {
                reply: Some(reply),
                usage,
                http_status: Some(status),
                reason: None,
            },
            Err(reason) => {
                let reason = match purpose {
                    Purpose::Probe(timeout) if started.elapsed() >= timeout => "probe_timeout",
                    Purpose::Probe(_) if reason != "model_rejected" => "incomplete_model_response",
                    Purpose::Probe(_) => reason,
                    Purpose::Score(_) => reason,
                };
                Outcome::error(reason, Some(status))
            }
        },
    )
}

fn probe_http_error(
    status: u16,
    response: reqwest::blocking::Response,
    started: Instant,
    timeout: Duration,
) -> &'static str {
    match status {
        401 => "oauth_rejected",
        403 => "provider_access_denied",
        429 => "rate_limited",
        500..=599 => "provider_unavailable",
        _ => {
            let mut body = Vec::new();
            if let Err(error) = response.take(16 * 1024).read_to_end(&mut body) {
                if error.kind() == std::io::ErrorKind::TimedOut || started.elapsed() >= timeout {
                    return "probe_timeout";
                }
                return "provider_request_rejected";
            }
            let error = serde_json::from_slice::<Value>(&body);
            if error.is_ok_and(|value| is_model_rejection(&value["error"]["code"])) {
                "model_rejected"
            } else {
                "provider_request_rejected"
            }
        }
    }
}

fn is_model_rejection(code: &Value) -> bool {
    matches!(
        code.as_str(),
        Some("model_not_found" | "model_not_available" | "unsupported_model")
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn oauth_refresh_reclaims_a_stale_lock_and_releases_its_own() {
        use std::fs::{File, FileTimes};

        let directory = tempfile::tempdir().unwrap();
        let auth = directory.path().join("auth.json");
        let lock_path = directory.path().join("auth.json.lock");
        fs::create_dir(&lock_path).unwrap();
        File::open(&lock_path)
            .unwrap()
            .set_times(
                FileTimes::new()
                    .set_modified(SystemTime::now() - AUTH_LOCK_STALE - Duration::from_secs(1)),
            )
            .unwrap();

        let lock = AuthLock::acquire(&auth).unwrap();
        assert!(lock.owns_lock());
        assert!(!AuthLock::remove_stale(&lock_path).unwrap());
        drop(lock);
        assert!(!lock_path.exists());
    }

    #[test]
    fn preflight_shares_credentials_but_keeps_probe_and_refresh_policies_distinct() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("auth.json");
        let expired =
            br#"{"openai":{"type":"oauth","access":"dummy","refresh":"dummy","expires":1}}"#;
        fs::write(&path, expired).unwrap();
        let config = ScoreConfig {
            request_budget: Some(1),
            oauth_file: Some(path.clone()),
            ..ScoreConfig::default()
        };
        let provider = Provider::new(&config);
        let readiness = provider.diagnostics();
        assert!(matches!(
            readiness.oauth,
            OAuthReadiness::Failed("oauth_expired")
        ));
        assert_eq!(readiness.probe_skip, Some("prerequisites_failed"));
        assert_eq!(provider.score_preflight(), Ok(true));
        assert_eq!(fs::read(&path).unwrap(), expired);

        fs::write(&path, b"{").unwrap();
        let readiness = provider.diagnostics();
        assert!(matches!(
            readiness.oauth,
            OAuthReadiness::Failed("oauth_file_invalid")
        ));
        assert_eq!(provider.score_preflight(), Err("oauth_required"));
        assert_eq!(fs::read(&path).unwrap(), b"{");
    }

    #[test]
    fn provider_rejects_remote_override_without_connecting() {
        let cfg = ScoreConfig {
            endpoint: Some("https://example.invalid/v1/responses".to_owned()),
            ..ScoreConfig::default()
        };
        let outcome = Provider::new(&cfg).assess("packet");
        assert_eq!(outcome.reason, Some("unsupported_transport"));
    }

    #[test]
    fn native_responses_requires_a_completed_stream() {
        let message = json!({
            "type": "message",
            "content": [{"type": "output_text", "text": "verified result"}],
        });
        let item = json!({"type": "response.output_item.done", "output_index": 0, "item": message});
        let done = json!({
            "type": "response.completed",
            "response": {"status": "completed", "output": [], "usage": {"input_tokens": 5, "output_tokens": 7}},
        });
        let first = format!("data: {item}\r\n\r\n");
        let full = format!("{first}data: {done}\r\n\r\n");
        assert_eq!(
            parse_stream(Cursor::new(full), false),
            Ok(("verified result".to_owned(), Some(12)))
        );
        assert_eq!(
            parse_stream(Cursor::new(first), false),
            Err("unknown_outcome")
        );
    }

    #[test]
    fn native_responses_rejects_incomplete_structured_output() {
        let event = json!({
            "type": "response.incomplete",
            "response": {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
        });
        let stream = format!("data: {event}\n\n");
        assert_eq!(
            parse_stream(Cursor::new(stream), false),
            Err("invalid_response")
        );
    }

    #[test]
    fn probe_times_out_before_receiving_headers() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let cfg = Connection {
            endpoint: Some(format!(
                "http://{}/responses",
                listener.local_addr().unwrap()
            )),
            ..Connection::default()
        };
        let selected = model("openai/gpt-6.1-terra", "medium").unwrap();
        // The listening socket stays open without sending a reply.
        let error = request(selected, Purpose::Probe(Duration::from_millis(80)), &cfg).err();
        assert_eq!(error, Some("probe_timeout"));
    }

    #[test]
    fn probe_preserves_http_status_when_the_error_body_stalls() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let cfg = Connection {
            endpoint: Some(format!(
                "http://{}/responses",
                listener.local_addr().unwrap()
            )),
            ..Connection::default()
        };
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = BufReader::new(&mut stream);
            let mut length = 0;
            loop {
                let mut line = String::new();
                assert_ne!(request.read_line(&mut line).unwrap(), 0);
                if let Some(value) = line.to_ascii_lowercase().strip_prefix("content-length:") {
                    length = value.trim().parse().unwrap();
                }
                if line == "\r\n" {
                    break;
                }
            }
            request.read_exact(&mut vec![0; length]).unwrap();
            stream
                .write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 100\r\n\r\n{")
                .unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let _ = stream.read(&mut [0]);
        });
        let selected = model("openai/gpt-6.1-terra", "medium").unwrap();
        let outcome = request(selected, Purpose::Probe(Duration::from_millis(250)), &cfg)
            .ok()
            .unwrap();
        assert_eq!(outcome.reason, Some("probe_timeout"));
        assert_eq!(outcome.http_status, Some(404));
        server.join().unwrap();
    }
}
