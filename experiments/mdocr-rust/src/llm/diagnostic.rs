use super::{Bearer, Route, credential, credential_json, field, model_route, now_millis, read_sse};
use crate::provider;
use reqwest::{StatusCode, blocking::Response, header::HeaderValue};
use serde_json::{Value, json};
use std::{
    io::{BufReader, ErrorKind, Read},
    path::Path,
    time::Duration,
};

const DEADLINE: Duration = Duration::from_secs(20);
const RESPONSE_LIMIT: u64 = 64 * 1024;

pub(crate) struct DoctorAuth<'a> {
    route: Route,
    model: &'a str,
    effort: &'a str,
    bearer: Bearer,
}

impl<'a> DoctorAuth<'a> {
    pub(crate) fn load(settings: &'a Value) -> Result<Self, &'static str> {
        let (route, model) = field(settings, "model")
            .and_then(model_route)
            .ok_or("unsupported_model")?;
        let effort = field(settings, "effort").ok_or("unsupported_effort")?;
        let path = field(settings, "oauth_file").ok_or("credential_not_configured")?;
        let store = credential_json(Path::new(path))?;
        let saved = credential(&store, route).map_err(|_| "oauth_credential_invalid")?;
        let bearer =
            Bearer::from_credential(route, saved).map_err(|_| "oauth_credential_invalid")?;
        if bearer.expires <= now_millis().map_err(|_| "clock_unavailable")? {
            return Err("oauth_credential_expired");
        }
        if bearer.access.chars().any(char::is_whitespace)
            || HeaderValue::from_str(&bearer.access).is_err()
            || bearer
                .account_id
                .as_ref()
                .is_some_and(|id| HeaderValue::from_str(id).is_err())
        {
            return Err("oauth_credential_invalid");
        }
        Ok(Self {
            route,
            model,
            effort,
            bearer,
        })
    }

    pub(crate) fn ping(&self, settings: &Value) -> Probe {
        self.ping_at(settings, self.route.endpoint(), DEADLINE)
    }

    fn ping_at(&self, settings: &Value, endpoint: &str, timeout: Duration) -> Probe {
        let Ok(client) = provider::client_for(settings) else {
            return Probe {
                status: "fail",
                ..Probe::skipped(
                    "http_client_invalid",
                    "proxy_url / ca_file",
                    "Check the configured proxy and CA file.",
                )
            };
        };
        let mut body = json!({
            "model": self.model,
            "instructions": "Reply briefly.",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "PING"}]}],
            "reasoning": {"effort": self.effort},
            "stream": true,
            "store": false
        });
        if self.route == Route::OpenAi {
            body["max_output_tokens"] = json!(512);
        }
        let response = self
            .route
            .request(&client, endpoint, &self.bearer)
            .timeout(timeout)
            .json(&body)
            .send();
        match response {
            Ok(response) => Probe::response(response),
            Err(error) if error.is_timeout() => Probe {
                http_state: "deadline_exceeded",
                ..Probe::failure("deadline_exceeded", None)
            },
            Err(_) => Probe {
                http_state: "transport_failure",
                ..Probe::failure("transport_failure", None)
            },
        }
    }
}

pub(crate) struct Probe {
    status: &'static str,
    http_state: &'static str,
    http_status: Option<u16>,
    remote_credentials: &'static str,
    model: &'static str,
    reason: &'static str,
    config_key: &'static str,
    hint: &'static str,
}

impl Probe {
    pub(crate) fn skipped(
        reason: &'static str,
        config_key: &'static str,
        hint: &'static str,
    ) -> Self {
        Self {
            status: "skipped",
            http_state: "skipped",
            http_status: None,
            remote_credentials: "unverified",
            model: "unverified",
            reason,
            config_key,
            hint,
        }
    }

    pub(crate) fn attempted(&self) -> bool {
        self.http_state != "skipped"
    }

    pub(crate) fn failed(&self) -> bool {
        self.status == "fail"
    }

