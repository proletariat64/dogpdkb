use reqwest::blocking::Client;
use std::time::Duration;

#[derive(Debug)]
pub(crate) struct Failure {
    pub(crate) reason: &'static str,
    pub(crate) status: Option<u16>,
    pub(crate) unknown: bool,
    pub(crate) usage_tokens: Option<u64>,
}

impl Failure {
    pub(crate) fn known(reason: &'static str) -> Self {
        Self {
            reason,
            status: None,
            unknown: false,
            usage_tokens: None,
        }
    }

    pub(crate) fn unknown(reason: &'static str) -> Self {
        Self {
            unknown: true,
            ..Self::known(reason)
        }
    }

    pub(crate) fn http(status: u16, unknown: bool) -> Self {
        let reason = match status {
            401 => "OCR_AUTH_UNAVAILABLE",
            403 => "OCR_QUOTA_EXHAUSTED",
            429 => "OCR_RATE_LIMITED",
            _ => "OCR_PROVIDER_FAILED",
        };
        Self {
            status: Some(status),
            unknown,
            ..Self::known(reason)
        }
    }
}

pub(crate) fn client() -> reqwest::Result<Client> {
    Client::builder()
        .connect_timeout(Duration::from_secs(20))
        .timeout(Duration::from_secs(60))
        .retry(reqwest::retry::never())
        .redirect(reqwest::redirect::Policy::none())
        .build()
}
