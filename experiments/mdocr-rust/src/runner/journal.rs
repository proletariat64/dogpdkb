use super::{Conversion, JOURNAL_SCHEMA};
use crate::artifacts::{Image, Plan, atomic, json_bytes, sha, validate_patch};
use crate::config::rounds;
use crate::provider::Failure;
use crate::publication::PublicationReceipt;
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashMap};
use std::path::Path;

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Status {
    Admitted,
    Unknown,
    Failed,
    Success,
    Reused,
}

#[derive(Deserialize, Serialize)]
pub(super) struct Attempt {
    pub(super) key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) source_sha256: Option<String>,
    pub(super) status: Status,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) usage_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) retry_round: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) repair_round: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) patches: Option<[Value; 1]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) warnings: Option<[String; 1]>,
}

impl Attempt {
    pub(super) fn new(key: String, status: Status) -> Self {
        Self {
            key,
            status,
            source_sha256: None,
            usage_tokens: None,
            reason: None,
            retry_round: None,
            repair_round: None,
            patches: None,
            warnings: None,
        }
    }

    pub(super) fn patch(&self) -> Option<&Value> {
        self.patches.as_ref().map(|patches| &patches[0])
    }
}

#[derive(Default, Deserialize, Serialize)]
pub(super) struct Budget {
    pub(super) requests: u64,
    pub(super) tokens: u64,
    pub(super) request_limit: Option<u64>,
}

#[derive(Deserialize, Serialize)]
pub(super) struct Concurrency {
    pub(super) initial: usize,
    #[serde(rename = "final")]
    pub(super) current: usize,
    pub(super) transitions: Vec<Value>,
}

#[derive(Deserialize, Serialize)]
pub(super) struct Journal {
    pub(super) schema_version: String,
    pub(super) plan_sha256: String,
    pub(super) runtime_version: String,
    pub(super) status: String,
    pub(super) causes: Vec<String>,
    pub(super) concurrency: Concurrency,
    pub(super) budget: Budget,
    pub(super) requests: Vec<Attempt>,
    #[serde(default)]
    pub(super) reused: BTreeMap<String, Attempt>,
    pub(super) documents: Vec<Value>,
    #[serde(default)]
    pub(super) publication_receipts: BTreeMap<String, PublicationReceipt>,
}

impl Journal {
    pub(super) fn settle(
        &mut self,
        index: usize,
        tier: usize,
        result: Result<Conversion, Failure>,
        settings: &Value,
        latest: &mut HashMap<String, usize>,
        stop: &mut Option<&'static str>,
    ) -> Result<Option<(std::path::PathBuf, Vec<u8>)>> {
        let attempt = &mut self.requests[index];
        let mut cache = None;
        let (status, usage) = match result {
            Ok(conversion) => {
                let warned = !conversion.mermaid_errors.is_empty();
                attempt.status = Status::Success;
                if warned && rounds(settings, "mermaid_repair_rounds") > 0 {
                    attempt.warnings = Some(["response_mermaid_syntax_invalid".into()]);
                }
                if attempt.repair_round.is_none() || !warned {
                    latest.insert(attempt.key.clone(), index);
                }
                if !warned && conversion.patch["kind"] != "hold" {
                    cache = settings["cache_dir"].as_str();
                }
                attempt.patches = Some([conversion.patch]);
                (None, conversion.usage_tokens)
            }
            Err(failure) => {
                attempt.status = if failure.unknown {
                    Status::Unknown
                } else {
                    Status::Failed
                };
                attempt.reason = Some(failure.reason.into());
                (failure.status, failure.usage_tokens)
            }
        };
        if let Some(tokens) = usage {
            self.budget.tokens = self
                .budget
                .tokens
                .checked_add(tokens)
                .context("token usage overflow")?;
            attempt.usage_tokens = Some(tokens);
        }
        let limit = &mut self.concurrency.current;
        if stop.is_none() {
            match status {
                Some(401) => *stop = Some("OCR_AUTH_UNAVAILABLE"),
                Some(403) => *stop = Some("OCR_QUOTA_EXHAUSTED"),
                Some(429) if tier == *limit && *limit == 1 => *stop = Some("OCR_RATE_LIMITED"),
                Some(429) if tier == *limit => {
                    *limit -= 1;
                    self.concurrency
                        .transitions
                        .push(json!({"from": tier, "to": *limit}));
                }
                _ => {}
            }
        }
        let cache_record = if let (Some(dir), Some(patches)) = (cache, &attempt.patches) {
            let key = &attempt.key;
            Some((
                Path::new(dir).join(format!("{key}.json")),
                json_bytes(
                    &json!({"key": key, "patches": patches, "sha256": sha(&json_bytes(patches)?)}),
                )?,
            ))
        } else {
            None
        };
        Ok(cache_record)
    }

