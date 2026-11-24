use crate::artifacts::{self, Image, Plan, atomic, json_bytes, read_bound, sha, validate_patch};
use crate::progress;
use crate::provider::{self, Failure};
use crate::workspace::{Workspace, lock_control, try_lock};
use anyhow::{Context, Result, ensure};
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::fs::{self, TryLockError};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

const JOURNAL_SCHEMA: &str = "mdocr-journal-1";
const RESULT_SCHEMA: &str = "mdocr-batch-result-1";
const ASSEMBLY_SCHEMA: &str = "mdocr-assembly-1";
const CANCEL_SCHEMA: &str = "mdocr-cancel-1";
const CANCELED: &str = "BATCH_CANCELED";
const HEARTBEAT: Duration = Duration::from_secs(5);

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum Status {
    Admitted,
    Unknown,
    Failed,
    Success,
    Reused,
}

#[derive(Deserialize, Serialize)]
struct Attempt {
    key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    source_sha256: Option<String>,
    status: Status,
    #[serde(skip_serializing_if = "Option::is_none")]
    usage_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    retry_round: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    repair_round: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    patches: Option<[Value; 1]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    warnings: Option<[String; 1]>,
}

impl Attempt {
    fn new(key: String, status: Status) -> Self {
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

    fn patch(&self) -> Option<&Value> {
        self.patches.as_ref().map(|patches| &patches[0])
    }
}

#[derive(Default, Deserialize, Serialize)]
struct Budget {
    requests: u64,
    tokens: u64,
    request_limit: Option<u64>,
}

#[derive(Deserialize, Serialize)]
struct Concurrency {
    initial: usize,
    #[serde(rename = "final")]
    current: usize,
    transitions: Vec<Value>,
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
pub(crate) struct PublicationReceipt {
    pub(crate) path: PathBuf,
    pub(crate) sha256: String,
}

pub(crate) struct PublicationState {
    pub(crate) status: String,
    pub(crate) receipts: Vec<PublicationReceipt>,
    pub(crate) unresolved: bool,
}

#[derive(Deserialize, Serialize)]
struct Journal {
    schema_version: String,
    plan_sha256: String,
    runtime_version: String,
    status: String,
    causes: Vec<String>,
    concurrency: Concurrency,
    budget: Budget,
    requests: Vec<Attempt>,
    #[serde(default)]
    reused: BTreeMap<String, Attempt>,
    documents: Vec<Value>,
    #[serde(default)]
    publication_receipts: BTreeMap<String, PublicationReceipt>,
}

struct Layout<'a> {
    jobs: HashMap<String, &'a Image>,
    bindings: Vec<Vec<Option<String>>>,
    pipelines: Vec<Vec<String>>,
    published: HashSet<String>,
}

impl<'a> Layout<'a> {
    fn new(plan: &'a Plan, configuration: &Value, evidence: &Path) -> Result<Self> {
        let llm = plan.settings["mode"] == "llm";
        let mut cache_config = configuration.clone();
        if rounds(&plan.settings, "mermaid_repair_rounds") > 0 {
            cache_config["mermaid_repair_rounds"] = plan.settings["mermaid_repair_rounds"].clone();
        }
        let mut layout = Self {
            jobs: HashMap::new(),
            bindings: Vec::new(),
            pipelines: Vec::new(),
            published: HashSet::new(),
        };
        for (index, doc) in plan.documents.iter().enumerate() {
            let mut bindings = Vec::new();
            let mut pipeline = Vec::new();
            for image in &doc.images {
                if !image.selected || image.error.is_some() {
                    bindings.push(None);
                    continue;
                }
                let key = sha(&json_bytes(&json!([cache_config, image.sha256]))?);
                layout.jobs.entry(key.clone()).or_insert(image);
                if evidence
                    .join(index.to_string())
                    .join(format!("{}.md", image.id))
                    .try_exists()?
                {
                    layout.published.insert(key.clone());
                }
                bindings.push(Some(key.clone()));
                if llm {
                    pipeline.push(key);
                } else {
                    layout.pipelines.push(vec![key]);
                }
            }
            if !pipeline.is_empty() {
                layout.pipelines.push(pipeline);
            }
            layout.bindings.push(bindings);
        }
        Ok(layout)
    }
}

struct Engine<'a> {
    settings: &'a Value,
    http: Client,
    paddle_token: std::sync::OnceLock<String>,
    llm_auth: std::sync::OnceLock<crate::llm::Auth>,
}

struct Conversion {
    patch: Value,
    mermaid_errors: Vec<Value>,
    usage_tokens: Option<u64>,
}