    pub(crate) fn report(&self) -> Value {
        let mut value = json!({
            "status": self.status,
            "http": {"status": self.http_state, "status_code": self.http_status},
            "remote_credentials": self.remote_credentials,
            "model": self.model,
            "reason_code": self.reason,
        });
        if self.status != "pass" {
            value["config_key"] = json!(self.config_key);
            value["hint"] = json!(self.hint);
        }
        value
    }

    fn failure(reason: &'static str, code: Option<u16>) -> Self {
        let (config_key, hint) = match reason {
            "authentication_rejected" => ("oauth_file", "Check the selected provider credential."),
            "permission_denied" => (
                "oauth_file",
                "Check credential permissions for the selected model.",
            ),
            "model_rejected" => ("model", "Check whether the provider supports this model."),
            "rate_limited" => ("model", "Check provider limits and try later."),
            "deadline_exceeded" | "transport_failure" => (
                "proxy_url / ca_file",
                "Check connectivity, proxy, and TLS trust for the provider.",
            ),
            _ => (
                "model / effort",
                "Check the provider status and selected model and effort.",
            ),
        };
        Self {
            status: "fail",
            http_state: "response_received",
            http_status: code,
            remote_credentials: "unverified",
            model: "unverified",
            reason,
            config_key,
            hint,
        }
    }

    fn response(response: Response) -> Self {
        let status = response.status();
        let code = Some(status.as_u16());
        if status.is_success() {
            return match read_reply(response) {
                Ok(()) => Self {
                    status: "pass",
                    http_state: "response_received",
                    http_status: code,
                    remote_credentials: "accepted",
                    model: "pass",
                    reason: "assistant_text_received",
                    config_key: "",
                    hint: "",
                },
                Err(reason) => Self::response_failure(reason, code),
            };
        }
        let reason = match status {
            StatusCode::UNAUTHORIZED => "authentication_rejected",
            StatusCode::TOO_MANY_REQUESTS => "rate_limited",
            StatusCode::BAD_REQUEST | StatusCode::FORBIDDEN | StatusCode::NOT_FOUND => {
                read_provider_error(response)
            }
            _ => "provider_error",
        };
        let reason = if reason == "provider_error" && status == StatusCode::FORBIDDEN {
            "permission_denied"
        } else {
            reason
        };
        Self::response_failure(reason, code)
    }

    fn response_failure(reason: &'static str, status: Option<u16>) -> Self {
        let mut probe = Self::failure(reason, status);
        match reason {
            "authentication_rejected" => probe.remote_credentials = "rejected",
            "empty_assistant_text" => {
                probe.remote_credentials = "accepted";
                probe.model = "fail";
            }
            "model_rejected" | "response_incomplete" => probe.model = "fail",
            _ => {}
        }
        probe
    }
}

fn provider_error(error: &Value) -> &'static str {
    match error["code"].as_str().or_else(|| error["type"].as_str()) {
        Some("model_not_found" | "invalid_model" | "unsupported_model") => "model_rejected",
        Some("invalid_api_key" | "invalid_token" | "token_expired" | "authentication_error") => {
            "authentication_rejected"
        }
        Some("permission_denied" | "insufficient_permissions") => "permission_denied",
        Some("rate_limit_exceeded") => "rate_limited",
        _ => "provider_error",
    }
}

fn response_io_error(error: std::io::Error) -> &'static str {
    if error.kind() == ErrorKind::TimedOut
        || error
            .get_ref()
            .and_then(|cause| cause.downcast_ref::<reqwest::Error>())
            .is_some_and(reqwest::Error::is_timeout)
    {
        "deadline_exceeded"
    } else if error.kind() == ErrorKind::InvalidData {
        "response_invalid"
    } else {
        "response_read_failed"
    }
}

fn read_provider_error(response: Response) -> &'static str {
    let mut body = Vec::new();
    if let Err(error) = response.take(RESPONSE_LIMIT + 1).read_to_end(&mut body) {
        return response_io_error(error);
    }
    if body.len() as u64 > RESPONSE_LIMIT {
        return "response_too_large";
    }
    serde_json::from_slice::<Value>(&body)
        .map(|body| provider_error(&body["error"]))
        .unwrap_or("provider_error")
}

