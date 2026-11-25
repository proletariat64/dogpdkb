use super::{Conversion, JOURNAL_SCHEMA, Pass};
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
    #[serde(skip)]
    latest: HashMap<String, usize>,
    #[serde(skip)]
    repairs: HashMap<String, usize>,
}

pub(super) struct Settlement {
    pub(super) cache_record: Option<(std::path::PathBuf, Vec<u8>)>,
    pub(super) stop_reason: Option<&'static str>,
}

impl Journal {
    pub(super) fn new(plan: &Plan) -> Result<Self> {
        let limit = plan.settings["concurrency"]
            .as_u64()
            .context("missing concurrency")? as usize;
        Ok(Self {
            schema_version: JOURNAL_SCHEMA.into(),
            plan_sha256: plan.digest.clone(),
            runtime_version: env!("CARGO_PKG_VERSION").into(),
            status: "running".into(),
            causes: Vec::new(),
            concurrency: Concurrency {
                initial: limit,
                current: limit,
                transitions: Vec::new(),
            },
            budget: Budget {
                request_limit: plan.settings["request_budget"].as_u64(),
                ..Budget::default()
            },
            requests: Vec::new(),
            reused: BTreeMap::new(),
            documents: Vec::new(),
            publication_receipts: BTreeMap::new(),
            latest: HashMap::new(),
            repairs: HashMap::new(),
        })
    }

    pub(super) fn is_fixable(attempt: &Attempt) -> bool {
        matches!(
            (attempt.status, attempt.reason.as_deref()),
            (
                Status::Failed,
                Some(
                    "OCR_RATE_LIMITED"
                        | "rate_limited"
                        | "response_incomplete"
                        | "response_json_invalid"
                        | "response_mermaid_fence_invalid"
                        | "response_output_limit"
                        | "response_schema_invalid"
                        | "response_table_invalid"
                        | "response_unexpected_stop"
                )
            ) | (Status::Unknown, Some("connection_outcome_unknown"))
        )
    }

    pub(super) fn last(&self, key: &str) -> Option<&Attempt> {
        self.reused
            .get(key)
            .or_else(|| self.latest.get(key).map(|&i| &self.requests[i]))
    }
    pub(super) fn repair(&self, key: &str) -> Option<&Attempt> {
        self.repairs.get(key).map(|&i| &self.requests[i])
    }
    pub(super) fn retry_round(&self, key: &str) -> u64 {
        self.requests
            .iter()
            .rev()
            .find_map(|r| (r.key == key).then_some(r.retry_round).flatten())
            .unwrap_or(0)
    }

    pub(super) fn settle(
        &mut self,
        index: usize,
        tier: usize,
        result: Result<Conversion, Failure>,
        settings: &Value,
        may_stop: bool,
    ) -> Result<Settlement> {
        let mut stop_reason = None;
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
                    self.latest.insert(attempt.key.clone(), index);
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
        if may_stop {
            match status {
                Some(401) => stop_reason = Some("OCR_AUTH_UNAVAILABLE"),
                Some(403) => stop_reason = Some("OCR_QUOTA_EXHAUSTED"),
                Some(429) if tier == *limit && *limit == 1 => {
                    stop_reason = Some("OCR_RATE_LIMITED")
                }
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
        Ok(Settlement {
            cache_record,
            stop_reason,
        })
    }

    pub(super) fn record_admission(
        &mut self,
        key: &str,
        source_sha256: &Option<String>,
        pass: Pass,
        settings: &Value,
        path: &Path,
    ) -> Result<usize> {
        ensure!(
            self.budget
                .request_limit
                .is_none_or(|limit| self.budget.requests < limit),
            "OCR_REQUEST_BUDGET_EXHAUSTED"
        );
        match pass {
            Pass::Fix => ensure!(
                self.last(key).is_some_and(Self::is_fixable)
                    && self.retry_round(key) < rounds(settings, "retry_rounds"),
                "invalid or unsafe journal retry history"
            ),
            Pass::Repair => ensure!(
                self.repair(key)
                    .or_else(|| self.last(key))
                    .is_some_and(|last| last.status == Status::Success
                        && last.warnings.is_some()
                        && last.repair_round.unwrap_or(0)
                            < rounds(settings, "mermaid_repair_rounds")),
                "invalid or unsafe journal retry history"
            ),
            Pass::Initial => {
                ensure!(
                    self.last(key).is_none_or(
                        |last| last.status == Status::Failed && last.repair_round.is_none()
                    ),
                    "invalid or unsafe journal retry history"
                )
            }
        }
        let index = self.requests.len();
        let mut attempt = Attempt::new(key.into(), Status::Admitted);
        attempt.source_sha256.clone_from(source_sha256);
        match pass {
            Pass::Repair => {
                attempt.repair_round =
                    Some(self.repair(key).and_then(|r| r.repair_round).unwrap_or(0) + 1);
                self.repairs.insert(key.into(), index);
            }
            Pass::Fix | Pass::Initial => {
                if pass == Pass::Fix {
                    attempt.retry_round = Some(self.retry_round(key) + 1);
                }
                self.latest.insert(key.into(), index);
            }
        }
        self.requests.push(attempt);
        self.budget.requests += 1;
        self.persist(path)?;
        Ok(index)
    }

    pub(super) fn persist(&self, path: &Path) -> Result<()> {
        atomic(path, &json_bytes(self)?, false)
    }

    pub(super) fn recover(&mut self, plan: &Plan, jobs: &HashMap<String, &Image>) -> Result<()> {
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
        self.latest = latest;
        self.repairs = repairs;
        Ok(())
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
                            last.repair_round.is_none() && Self::is_fixable(last)
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

#[cfg(test)]
mod tests {
    use super::*;

    fn journal() -> Journal {
        Journal::new(&Plan {
            digest: "frozen-plan".into(),
            settings: json!({"concurrency": 1, "request_budget": 3}),
            documents: Vec::new(),
            protected: Default::default(),
        })
        .unwrap()
    }

    #[test]
    fn unsafe_live_retry_cannot_create_a_persisted_admission() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("journal.json");
        let mut journal = journal();
        let mut unknown = Attempt::new("image".into(), Status::Unknown);
        unknown.reason = Some("OCR_OUTCOME_UNKNOWN".into());
        journal.requests.push(unknown);
        journal.latest.insert("image".into(), 0);
        journal.budget.requests = 1;
        journal.persist(&path).unwrap();
        let before = std::fs::read(&path).unwrap();
        let settings = json!({"retry_rounds": 2, "mermaid_repair_rounds": 2});
        for pass in [Pass::Initial, Pass::Fix, Pass::Repair] {
            assert!(
                journal
                    .record_admission("image", &None, pass, &settings, &path)
                    .is_err()
            );
            assert_eq!(journal.requests.len(), 1);
            assert_eq!(journal.budget.requests, 1);
            assert_eq!(std::fs::read(&path).unwrap(), before);
        }
    }

    #[test]
    fn runtime_indexes_never_become_durable_recovery_authority() {
        let mut journal = journal();
        journal.latest.insert("stale-image".into(), 99);
        journal.repairs.insert("stale-image".into(), 100);
        let value = serde_json::to_value(&journal).unwrap();
        assert!(value.get("latest").is_none());
        assert!(value.get("repairs").is_none());
        let restored: Journal = serde_json::from_value(value).unwrap();
        assert!(restored.latest.is_empty());
        assert!(restored.repairs.is_empty());
    }
}