impl Engine<'_> {
    fn preflight(&self) -> Result<(), Failure> {
        if self.settings["mode"] == "llm" {
            if self.llm_auth.get().is_none() {
                let auth = crate::llm::preflight(&self.http, self.settings)?;
                self.llm_auth
                    .set(auth)
                    .unwrap_or_else(|_| unreachable!("preflight runs on the scheduler"));
            }
        } else if self.paddle_token.get().is_none() {
            let token = crate::config::paddle_token(self.settings)?;
            self.paddle_token
                .set(token)
                .unwrap_or_else(|_| unreachable!("preflight runs on the scheduler"));
        }
        Ok(())
    }

    fn convert(&self, image: &Image, repair: Option<&Value>) -> Result<Conversion, Failure> {
        let (Some(path), Some(digest)) = (&image.source_path, &image.sha256) else {
            return Err(Failure::known("IMAGE_DRIFT"));
        };
        let bytes =
            read_bound(path, digest, "IMAGE_DRIFT").map_err(|_| Failure::known("IMAGE_DRIFT"))?;
        if self.settings["mode"] == "llm" {
            let prepared = crate::llm::prepare(&bytes)?;
            let auth = self
                .llm_auth
                .get()
                .expect("LLM admission requires preflight");
            let prompt = self.settings["prompt"]
                .as_str()
                .expect("validated OCR prompt");
            let result = crate::llm::convert(&self.http, auth, prepared, prompt, repair)?;
            validate_patch(&result.patch).map_err(|reason| Failure {
                usage_tokens: result.usage_tokens,
                ..Failure::known(reason)
            })?;
            return Ok(Conversion {
                patch: result.patch,
                mermaid_errors: result.mermaid_errors,
                usage_tokens: result.usage_tokens,
            });
        }
        let original = crate::paddle::prepare(&bytes)
            .map_err(|_| Failure::known("IMAGE_PROCESSING_FAILED"))?;
        let token = self
            .paddle_token
            .get()
            .ok_or_else(|| Failure::known("OCR_AUTH_UNAVAILABLE"))?;
        let text = crate::paddle::convert(
            &self.http,
            self.settings["endpoint"].as_str(),
            token,
            original,
        )?;
        let patch = json!({"markdown":text, "uncertainty":""});
        validate_patch(&patch).map_err(Failure::known)?;
        Ok(Conversion {
            patch,
            mermaid_errors: Vec::new(),
            usage_tokens: None,
        })
    }
}

struct Execution<'a> {
    plan: &'a Plan,
    layout: Layout<'a>,
    config: Value,
    state: Journal,
    workspace: &'a Workspace,
    stop: Option<&'static str>,
    latest: HashMap<String, usize>,
    repairs: HashMap<String, usize>,
    deadline: Instant,
    retry_failed: bool,
}

#[derive(Clone, Copy, PartialEq)]
enum Pass {
    Initial,
    Fix,
    Repair,
}

fn rounds(settings: &Value, key: &str) -> u64 {
    settings[key].as_u64().unwrap_or(0)
}

fn configuration(settings: &Value, version: &str) -> Value {
    if settings["mode"] == "llm" {
        json!({
            "mode":"llm", "version":version, "model":settings["model"],
            "effort":settings["effort"], "schema":settings["llm_schema"],
            "prompt_sha256":sha(settings["prompt"].as_str().unwrap_or_default().as_bytes())
        })
    } else {
        json!({
            "mode":"paddle", "version":version, "model":crate::paddle::MODEL,
            "jpeg_quality":crate::paddle::JPEG_QUALITY, "endpoint":settings["endpoint"]
        })
    }
}

fn read_journal(path: &Path) -> Result<Journal> {
    serde_json::from_slice(&fs::read(path)?)
        .with_context(|| format!("invalid journal {}", path.display()))
}

fn validate_receipts(journal: &Journal, outputs: &HashMap<&str, &Path>) -> Result<()> {
    for (id, receipt) in &journal.publication_receipts {
        ensure!(
            outputs.get(id.as_str()).copied() == Some(receipt.path.as_path())
                && receipt.path.is_absolute()
                && receipt.sha256.len() == 64
                && receipt
                    .sha256
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()),
            "publication receipt does not match its frozen document"
        );
    }
    Ok(())
}

pub(crate) fn publication_state(workspace: &Workspace) -> Result<Option<PublicationState>> {
    artifacts::owned_path(&workspace.journal)?;
    if !workspace.journal.try_exists()? {
        return Ok(None);
    }
    artifacts::owned_path(&workspace.plan)?;
    ensure!(
        workspace.journal.is_file() && workspace.plan.is_file(),
        "publication state requires a journal and frozen plan file"
    );
    let bytes = fs::read(&workspace.plan).context("workspace plan is missing")?;
    let frozen: Value =
        serde_json::from_slice(&bytes).context("invalid frozen publication plan")?;
    let journal = read_journal(&workspace.journal)?;
    ensure!(
        frozen["schema_version"] == crate::plan::FROZEN_SCHEMA
            && frozen["work_path"].as_str().map(Path::new) == Some(workspace.work.as_path())
            && journal.schema_version == JOURNAL_SCHEMA
            && journal.plan_sha256 == sha(&bytes)
            && matches!(
                journal.status.as_str(),
                "running" | "completed" | "partial" | "canceled"
            ),
        "workspace publication state does not match its plan"
    );
    let mut outputs = HashMap::new();
    for document in frozen["documents"]
        .as_array()
        .context("invalid frozen documents")?
    {
        let id = document["document_id"]
            .as_str()
            .context("invalid frozen document identity")?;
        let output = document["output"]
            .as_str()
            .map(Path::new)
            .context("invalid frozen output path")?;
        ensure!(
            outputs.insert(id, output).is_none(),
            "duplicate frozen document identity"
        );
    }
    validate_receipts(&journal, &outputs)?;
    let publication = PublicationState {
        status: journal.status,
        receipts: journal.publication_receipts.into_values().collect(),
        unresolved: journal
            .requests
            .iter()
            .any(|attempt| matches!(attempt.status, Status::Admitted | Status::Unknown)),
    };
    ensure!(
        publication
            .receipts
            .iter()
            .all(|receipt| !receipt.path.starts_with(&workspace.root)
                && !workspace.root.starts_with(&receipt.path)
                && receipt.path != workspace.work),
        "publication receipt overlaps the work plan or workspace"
    );
    Ok(Some(publication))
}

