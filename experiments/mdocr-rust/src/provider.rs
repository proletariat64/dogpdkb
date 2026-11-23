use anyhow::{Context, Result, ensure};
use reqwest::{
    Certificate, Proxy,
    blocking::{Client, ClientBuilder},
};
use serde_json::Value;
use std::fs;
use std::path::Path;
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

fn base_builder() -> ClientBuilder {
    Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(20))
        .timeout(Duration::from_secs(60))
        .retry(reqwest::retry::never())
        .redirect(reqwest::redirect::Policy::none())
}

pub(crate) fn ca_certificates(settings: &Value) -> Result<Vec<Certificate>> {
    let Some(path) = settings["ca_file"].as_str().map(Path::new) else {
        return Ok(Vec::new());
    };
    let metadata =
        fs::metadata(path).with_context(|| format!("cannot inspect CA file {}", path.display()))?;
    ensure!(
        metadata.is_file(),
        "CA path is not a file: {}",
        path.display()
    );
    let bytes =
        fs::read(path).with_context(|| format!("cannot read CA file {}", path.display()))?;
    let certificates =
        Certificate::from_pem_bundle(&bytes).context("invalid CA certificate bundle")?;
    ensure!(
        !certificates.is_empty(),
        "CA file must contain at least one PEM certificate"
    );
    Ok(certificates)
}

pub(crate) fn client_builder(settings: &Value) -> Result<ClientBuilder> {
    let mut builder = base_builder();
    if let Some(proxy) = settings["proxy_url"].as_str() {
        builder = builder.proxy(Proxy::all(proxy).context("invalid configured proxy")?);
    }
    for certificate in ca_certificates(settings)? {
        builder = builder.add_root_certificate(certificate);
    }
    Ok(builder)
}

pub(crate) fn client_for(settings: &Value) -> Result<Client> {
    client_builder(settings)?
        .build()
        .context("cannot build HTTP client")
}

pub(crate) fn client() -> reqwest::Result<Client> {
    base_builder().build()
}
