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
    time::{Duration, SystemTime, UNIX_EPOCH},
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
pub const FORMAT: &str = include_str!("../../../src/mdcompare/semantic_score_format.json");
pub const PROMPT: &str = include_str!("../../../src/mdcompare/semantic_score_prompt.txt");

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
pub struct Connection {
    pub oauth_file: Option<PathBuf>,
    pub proxy: Option<String>,
    pub ca_file: Option<PathBuf>,
    pub endpoint: Option<String>,
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

fn auth_json(path: &Path) -> Result<Value, &'static str> {
    let (bytes, _) = crate::input::read(path, 1024 * 1024).map_err(|_| "oauth_required")?;
    serde_json::from_slice(bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&bytes))
        .map_err(|_| "oauth_required")
}

fn credential(auth: &Value, backend: Backend) -> Result<&Value, &'static str> {
    let cred = &auth[backend.name()];
    if cred["type"] != "oauth"
        || nonempty(&cred["access"]).is_none()
        || nonempty(&cred["refresh"]).is_none()
        || cred["expires"].as_f64().is_none()
    {
        return Err("oauth_required");
    }
    Ok(cred)
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

fn connection(settings: &Connection) -> Result<Client, &'static str> {
    settings.endpoint()?;
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
    builder.build().map_err(|_| "proxy_unavailable")
}

pub fn check_transport_settings(settings: &Connection) -> Result<(), &'static str> {
    connection(settings).map(drop)
}

pub fn check_oauth_settings(
    name: &str,
    effort: &str,
    settings: &Connection,
) -> Result<bool, &'static str> {
    let model = model(name, effort)?;
    let _client = connection(settings)?;
    if settings.endpoint()?.is_some() {
        return Ok(false);
    }
    oauth_expired(settings, model.backend)
}

pub(crate) fn check_model(name: &str, effort: &str) -> Result<&'static str, &'static str> {
    model(name, effort).map(|model| model.backend.name())
}

pub(crate) fn check_saved_oauth(
    name: &str,
    effort: &str,
    settings: &Connection,
) -> Result<bool, &'static str> {
    oauth_expired(settings, model(name, effort)?.backend)
}

fn oauth_expired(settings: &Connection, backend: Backend) -> Result<bool, &'static str> {
    let auth = auth_json(&settings.auth_path()?)?;
    Ok(expired(credential(&auth, backend)?))
}

struct AuthLock {
    path: PathBuf,
    identity: (u64, u64),
}

impl AuthLock {
    fn acquire(auth_path: &Path) -> Result<Self, &'static str> {
        let mut name = auth_path.file_name().ok_or("auth_error")?.to_owned();
        name.push(".lock");
        let path = auth_path.with_file_name(name);
        for _ in 0..50 {
            match fs::DirBuilder::new().mode(0o700).create(&path) {
                Ok(()) => {
                    let m = fs::symlink_metadata(&path).map_err(|_| "auth_error")?;
                    return Ok(Self {
                        path,
                        identity: (m.dev(), m.ino()),
                    });
                }
                Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => {
                    thread::sleep(Duration::from_millis(100));
                }
                Err(_) => return Err("auth_error"),
            }
        }
        Err("auth_error")
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
        .timeout(Duration::from_secs(6))
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
    let auth = auth_json(path)?;
    let cred = credential(&auth, backend)?;
    if !expired(cred) {
        return nonempty(&cred["access"])
            .map(str::to_owned)
            .ok_or("oauth_required");
    }

    let lock = AuthLock::acquire(path)?;
    let mut auth = auth_json(path)?;
    let cred = credential(&auth, backend)?;
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
    nonempty(&credential(&auth, backend)?["access"])
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

fn parse_stream(mut reader: impl BufRead) -> Result<(String, Option<u64>), &'static str> {
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
                Some("response.failed" | "error") => return Err("unknown_outcome"),
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

fn request(model: Model<'_>, packet: &str, settings: &Connection) -> Result<Outcome, &'static str> {
    let client = connection(settings)?;
    let endpoint = settings.endpoint()?;
    let token = if endpoint.is_some() {
        None
    } else {
        Some(access(&client, model.backend, &settings.auth_path()?)?)
    };
    let format: Value = serde_json::from_str(FORMAT).expect("valid embedded score format");
    let mut input = vec![json!({"role": "developer", "content": PROMPT})];
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
        "max_output_tokens": 4096,
        "reasoning": {
            "effort": if model.codex_effort_item { "medium" } else { model.effort },
            "summary": "auto",
        },
        "include": ["reasoning.encrypted_content"],
        "text": {"format": format},
    });
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
    let response = request.send().map_err(|_| "unknown_outcome")?;
    let status = response.status().as_u16();
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
        match parse_stream(BufReader::new(response.take(RESPONSE_LIMIT))) {
            Ok((reply, usage)) => Outcome {
                reply: Some(reply),
                usage,
                http_status: None,
                reason: None,
            },
            Err(reason) => Outcome::error(reason, Some(status)),
        },
    )
}

pub fn assess_with_connection(
    name: &str,
    effort: &str,
    packet: &str,
    settings: &Connection,
) -> Outcome {
    model(name, effort)
        .and_then(|model| request(model, packet, settings))
        .unwrap_or_else(|reason| Outcome::error(reason, None))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn provider_rejects_remote_override_without_connecting() {
        let cfg = Connection {
            endpoint: Some("https://example.invalid/v1/responses".to_owned()),
            ..Connection::default()
        };
        let outcome = assess_with_connection("openai/gpt-6.1-terra", "medium", "packet", &cfg);
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
            parse_stream(Cursor::new(full)),
            Ok(("verified result".to_owned(), Some(12)))
        );
        assert_eq!(parse_stream(Cursor::new(first)), Err("unknown_outcome"));
    }

    #[test]
    fn native_responses_rejects_incomplete_structured_output() {
        let event = json!({
            "type": "response.incomplete",
            "response": {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
        });
        let stream = format!("data: {event}\n\n");
        assert_eq!(parse_stream(Cursor::new(stream)), Err("invalid_response"));
    }
}