fn validate_plan_receipts(plan: &Plan, journal: &Journal) -> Result<()> {
    ensure!(
        journal.schema_version == JOURNAL_SCHEMA && journal.plan_sha256 == plan.digest,
        "execution journal schema or plan binding is invalid"
    );
    let outputs = plan
        .documents
        .iter()
        .map(|document| (document.document_id.as_str(), document.output.as_path()))
        .collect();
    validate_receipts(journal, &outputs)
}

fn journal_for(plan: &Plan, result: &Path, ledger: &Path) -> Result<Journal> {
    validate_mirror(plan, result)?;
    if ledger.try_exists()? {
        let journal = read_journal(ledger)?;
        validate_plan_receipts(plan, &journal)?;
        for document in &plan.documents {
            if document.output.try_exists()? {
                let receipt = journal
                    .publication_receipts
                    .get(&document.document_id)
                    .with_context(|| {
                        format!(
                            "preexisting unowned document output: {}",
                            document.output.display()
                        )
                    })?;
                read_bound(
                    &document.output,
                    &receipt.sha256,
                    "OCR artifact has drifted",
                )?;
            }
        }
        return Ok(journal);
    }
    ensure!(
        !result.try_exists()?,
        "preexisting unowned execution result"
    );
    for document in &plan.documents {
        ensure!(
            !document.output.try_exists()?,
            "preexisting unowned document output"
        );
    }
    let limit = plan.settings["concurrency"]
        .as_u64()
        .context("missing concurrency")? as usize;
    Ok(Journal {
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
    })
}

fn validate_mirror(plan: &Plan, result: &Path) -> Result<()> {
    artifacts::owned_path(result)?;
    if result.try_exists()? {
        let mirror: Value = serde_json::from_slice(&fs::read(result)?)?;
        ensure!(
            mirror["schema_version"] == RESULT_SCHEMA && mirror["plan_sha256"] == plan.digest,
            "result is owned by another plan"
        );
    }
    Ok(())
}

impl<'a> Execution<'a> {
    fn new(
        plan: &'a Plan,
        workspace: &'a Workspace,
        state: Journal,
        retry_failed: bool,
    ) -> Result<Self> {
        let config = configuration(&plan.settings, &state.runtime_version);
        let layout = Layout::new(plan, &config, &workspace.evidence)?;
        let timeout = Duration::try_from_secs_f64(
            plan.settings["admission_seconds"]
                .as_f64()
                .context("missing admission window")?,
        )?;
        let deadline = Instant::now()
            .checked_add(timeout)
            .context("admission window too large")?;
        let mut execution = Self {
            plan,
            layout,
            config,
            state,
            workspace,
            stop: None,
            latest: HashMap::new(),
            repairs: HashMap::new(),
            deadline,
            retry_failed,
        };
        execution.restore()?;
        Ok(execution)
    }