fn read_reply(response: Response) -> Result<(), &'static str> {
    let mut reader = BufReader::new(response.take(RESPONSE_LIMIT + 1));
    let result = read_sse(&mut reader, reply_event, response_io_error);
    if reader.get_ref().limit() == 0 && reader.buffer().is_empty() {
        return Err("response_too_large");
    }
    result?.ok_or("response_incomplete")
}

fn reply_event(data: &str) -> Result<Option<()>, &'static str> {
    if data == "[DONE]" {
        return Ok(None);
    }
    let event: Value = serde_json::from_str(data).map_err(|_| "response_invalid")?;
    let response = &event["response"];
    match event["type"].as_str() {
        Some("response.completed" | "response.done") => match response["status"].as_str() {
            Some("completed") => {}
            Some("incomplete") => return Err("response_incomplete"),
            Some("failed") => return Err(provider_error(&response["error"])),
            _ => return Err("response_invalid"),
        },
        Some("response.incomplete") => return Err("response_incomplete"),
        Some("response.failed") => return Err(provider_error(&response["error"])),
        Some("error") => return Err(provider_error(&event)),
        _ => return Ok(None),
    }
    let output = response["output"].as_array().ok_or("response_invalid")?;
    let has_text = output
        .iter()
        .filter(|item| item["type"] == "message" && item["role"] == "assistant")
        .filter_map(|item| item["content"].as_array())
        .flatten()
        .any(|part| part["type"] == "output_text" && field(part, "text").is_some());
    has_text.then_some(Some(())).ok_or("empty_assistant_text")
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
    use std::{
        fs,
        io::{BufRead, Write},
        net::{TcpListener, TcpStream},
        thread,
    };

    fn request(socket: &mut TcpStream) -> (String, Value) {
        socket
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let mut reader = BufReader::new(socket);
        let mut headers = String::new();
        loop {
            let mut line = String::new();
            assert!(reader.read_line(&mut line).unwrap() > 0);
            if line == "\r\n" {
                break;
            }
            headers.push_str(&line);
        }
        let length = headers
            .lines()
            .find_map(|line| {
                let (key, value) = line.split_once(':')?;
                key.eq_ignore_ascii_case("content-length")
                    .then(|| value.trim().parse().unwrap())
            })
            .unwrap();
        let mut body = vec![0; length];
        reader.read_exact(&mut body).unwrap();
        (headers, serde_json::from_slice(&body).unwrap())
    }

    fn probe(
        route: Route,
        status: u16,
        body: &str,
        delay: Duration,
        timeout: Duration,
    ) -> (Value, String, Value) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("oauth.json");
        let account = URL_SAFE_NO_PAD
            .encode(br#"{"https://api.openai.com/auth":{"chatgpt_account_id":"fixture-account"}}"#);
        let credentials = json!({
            route.key(): {
                "type": "oauth", "access": format!("header.{account}.signature"),
                "refresh": "fixture-refresh", "expires": 9_999_999_999_999u64,
            }
        });
        let saved = credentials.to_string();
        fs::write(&path, &saved).unwrap();
        let settings = json!({
            "oauth_file": path, "model": format!("{}/fixture-model", route.key()), "effort": "high",
        });
        let auth = DoctorAuth::load(&settings).unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let route_path = reqwest::Url::parse(route.endpoint()).unwrap();
        let endpoint = format!(
            "http://{}{}",
            listener.local_addr().unwrap(),
            route_path.path()
        );
        let (result, request) = thread::scope(|scope| {
            let server = scope.spawn(|| {
                let (mut socket, _) = listener.accept().unwrap();
                let request = request(&mut socket);
                write!(
                    socket,
                    "HTTP/1.1 {status} Fixture\r\nConnection: close\r\nContent-Length: {}\r\nLocation: {endpoint}\r\n\r\n",
                    body.len()
                )
                .unwrap();
                thread::sleep(delay);
                let _ = socket.write_all(body.as_bytes());
                request
            });
            let probe = auth.ping_at(&settings, &endpoint, timeout);
            (probe, server.join().unwrap())
        });
        listener.set_nonblocking(true).unwrap();
        assert_eq!(listener.accept().unwrap_err().kind(), ErrorKind::WouldBlock);
        assert_eq!(fs::read_to_string(path).unwrap(), saved);
        assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
        assert!(result.attempted());
        (result.report(), request.0, request.1)
    }

    fn completed(text: &str) -> String {
        format!(
            "data: {}\n\n",
            json!({"type": "response.completed", "response": {
                "status": "completed", "output": [{
                    "type": "message", "role": "assistant", "content": [{
                        "type": "output_text", "text": text
                    }]
                }]
            }})
        )
    }

    #[test]
    fn each_route_sends_one_text_only_request_and_accepts_any_completed_reply() {
        for (route, text) in [(Route::OpenAi, "PONG"), (Route::Codex, "Hello.")] {
            let (report, headers, body) = probe(
                route,
                200,
                &completed(text),
                Duration::ZERO,
                Duration::from_secs(2),
            );
            assert_eq!(report["status"], "pass");
            assert_eq!(report["http"]["status_code"], 200);
            assert_eq!(report["remote_credentials"], "accepted");
            assert_eq!(report["model"], "pass");
            assert!(!report.to_string().contains(text));
            assert!(!report.to_string().contains("fixture-account"));
            assert!(headers.starts_with(&format!(
                "POST {} ",
                reqwest::Url::parse(route.endpoint()).unwrap().path()
            )));
            assert_eq!(
                headers.contains("chatgpt-account-id: fixture-account"),
                route == Route::Codex
            );
            assert_eq!(body["model"], "fixture-model");
            assert_eq!(body["reasoning"]["effort"], "high");
            assert_eq!(
                body["input"],
                json!([{"role":"user","content":[{"type":"input_text","text":"PING"}]}])
            );
            assert_eq!(body["store"], false);
            assert_eq!(
                body.get("max_output_tokens").is_some(),
                route == Route::OpenAi
            );
        }
    }

    #[test]
    fn probe_separates_http_oauth_and_model_results() {
        let cases = [
            (
                200,
                completed("  "),
                "empty_assistant_text",
                "accepted",
                "fail",
            ),
            (
                200,
                "data: [DONE]\n\n".into(),
                "response_incomplete",
                "unverified",
                "fail",
            ),
            (
                401,
                "{}".into(),
                "authentication_rejected",
                "rejected",
                "unverified",
            ),
            (
                403,
                "{}".into(),
                "permission_denied",
                "unverified",
                "unverified",
            ),
            (
                404,
                r#"{"error":{"code":"model_not_found","message":"private details"}}"#.into(),
                "model_rejected",
                "unverified",
                "fail",
            ),
            (429, "{}".into(), "rate_limited", "unverified", "unverified"),
            (
                302,
                String::new(),
                "provider_error",
                "unverified",
                "unverified",
            ),
        ];
        for (code, body, reason, remote, model) in cases {
            let (report, _, _) = probe(
                Route::OpenAi,
                code,
                &body,
                Duration::ZERO,
                Duration::from_secs(2),
            );
            assert_eq!(report["status"], "fail");
            assert_eq!(report["http"]["status"], "response_received");
            assert_eq!(report["http"]["status_code"], code);
            assert_eq!(report["reason_code"], reason);
            assert_eq!(report["remote_credentials"], remote);
            assert_eq!(report["model"], model);
            assert!(!report.to_string().contains("private details"));
        }
    }

    #[test]
    fn stalled_response_body_reaches_the_request_deadline() {
        for (code, body) in [(200, completed("PONG")), (400, "{}".into())] {
            let (report, _, _) = probe(
                Route::OpenAi,
                code,
                &body,
                Duration::from_millis(400),
                Duration::from_millis(200),
            );
            assert_eq!(report["status"], "fail");
            assert_eq!(report["http"]["status_code"], code);
            assert_eq!(report["reason_code"], "deadline_exceeded");
            assert_eq!(report["model"], "unverified");
        }
    }
}