    pub(super) fn record_admission(&mut self, attempt: Attempt, path: &Path) -> Result<usize> {
        ensure!(
            self.budget
                .request_limit
                .is_none_or(|limit| self.budget.requests < limit),
            "OCR_REQUEST_BUDGET_EXHAUSTED"
        );
        let index = self.requests.len();
        self.requests.push(attempt);
        self.budget.requests += 1;
        self.persist(path)?;
        Ok(index)
    }

    pub(super) fn persist(&self, path: &Path) -> Result<()> {
        atomic(path, &json_bytes(self)?, false)
    }

    pub(super) fn recover(
        &mut self,
        plan: &Plan,
        jobs: &HashMap<String, &Image>,
    ) -> Result<(HashMap<String, usize>, HashMap<String, usize>)> {
        let mut latest = HashMap::new();
        let mut repairs = HashMap::new();
        let settings = &plan.settings;
        let state = &mut *self;
        ensure!(
            state.schema_version == JOURNAL_SCHEMA
                && state.plan_sha256 == plan.digest
                && matches!(
                    state.status.as_str(),
                    "running" | "partial" | "completed" | "canceled"
                ),
            "execution journal schema or plan binding is invalid"
        );
        ensure!(
            state.concurrency.initial as u64 == rounds(settings, "concurrency")
                && (1..=state.concurrency.initial).contains(&state.concurrency.current)
                && state.budget.requests == state.requests.len() as u64
                && state.budget.request_limit == settings["request_budget"].as_u64()
                && settings["request_budget"]
                    .as_u64()
                    .is_none_or(|n| state.budget.requests <= n),
            "invalid saved execution counters"
        );
        self.validate_history(plan, jobs)?;
        let state = &mut *self;
        for (i, attempt) in state.requests.iter_mut().enumerate() {
            if attempt.status == Status::Admitted {
                attempt.status = Status::Unknown;
                attempt.reason = Some("OCR_OUTCOME_UNKNOWN".into());
            }
            if attempt.repair_round.is_some() {
                repairs.insert(attempt.key.clone(), i);
                if attempt.status != Status::Success || attempt.warnings.is_some() {
                    continue;
                }
            }
            latest.insert(attempt.key.clone(), i);
        }
        for (key, attempt) in &state.reused {
            ensure!(
                key == &attempt.key && jobs.contains_key(key) && attempt.status == Status::Reused,
                "invalid journal cache reuse"
            );
            validate_patch(attempt.patch().context("missing cached patch")?)
                .map_err(anyhow::Error::msg)?;
        }
        Ok((latest, repairs))
    }

    fn validate_history(&self, plan: &Plan, jobs: &HashMap<String, &Image>) -> Result<()> {
        let mut latest: HashMap<&str, &Attempt> = HashMap::new();
        let mut fixes = HashMap::new();
        let mut accounted_tokens = 0u64;
        for attempt in &self.requests {
            let key = attempt.key.as_str();
            let image = jobs.get(key).context(
                "saved OCR request identities do not match this runtime; no requests sent",
            )?;
            ensure!(
                attempt.source_sha256 == image.sha256
                    && attempt.status != Status::Reused
                    && (attempt.status == Status::Success) == attempt.patches.is_some(),
                "invalid journal attempt"
            );
            if let Some(patch) = attempt.patch() {
                validate_patch(patch).map_err(anyhow::Error::msg)?;
                if let Some(warnings) = &attempt.warnings {
                    ensure!(
                        patch["warnings"] == json!(warnings),
                        "journal warning does not match patch"
                    );
                }
            } else {
                ensure!(attempt.warnings.is_none(), "journal warning has no patch");
            }
            accounted_tokens = accounted_tokens
                .checked_add(attempt.usage_tokens.unwrap_or(0))
                .context("token usage overflow")?;
            let last = latest.get(key).copied();
            let bounded = match (attempt.retry_round, attempt.repair_round) {
                (None, None) => last.is_none_or(|last| {
                    last.status == Status::Failed && last.repair_round.is_none()
                }),
                (Some(round), None) => {
                    let previous = fixes.insert(key, round).unwrap_or(0u64);
                    round <= rounds(&plan.settings, "retry_rounds")
                        && previous.checked_add(1) == Some(round)
                        && last.is_some_and(|last| {
                            last.repair_round.is_none() && super::Execution::is_fixable(last)
                        })
                }
                (None, Some(round)) => {
                    round <= rounds(&plan.settings, "mermaid_repair_rounds")
                        && last.is_some_and(|last| {
                            last.status == Status::Success
                                && last.warnings.is_some()
                                && last.repair_round.unwrap_or(0).checked_add(1) == Some(round)
                        })
                }
                (Some(_), Some(_)) => false,
            };
            ensure!(bounded, "invalid or unsafe journal retry history");
            latest.insert(key, attempt);
        }
        ensure!(
            accounted_tokens == self.budget.tokens,
            "invalid journal token tally"
        );
        Ok(())
    }
}