    fn restore(&mut self) -> Result<()> {
        let settings = &self.plan.settings;
        let state = &mut self.state;
        ensure!(
            state.schema_version == JOURNAL_SCHEMA
                && state.plan_sha256 == self.plan.digest
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
        self.validate_history()?;
        let state = &mut self.state;
        for (i, attempt) in state.requests.iter_mut().enumerate() {
            if attempt.status == Status::Admitted {
                attempt.status = Status::Unknown;
                attempt.reason = Some("OCR_OUTCOME_UNKNOWN".into());
            }
            if attempt.repair_round.is_some() {
                self.repairs.insert(attempt.key.clone(), i);
                if attempt.status != Status::Success || attempt.warnings.is_some() {
                    continue;
                }
            }
            self.latest.insert(attempt.key.clone(), i);
        }
        for (key, attempt) in &state.reused {
            ensure!(
                key == &attempt.key
                    && self.layout.jobs.contains_key(key)
                    && attempt.status == Status::Reused,
                "invalid journal cache reuse"
            );
            validate_patch(attempt.patch().context("missing cached patch")?)
                .map_err(anyhow::Error::msg)?;
        }
        Ok(())
    }

    fn validate_history(&self) -> Result<()> {
        let mut latest: HashMap<&str, &Attempt> = HashMap::new();
        let mut fixes = HashMap::new();
        let mut accounted_tokens = 0u64;
        for attempt in &self.state.requests {
            let key = attempt.key.as_str();
            let image = self.layout.jobs.get(key).context(
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
                    round <= rounds(&self.plan.settings, "retry_rounds")
                        && previous.checked_add(1) == Some(round)
                        && last.is_some_and(|last| {
                            last.repair_round.is_none() && Self::is_fixable(last)
                        })
                }
                (None, Some(round)) => {
                    round <= rounds(&self.plan.settings, "mermaid_repair_rounds")
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
            accounted_tokens == self.state.budget.tokens,
            "invalid journal token tally"
        );
        Ok(())
    }

    fn save_journal(&self) -> Result<()> {
        atomic(&self.workspace.journal, &json_bytes(&self.state)?, false)
    }

    fn report(&self, schema: &str, status: &str, rows: Vec<Value>) -> Value {
        json!({
            "schema_version": schema,
            "plan_sha256": self.plan.digest,
            "status": status,
            "causes": self.state.causes,
            "budget": self.state.budget,
            "concurrency": self.state.concurrency,
            "documents": rows,
        })
    }

    fn save_result(&self) -> Result<()> {
        let report = self.report(
            RESULT_SCHEMA,
            &self.state.status,
            self.state.documents.clone(),
        );
        atomic(&self.workspace.result, &json_bytes(&report)?, false)
    }

    fn summary(&self, artifact_key: &str, path: &Path, status: &str, rows: &[Value]) -> Value {
        let complete = rows
            .iter()
            .filter(|row| row["status"] == "completed")
            .count();
        let mut summary = json!({
            "status": status,
            "counts": {
                "documents_total": self.plan.documents.len(),
                "documents_completed": complete,
                "requests": self.state.budget.requests,
            },
        });
        summary[artifact_key] = json!(path);
        if let Some(reason) = self.stop {
            summary["reason_code"] = json!(reason);
        }
        summary
    }

    fn observe_cancel(&mut self) -> Result<()> {
        if cancel_requested(self.workspace, &self.plan.digest)? {
            self.set_stop(CANCELED);
        }
        Ok(())
    }

    fn set_stop(&mut self, reason: &'static str) {
        self.stop = Some(reason);
        if !self.state.causes.iter().any(|cause| cause == reason) {
            self.state.causes.push(reason.into());
        }
    }

    fn progress_snapshot(&self, event: &str, active: usize) {
        let ready = self
            .plan
            .documents
            .iter()
            .zip(&self.layout.bindings)
            .filter(|(doc, keys)| {
                doc.images.iter().zip(*keys).all(|(image, key)| {
                    !image.selected
                        || key
                            .as_deref()
                            .and_then(|key| self.last(key))
                            .and_then(Attempt::patch)
                            .is_some_and(|patch| {
                                patch["kind"] != "hold" && patch.get("warnings").is_none()
                            })
                })
            })
            .count();
        self.emit_progress(event, active, None, Some(ready));
    }

    fn progress(&self, event: &str, active: usize, reason: Option<&str>) {
        self.emit_progress(event, active, reason, None);
    }

    fn emit_progress(
        &self,
        event: &str,
        active: usize,
        reason: Option<&str>,
        ready: Option<usize>,
    ) {
        let documents = ready
            .map(|ready| format!(" documents={ready}/{}", self.plan.documents.len()))
            .unwrap_or_default();
        let request_limit = self
            .state
            .budget
            .request_limit
            .map(|limit| limit.to_string())
            .unwrap_or_else(|| "unlimited".into());
        progress::emit(
            event,
            format_args!(
                "phase=ocr{documents} in_flight={active} requests={}/{request_limit} concurrency={} reason_code={}",
                self.state.budget.requests,
                self.state.concurrency.current,
                reason.or(self.stop).unwrap_or("none"),
            ),
        );
    }

    fn last(&self, key: &str) -> Option<&Attempt> {
        self.state
            .reused
            .get(key)
            .or_else(|| self.latest.get(key).map(|&i| &self.state.requests[i]))
    }

    fn is_fixable(attempt: &Attempt) -> bool {
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

    fn read_cache(&self, key: &str) -> Result<Option<[Value; 1]>> {
        let Some(dir) = self.plan.settings["cache_dir"].as_str() else {
            return Ok(None);
        };
        let path = Path::new(dir).join(format!("{key}.json"));
        let data = match fs::read(path) {
            Ok(data) => data,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(err) => return Err(err.into()),
        };
        let Ok(record) = serde_json::from_slice::<Value>(&data) else {
            return Ok(None);
        };
        let Ok(patches) = serde_json::from_value::<[Value; 1]>(record["patches"].clone()) else {
            return Ok(None);
        };
        if record["key"] != key
            || record["sha256"] != sha(&json_bytes(&patches)?)
            || validate_patch(&patches[0]).is_err()
            || patches[0]["kind"] == "hold"
            || patches[0].get("warnings").is_some()
        {
            return Ok(None);
        }
        Ok(Some(patches))
    }

    fn admit(&mut self, engine: &Engine<'_>, key: &str, pass: Pass) -> Result<Option<usize>> {
        self.observe_cancel()?;
        if self.stop.is_some() {
            return Ok(None);
        }
        if Instant::now() >= self.deadline {
            self.set_stop("BATCH_DEADLINE_EXCEEDED");
            return Ok(None);
        }
        if self
            .state
            .budget
            .request_limit
            .is_some_and(|limit| self.state.budget.requests >= limit)
        {
            self.set_stop("OCR_REQUEST_BUDGET_EXHAUSTED");
            return Ok(None);
        }
        if let Err(failure) = engine.preflight() {
            self.set_stop(failure.reason);
            return Ok(None);
        }
        let _control = lock_control(&self.workspace.control)?;
        self.observe_cancel()?;
        if self.stop.is_some() {
            return Ok(None);
        }
        if Instant::now() >= self.deadline {
            self.set_stop("BATCH_DEADLINE_EXCEEDED");
            return Ok(None);
        }
        let mut attempt = Attempt::new(key.into(), Status::Admitted);
        attempt
            .source_sha256
            .clone_from(&self.layout.jobs[key].sha256);
        let index = self.state.requests.len();
        match pass {
            Pass::Repair => {
                attempt.repair_round = Some(
                    self.repairs
                        .get(key)
                        .and_then(|&i| self.state.requests[i].repair_round)
                        .unwrap_or(0)
                        + 1,
                );
                self.repairs.insert(key.into(), index);
            }
            Pass::Fix | Pass::Initial => {
                // Explicit retries still inherit earlier fix rounds for this image.
                if pass == Pass::Fix {
                    attempt.retry_round = Some(self.retry_round(key) + 1);
                }
                self.latest.insert(key.into(), index);
            }
        }
        self.state.requests.push(attempt);
        self.state.budget.requests += 1;
        self.save_journal()?;
        Ok(Some(index))
    }

    fn retry_round(&self, key: &str) -> u64 {
        self.state
            .requests
            .iter()
            .rev()
            .find_map(|r| (r.key == key).then_some(r.retry_round).flatten())
            .unwrap_or(0)
    }

    fn settle(
        &mut self,
        index: usize,
        tier: usize,
        result: Result<Conversion, Failure>,
    ) -> Result<()> {
        let attempt = &mut self.state.requests[index];
        let mut cache = None;
        let (status, usage) = match result {
            Ok(conversion) => {
                let warned = !conversion.mermaid_errors.is_empty();
                attempt.status = Status::Success;
                if warned && rounds(&self.plan.settings, "mermaid_repair_rounds") > 0 {
                    attempt.warnings = Some(["response_mermaid_syntax_invalid".into()]);
                }
                if attempt.repair_round.is_none() || !warned {
                    self.latest.insert(attempt.key.clone(), index);
                }
                if !warned && conversion.patch["kind"] != "hold" {
                    cache = self.plan.settings["cache_dir"].as_str();
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
            self.state.budget.tokens = self
                .state
                .budget
                .tokens
                .checked_add(tokens)
                .context("token usage overflow")?;
            attempt.usage_tokens = Some(tokens);
        }
        let limit = &mut self.state.concurrency.current;
        if self.stop.is_none() {
            match status {
                Some(401) => self.stop = Some("OCR_AUTH_UNAVAILABLE"),
                Some(403) => self.stop = Some("OCR_QUOTA_EXHAUSTED"),
                Some(429) if tier == *limit && *limit == 1 => self.stop = Some("OCR_RATE_LIMITED"),
                Some(429) if tier == *limit => {
                    *limit -= 1;
                    self.state
                        .concurrency
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
        if let Some(reason) = self.stop {
            self.set_stop(reason);
        }
        self.save_journal()?;
        if let Some((path, data)) = cache_record {
            atomic(&path, &data, false)?;
        }
        Ok(())
    }

    fn dispatch(&mut self, engine: &Engine<'_>) -> Result<()> {
        self.state.status = "running".into();
        let mut pending = HashMap::new();
        for key in self.layout.jobs.keys() {
            if self
                .last(key)
                .is_some_and(|last| last.status != Status::Failed || !self.retry_failed)
            {
                continue;
            }
            if let Some(patches) = self.read_cache(key)? {
                let mut attempt = Attempt::new(key.clone(), Status::Reused);
                attempt.patches = Some(patches);
                self.state.reused.insert(key.clone(), attempt);
            } else {
                pending.insert(key.clone(), None);
            }
        }
        self.state.documents = self.document_rows();
        self.save_journal()?;
        self.save_result()?;
        self.progress_snapshot("started", 0);
        self.dispatch_pass(engine, pending, Pass::Initial)?;
        while self.stop.is_none() {
            let limit = rounds(&self.plan.settings, "retry_rounds");
            let pending = self
                .layout
                .jobs
                .keys()
                .filter(|key| {
                    !self.layout.published.contains(*key)
                        && self.last(key).is_some_and(Self::is_fixable)
                        && self.retry_round(key) < limit
                })
                .map(|key| (key.clone(), None))
                .collect::<HashMap<_, _>>();
            if pending.is_empty() {
                break;
            }
            self.dispatch_pass(engine, pending, Pass::Fix)?;
        }
        while self.stop.is_none() {
            let pending = self.pending_repairs()?;
            if pending.is_empty() {
                break;
            }
            self.dispatch_pass(engine, pending, Pass::Repair)?;
        }
        Ok(())
    }

    fn pending_repairs(&self) -> Result<HashMap<String, Option<Value>>> {
        let limit = rounds(&self.plan.settings, "mermaid_repair_rounds");
        let mut pending = HashMap::new();
        for key in self.layout.jobs.keys() {
            let Some(original) = self.last(key) else {
                continue;
            };
            let newest = self
                .repairs
                .get(key)
                .map_or(original, |&i| &self.state.requests[i]);
            if self.layout.published.contains(key)
                || newest.repair_round.unwrap_or(0) >= limit
                || newest.status != Status::Success
                || newest.warnings.is_none()
            {
                continue;
            }
            let patch = newest.patch().context("repair candidate has no patch")?;
            let errors = crate::llm::mermaid_diagnostics(patch)
                .map_err(|failure| anyhow::anyhow!(failure.reason))?;
            if !errors.is_empty() {
                pending.insert(
                    key.clone(),
                    Some(json!({
                        "kind": patch["kind"], "markdown": patch["markdown"],
                        "uncertainty": patch["uncertainty"], "errors": errors,
                    })),
                );
            }
        }
        Ok(pending)
    }

    fn dispatch_pass(
        &mut self,
        engine: &Engine<'_>,
        mut pending: HashMap<String, Option<Value>>,
        pass: Pass,
    ) -> Result<()> {
        if pending.is_empty() {
            return Ok(());
        }
        let mut waiting = self
            .layout
            .pipelines
            .iter()
            .filter_map(|pipeline| {
                let queue: VecDeque<_> = pipeline
                    .iter()
                    .filter(|key| pending.contains_key(*key))
                    .cloned()
                    .collect();
                (!queue.is_empty()).then_some(queue)
            })
            .collect::<VecDeque<_>>();
        thread::scope(|scope| -> Result<()> {
            let (sender, receiver) = mpsc::channel();
            let mut active = HashMap::<String, usize>::new();
            let mut slots: Vec<VecDeque<String>> = Vec::new();
            let mut last_snapshot = Instant::now();
            loop {
                self.observe_cancel()?;
                let tier = self.state.concurrency.current;
                if self.stop.is_none() && active.values().all(|&old| old <= tier) {
                    for slot in &mut slots {
                        discard_finished(slot, &pending, &active);
                    }
                    slots.retain(|slot| !slot.is_empty());
                    while slots.len() < tier {
                        let Some(mut slot) = waiting.pop_front() else {
                            break;
                        };
                        discard_finished(&mut slot, &pending, &active);
                        if !slot.is_empty() {
                            slots.push(slot);
                        }
                    }
                    for slot in slots.iter().take(tier) {
                        let key = &slot[0];
                        if active.contains_key(key) {
                            continue;
                        }
                        let Some(repair) = pending.remove(key) else {
                            continue;
                        };
                        let Some(index) = self.admit(engine, key, pass)? else {
                            self.progress("admission_stopped", active.len(), None);
                            break;
                        };
                        let image = self.layout.jobs[key];
                        let key = key.clone();
                        active.insert(key.clone(), tier);
                        let event = match pass {
                            Pass::Initial => "admitted",
                            Pass::Fix => "retry_admitted",
                            Pass::Repair => "repair_admitted",
                        };
                        self.progress(event, active.len(), None);
                        let sender = sender.clone();
                        scope.spawn(move || {
                            let result =
                                std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                                    engine.convert(image, repair.as_ref())
                                }));
                            let _ = sender.send((key, index, tier, result));
                        });
                    }
                }
                if active.is_empty() {
                    break;
                }
                let (key, index, tier, result) = match receiver.recv_timeout(HEARTBEAT) {
                    Ok(settled) => settled,
                    Err(mpsc::RecvTimeoutError::Timeout) => {
                        self.progress_snapshot("waiting", active.len());
                        last_snapshot = Instant::now();
                        continue;
                    }
                    Err(error) => return Err(error.into()),
                };
                active.remove(&key);
                let concurrency = self.state.concurrency.current;
                self.settle(
                    index,
                    tier,
                    result.map_err(|_| anyhow::anyhow!("OCR worker panicked after admission"))?,
                )?;
                self.progress(
                    "settled",
                    active.len(),
                    self.state.requests[index].reason.as_deref(),
                );
                if self.state.concurrency.current != concurrency {
                    self.progress("throttled", active.len(), None);
                }
                if last_snapshot.elapsed() >= HEARTBEAT {
                    self.progress_snapshot("progress", active.len());
                    last_snapshot = Instant::now();
                }
            }
            Ok(())
        })
    }

    fn document_records(&self, index: usize) -> (Vec<Value>, Vec<Option<&Value>>) {
        let document = &self.plan.documents[index];
        let bindings = &self.layout.bindings[index];
        let mut records = Vec::with_capacity(document.images.len());
        let mut patches = Vec::with_capacity(document.images.len());
        for (image, key) in document.images.iter().zip(bindings) {
            let mut record =
                json!({"id": image.id, "resource_id": image.resource_id, "status":"unselected"});
            let mut patch = None;
            if image.selected {
                if let Some(error) = &image.error {
                    record["status"] = json!("failed");
                    record["reason_code"] = json!(error);
                } else if let Some(attempt) = key.as_deref().and_then(|key| self.last(key)) {
                    record["status"] = json!(attempt.status);
                    if let Some(value) = attempt.patch() {
                        if value["kind"] == "hold" {
                            record["status"] = json!("held");
                            record["reason_code"] = json!("IMAGE_CONVERSION_HELD");
                            record["uncertainty"] = value["uncertainty"].clone();
                        } else if let Some(warnings) = value.get("warnings") {
                            record["status"] = json!("failed");
                            record["reason_code"] = json!("response_mermaid_syntax_invalid");
                            record["warnings"] = warnings.clone();
                        } else {
                            patch = Some(value);
                        }
                    } else if let Some(reason) = &attempt.reason {
                        record["reason_code"] = json!(reason);
                    }
                } else {
                    record["status"] = json!("unattempted");
                    if let Some(reason) = self.stop {
                        record["reason_code"] = json!(reason);
                    }
                }
            }
            patches.push(patch);
            records.push(record);
        }
        (records, patches)
    }

    fn document_rows(&self) -> Vec<Value> {
        self.plan
            .documents
            .iter()
            .enumerate()
            .map(|(index, document)| {
                if let Some(row) = self.state.documents.get(index).filter(|row| {
                    row["status"] == "completed" && row["document_id"] == document.document_id
                }) {
                    return row.clone();
                }
                let (records, _) = self.document_records(index);
                json!({
                    "document_id": document.document_id,
                    "path": document.path,
                    "status": "partial",
                    "output": null,
                    "resources": records,
                })
            })
            .collect()
    }

    fn render_document(
        &self,
        index: usize,
        evidence: &Path,
    ) -> Result<artifacts::RenderedDocument> {
        let (records, patches) = self.document_records(index);
        artifacts::render_document(
            &self.plan.documents[index],
            &evidence.join(index.to_string()),
            &self.config,
            records,
            &patches,
        )
    }

    fn bind_publication(&mut self, index: usize, digest: &str) -> Result<bool> {
        let document = &self.plan.documents[index];
        let receipt = PublicationReceipt {
            path: document.output.clone(),
            sha256: digest.into(),
        };
        if let Some(saved) = self.state.publication_receipts.get(&document.document_id) {
            ensure!(
                saved == &receipt,
                "publication receipt differs from the rendered document"
            );
            return Ok(false);
        }
        artifacts::owned_path(&document.output)?;
        ensure!(
            !document.output.try_exists()?,
            "preexisting unowned document output: {}",
            document.output.display()
        );
        self.state
            .publication_receipts
            .insert(document.document_id.clone(), receipt);
        self.save_journal()?;
        Ok(true)
    }

    fn publish_documents(&mut self) -> Result<Vec<Value>> {
        let mut rows = Vec::with_capacity(self.plan.documents.len());
        for index in 0..self.plan.documents.len() {
            let rendered = self.render_document(index, &self.workspace.evidence)?;
            let first_write = if let Some(digest) = rendered.output_sha256() {
                self.bind_publication(index, digest)?
            } else {
                false
            };
            rows.push(rendered.publish(&self.plan.documents[index].output, first_write)?);
        }
        Ok(rows)
    }

    fn finish(&mut self, publish: bool) -> Result<(u8, Value)> {
        self.state.documents = if publish {
            self.publish_documents()?
        } else {
            self.document_rows()
        };
        let (code, status) = if self.stop == Some(CANCELED) {
            (3, "canceled")
        } else if self
            .state
            .documents
            .iter()
            .all(|doc| doc["status"] == "completed")
        {
            (0, "completed")
        } else {
            (2, "partial")
        };
        self.state.status = status.into();
        self.save_journal()?;
        self.save_result()?;
        self.progress_snapshot(
            if status == "canceled" {
                "canceled"
            } else {
                "published"
            },
            0,
        );
        Ok((
            code,
            self.summary(
                "result_path",
                &self.workspace.result,
                status,
                &self.state.documents,
            ),
        ))
    }
}

fn discard_finished(
    queue: &mut VecDeque<String>,
    pending: &HashMap<String, Option<Value>>,
    active: &HashMap<String, usize>,
) {
    while queue
        .front()
        .is_some_and(|key| !pending.contains_key(key) && !active.contains_key(key))
    {
        queue.pop_front();
    }
}

fn load_workspace(workspace: &Workspace) -> Result<Plan> {
    let locks = [workspace.lock.clone(), workspace.control.clone()];
    let plan = artifacts::load_plan(
        &workspace.plan,
        &workspace.result,
        &workspace.journal,
        &workspace.evidence,
        &locks,
    )?;
    read_bound(&workspace.plan, &plan.digest, "PLAN_DRIFT")?;
    Ok(plan)
}

fn cancel_requested(workspace: &Workspace, digest: &str) -> Result<bool> {
    artifacts::owned_path(&workspace.cancel)?;
    let data = match fs::read(&workspace.cancel) {
        Ok(data) => data,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error.into()),
    };
    let marker: Value = serde_json::from_slice(&data)?;
    ensure!(
        marker["schema_version"] == CANCEL_SCHEMA && marker["plan_sha256"] == digest,
        "cancel marker is bound to a different frozen plan"
    );
    marker["requested"]
        .as_bool()
        .context("invalid cancel marker state")
}

fn write_cancel(workspace: &Workspace, digest: &str, requested: bool) -> Result<()> {
    let marker = json!({
        "schema_version": CANCEL_SCHEMA,
        "plan_sha256": digest,
        "requested": requested,
    });
    atomic(&workspace.cancel, &json_bytes(&marker)?, false)
}

pub(crate) fn frozen_batch_canceled(workspace: &Workspace, digest: &str) -> Result<bool> {
    let _control = lock_control(&workspace.control)?;
    if cancel_requested(workspace, digest)? {
        return Ok(true);
    }
    artifacts::owned_path(&workspace.journal)?;
    if !workspace.journal.try_exists()? {
        return Ok(false);
    }
    let state = read_journal(&workspace.journal)?;
    ensure!(
        state.schema_version == JOURNAL_SCHEMA && state.plan_sha256 == digest,
        "execution journal schema or plan binding is invalid"
    );
    Ok(state.status == "canceled")
}

pub(crate) fn run(workspace: &Workspace, resume: bool, retry_failed: bool) -> Result<(u8, Value)> {
    let plan = load_workspace(workspace)?;
    let state = journal_for(&plan, &workspace.result, &workspace.journal)?;
    let mut execution = Execution::new(&plan, workspace, state, retry_failed)?;
    {
        let _control = lock_control(&workspace.control)?;
        if execution.state.status == "completed" {
            return execution.finish(true);
        }
        let requested = cancel_requested(workspace, &plan.digest)?;
        if requested || execution.state.status == "canceled" {
            if !resume {
                execution.set_stop(CANCELED);
                return execution.finish(false);
            }
            write_cancel(workspace, &plan.digest, false)?;
            execution.state.status = "running".into();
            execution.save_journal()?;
        }
    }
    let engine = Engine {
        settings: &plan.settings,
        http: provider::client_for(&plan.settings)?,
        paddle_token: std::sync::OnceLock::new(),
        llm_auth: std::sync::OnceLock::new(),
    };
    execution.dispatch(&engine)?;
    let _control = lock_control(&workspace.control)?;
    execution.observe_cancel()?;
    execution.finish(true)
}

pub(crate) fn cancel(workspace: &Workspace) -> Result<(u8, Value)> {
    ensure!(workspace.plan.is_file(), "work plan has not been accepted");
    workspace.ensure_owner()?;
    let execution_lock = match try_lock(&workspace.lock) {
        Ok(lock) => Some(lock),
        Err(error)
            if matches!(
                error.downcast_ref::<TryLockError>(),
                Some(TryLockError::WouldBlock)
            ) =>
        {
            None
        }
        Err(error) => return Err(error),
    };
    let _control = lock_control(&workspace.control)?;
    workspace.ensure_live()?;
    workspace.ensure_owner()?;

    // Request cancellation even if the source document or a bound image
    // changed while an active scheduler owns the workspace.
    artifacts::owned_path(&workspace.plan)?;
    let raw = fs::read(&workspace.plan)?;
    let digest = sha(&raw);
    let frozen: Value = serde_json::from_slice(&raw)?;
    ensure!(
        frozen["schema_version"] == "mdocr-frozen-plan-1",
        "invalid frozen plan for cancellation"
    );
    if workspace.journal.try_exists()? {
        artifacts::owned_path(&workspace.journal)?;
        let state = read_journal(&workspace.journal)?;
        ensure!(
            state.schema_version == JOURNAL_SCHEMA && state.plan_sha256 == digest,
            "execution journal schema or plan binding is invalid"
        );
        if state.status == "completed" {
            return Ok((
                0,
                json!({"status":"completed", "result_path":workspace.result}),
            ));
        }
    }
    if !cancel_requested(workspace, &digest)? {
        write_cancel(workspace, &digest, true)?;
    }
    if execution_lock.is_some() {
        let plan = load_workspace(workspace)?;
        let state = journal_for(&plan, &workspace.result, &workspace.journal)?;
        let mut execution = Execution::new(&plan, workspace, state, false)?;
        execution.set_stop(CANCELED);
        let (_, summary) = execution.finish(false)?;
        Ok((0, summary))
    } else {
        progress::emit("cancel_requested", format_args!("phase=ocr"));
        Ok((
            0,
            json!({"status":"cancel_requested", "result_path":workspace.result}),
        ))
    }
}

pub(crate) fn assemble(workspace: &Workspace, output_dir: PathBuf) -> Result<(u8, Value)> {
    let mut plan = load_workspace(workspace)?;
    validate_mirror(&plan, &workspace.result)?;
    let raw = fs::read(&workspace.journal)?;
    let digest = sha(&raw);
    let state: Journal = serde_json::from_slice(&raw)?;
    validate_plan_receipts(&plan, &state)?;
    let output = artifacts::resolve(&output_dir)?;
    ensure!(!output.try_exists()?, "assembly directory must be new");
    let cache = plan.settings["cache_dir"].as_str().map(PathBuf::from);
    for path in plan
        .protected
        .iter()
        .chain([&workspace.root, &workspace.work])
        .chain(cache.iter())
        .chain(plan.documents.iter().map(|doc| &doc.output))
    {
        ensure!(
            !output.starts_with(path) && !path.starts_with(&output),
            "assembly destination overlaps original execution artifacts"
        );
    }
    for (index, doc) in plan.documents.iter_mut().enumerate() {
        doc.output = output.join("documents").join(format!("{index}.md"));
    }
    let execution = Execution::new(&plan, workspace, state, false)?;
    read_bound(&workspace.plan, &plan.digest, "PLAN_DRIFT")?;
    read_bound(&workspace.journal, &digest, "JOURNAL_DRIFT")?;
    fs::create_dir(&output)?;
    let evidence = output.join("evidence");
    let rows = plan
        .documents
        .iter()
        .enumerate()
        .map(|(index, document)| {
            execution
                .render_document(index, &evidence)?
                .publish(&document.output, true)
        })
        .collect::<Result<Vec<_>>>()?;
    let (code, status) = if rows.iter().all(|row| row["status"] == "completed") {
        (0, "completed")
    } else {
        (2, "partial")
    };
    let path = output.join("assembly.json");
    let summary = execution.summary("assembly_path", &path, status, &rows);
    let mut report = execution.report(ASSEMBLY_SCHEMA, status, rows);
    report["recovery"] = json!({
        "journal_sha256": digest,
        "execution_version": execution.state.runtime_version,
    });
    atomic(&path, &json_bytes(&report)?, true)?;
    Ok((code, summary))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invalid_mermaid_patch_can_be_repaired_but_cannot_be_published() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let root = directory.path();
        fs::write(root.join("source.md"), "![workflow](image.png)\n")?;
        image::RgbImage::from_pixel(1, 1, image::Rgb([10, 20, 30])).save(root.join("image.png"))?;
        let work = root.join("work.json");
        fs::write(
            &work,
            json_bytes(&json!({
                "schema_version": "mdocr-work-1",
                "output_dir": "out",
                "documents": [{"path": "source.md"}],
            }))?,
        )?;
        let workspace = Workspace::new(&work)?;
        let settings = crate::config::load(None, None)?;
        crate::plan::accept(
            &workspace.work,
            &workspace.plan,
            &workspace.images,
            Some(&settings),
            None,
            None,
        )?;
        let plan = load_workspace(&workspace)?;
        let state = journal_for(&plan, &workspace.result, &workspace.journal)?;
        let mut execution = Execution::new(&plan, &workspace, state, false)?;
        let (key, image) = execution
            .layout
            .jobs
            .iter()
            .next()
            .context("fixture image")?;
        let mut admitted = Attempt::new(key.clone(), Status::Admitted);
        admitted.source_sha256.clone_from(&image.sha256);
        execution.state.requests.push(admitted);
        execution.state.budget.requests = 1;
        let patch = json!({
            "kind": "mermaid", "markdown": "```mermaid\nnot-a-real-diagram\n```",
            "uncertainty": "", "warnings": ["response_mermaid_syntax_invalid"],
        });
        let errors = crate::llm::mermaid_diagnostics(&patch)
            .map_err(|failure| anyhow::anyhow!(failure.reason))?;
        assert!(!errors.is_empty());
        execution.settle(
            0,
            3,
            Ok(Conversion {
                patch,
                mermaid_errors: errors,
                usage_tokens: None,
            }),
        )?;
        assert_eq!(execution.pending_repairs()?.len(), 1);

        let rows = execution.publish_documents()?;
        assert_eq!(rows[0]["status"], "partial");
        let resource = &rows[0]["resources"][0];
        assert_eq!(resource["status"], "failed");
        assert_eq!(resource["reason_code"], "response_mermaid_syntax_invalid");
        assert!(!plan.documents[0].output.exists());
        Ok(())
    }
}
