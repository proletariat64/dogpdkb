use crate::artifacts::{self, Image, Plan, atomic, json_bytes, read_bound, sha, validate_patch};
use crate::provider::{self, Failure};
use anyhow::{Context, Result, ensure};
use clap::Args as ClapArgs;
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

#[derive(ClapArgs)]
pub(crate) struct Args {
    #[arg(long)]
    plan: PathBuf,
    #[arg(long)]
    result: PathBuf,
    #[arg(long)]
    retry_failed: bool,
}

#[derive(ClapArgs)]
pub(crate) struct AssembleArgs {
    #[arg(long)]
    plan: PathBuf,
    #[arg(long)]
    journal: PathBuf,
    #[arg(long)]
    execution_version: String,
    #[arg(long)]
    output_dir: PathBuf,
}

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
    reserved_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    usage_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    fix_round: Option<u64>,
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
            reserved_tokens: None,
            usage_tokens: None,
            reason: None,
            fix_round: None,
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
    reserved_tokens: u64,
}

#[derive(Deserialize, Serialize)]
struct Concurrency {
    initial: usize,
    #[serde(rename = "final")]
    current: usize,
    transitions: Vec<Value>,
}

#[derive(Deserialize, Serialize)]
struct Journal {
    schema_version: String,
    plan_sha256: String,
    result_path: PathBuf,
    status: String,
    causes: Vec<String>,
    concurrency: Concurrency,
    budget: Budget,
    requests: Vec<Attempt>,
    #[serde(default)]
    reused: BTreeMap<String, Attempt>,
    documents: Vec<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    recovery: Option<Value>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    legacy_request_sources: BTreeMap<String, String>,
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
    paddle_token: Option<String>,
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
        } else if self.paddle_token.is_none() {
            return Err(Failure::known("OCR_AUTH_UNAVAILABLE"));
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
            .as_deref()
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
    ledger: Option<PathBuf>,
    result: PathBuf,
    evidence: PathBuf,
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

fn sibling(path: &Path, suffix: &str) -> Result<PathBuf> {
    let mut name = path
        .file_name()
        .context("execution path has no basename")?
        .to_os_string();
    name.push(suffix);
    Ok(path.with_file_name(name))
}

fn lock(path: &Path) -> Result<File> {
    artifacts::owned_path(path)?;
    fs::create_dir_all(path.parent().context("lock has no parent")?)?;
    let file = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(path)?;
    file.try_lock()
        .with_context(|| format!("cannot lock {}", path.display()))?;
    Ok(file)
}

fn read_journal(path: &Path) -> Result<Journal> {
    serde_json::from_slice(&fs::read(path)?)
        .with_context(|| format!("invalid journal {}", path.display()))
}

fn journal_for(plan: &Plan, result: &Path, ledger: &Path) -> Result<Journal> {
    validate_mirror(plan, result)?;
    if ledger.try_exists()? {
        return read_journal(ledger);
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
        schema_version: "mdocr-result-2".into(),
        plan_sha256: plan.digest.clone(),
        result_path: result.into(),
        status: "running".into(),
        causes: Vec::new(),
        concurrency: Concurrency {
            initial: limit,
            current: limit,
            transitions: Vec::new(),
        },
        budget: Budget::default(),
        requests: Vec::new(),
        reused: BTreeMap::new(),
        documents: Vec::new(),
        recovery: None,
        legacy_request_sources: BTreeMap::new(),
    })
}

fn validate_mirror(plan: &Plan, result: &Path) -> Result<()> {
    artifacts::owned_path(result)?;
    if result.try_exists()? {
        let mirror: Value = serde_json::from_slice(&fs::read(result)?)?;
        ensure!(
            mirror["plan_sha256"] == plan.digest,
            "result is owned by another plan"
        );
    }
    Ok(())
}

impl<'a> Execution<'a> {
    fn new(
        plan: &'a Plan,
        ledger: PathBuf,
        result: PathBuf,
        evidence: PathBuf,
        retry_failed: bool,
    ) -> Result<Self> {
        let config = configuration(&plan.settings, env!("CARGO_PKG_VERSION"));
        let layout = Layout::new(plan, &config, &evidence)?;
        let state = journal_for(plan, &result, &ledger)?;
        let timeout = Duration::try_from_secs_f64(
            plan.settings["max_seconds"]
                .as_f64()
                .context("missing deadline")?,
        )?;
        let deadline = Instant::now()
            .checked_add(timeout)
            .context("deadline too large")?;
        Self::from_journal(
            plan,
            layout,
            config,
            Some(ledger),
            result,
            evidence,
            state,
            deadline,
            retry_failed,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn from_journal(
        plan: &'a Plan,
        layout: Layout<'a>,
        config: Value,
        ledger: Option<PathBuf>,
        result: PathBuf,
        evidence: PathBuf,
        state: Journal,
        deadline: Instant,
        retry_failed: bool,
    ) -> Result<Self> {
        let mut execution = Self {
            plan,
            layout,
            config,
            state,
            ledger,
            result,
            evidence,
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
            state.schema_version == "mdocr-result-2"
                && state.plan_sha256 == self.plan.digest
                && state.result_path == self.result
                && state.recovery.is_none()
                && matches!(state.status.as_str(), "running" | "partial" | "completed"),
            "execution is bound to a different plan or result path"
        );
        ensure!(
            state.concurrency.initial as u64 == rounds(settings, "concurrency")
                && (1..=state.concurrency.initial).contains(&state.concurrency.current)
                && state.budget.requests == state.requests.len() as u64
                && state.budget.reserved_tokens == 0
                && settings["request_budget"]
                    .as_u64()
                    .is_none_or(|n| state.budget.requests <= n),
            "invalid saved execution counters"
        );
        self.validate_history()?;
        let state = &mut self.state;
        for (i, attempt) in state.requests.iter_mut().enumerate() {
            if attempt.source_sha256.is_none() {
                let digest = self.layout.jobs[&attempt.key]
                    .sha256
                    .as_ref()
                    .expect("validated job digest");
                state
                    .legacy_request_sources
                    .insert(attempt.key.clone(), digest.clone());
            }
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
                (attempt.source_sha256.is_none() || attempt.source_sha256 == image.sha256)
                    && attempt.reserved_tokens == Some(0)
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
            let bounded = match (attempt.fix_round, attempt.repair_round) {
                (None, None) => last.is_none_or(|last| {
                    last.status == Status::Failed && last.repair_round.is_none()
                }),
                (Some(round), None) => {
                    let previous = fixes.insert(key, round).unwrap_or(0u64);
                    round <= rounds(&self.plan.settings, "fix")
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

    fn save(&self) -> Result<()> {
        let data = json_bytes(&self.state)?;
        if let Some(ledger) = &self.ledger {
            atomic(ledger, &data, false)?;
        }
        atomic(&self.result, &data, false)
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
                        | "svg_profile_cleanup_failed"
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
        if self.stop.is_some() {
            return Ok(None);
        }
        let settings = &self.plan.settings;
        self.stop = if Instant::now() >= self.deadline {
            Some("BATCH_DEADLINE_EXCEEDED")
        } else if settings["request_budget"]
            .as_u64()
            .is_some_and(|n| self.state.budget.requests >= n)
        {
            Some("OCR_REQUEST_BUDGET_EXHAUSTED")
        } else {
            match settings["token_budget"].as_u64() {
                Some(0) => Some("OCR_TOKEN_BUDGET_EXHAUSTED"),
                Some(_) => Some("OCR_TOKEN_BOUND_UNAVAILABLE"),
                None => None,
            }
        };
        if self.stop.is_some() {
            return Ok(None);
        }
        if let Err(failure) = engine.preflight() {
            self.stop = Some(failure.reason);
            return Ok(None);
        }
        if Instant::now() >= self.deadline {
            self.stop = Some("BATCH_DEADLINE_EXCEEDED");
            return Ok(None);
        }
        let mut attempt = Attempt::new(key.into(), Status::Admitted);
        attempt
            .source_sha256
            .clone_from(&self.layout.jobs[key].sha256);
        attempt.reserved_tokens = Some(0);
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
                    attempt.fix_round = Some(self.fix_round(key) + 1);
                }
                self.latest.insert(key.into(), index);
            }
        }
        self.state.requests.push(attempt);
        self.state.budget.requests += 1;
        self.save()?;
        Ok(Some(index))
    }

    fn fix_round(&self, key: &str) -> u64 {
        self.state
            .requests
            .iter()
            .rev()
            .find_map(|r| (r.key == key).then_some(r.fix_round).flatten())
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
        self.save()?;
        if let Some((path, data)) = cache_record {
            atomic(&path, &data, false)?;
        }
        Ok(())
    }

    fn dispatch(&mut self, engine: &Engine<'_>) -> Result<()> {
        self.state.documents.clear();
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
        self.save()?;
        self.dispatch_pass(engine, pending, Pass::Initial)?;
        while self.stop.is_none() {
            let limit = rounds(&self.plan.settings, "fix");
            let pending = self
                .layout
                .jobs
                .keys()
                .filter(|key| {
                    !self.layout.published.contains(*key)
                        && self.last(key).is_some_and(Self::is_fixable)
                        && self.fix_round(key) < limit
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
            loop {
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
                            break;
                        };
                        let image = self.layout.jobs[key];
                        let key = key.clone();
                        active.insert(key.clone(), tier);
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
                let (key, index, tier, result) = receiver.recv()?;
                active.remove(&key);
                self.settle(
                    index,
                    tier,
                    result.map_err(|_| anyhow::anyhow!("OCR worker panicked after admission"))?,
                )?;
            }
            Ok(())
        })
    }

    fn publish(&mut self) -> Result<u8> {
        let mut rows = Vec::with_capacity(self.plan.documents.len());
        for (index, document) in self.plan.documents.iter().enumerate() {
            let bindings = &self.layout.bindings[index];
            let mut records = Vec::with_capacity(document.images.len());
            let mut patches = Vec::with_capacity(document.images.len());
            for (image, key) in document.images.iter().zip(bindings) {
                let mut record = json!({"id": image.id, "resource_id": image.resource_id, "status":"unselected"});
                let mut patch = None;
                if image.selected {
                    if let Some(error) = &image.error {
                        record["status"] = json!("failed");
                        record["reason"] = json!(error);
                    } else if let Some(attempt) = key.as_deref().and_then(|key| self.last(key)) {
                        record["status"] = json!(attempt.status);
                        if let Some(value) = attempt.patch() {
                            if value["kind"] == "hold" {
                                record["status"] = json!("held");
                                record["reason"] = json!("IMAGE_CONVERSION_HELD");
                                record["uncertainty"] = value["uncertainty"].clone();
                            } else {
                                patch = Some(value);
                                if let Some(warnings) = value.get("warnings") {
                                    record["warnings"] = warnings.clone();
                                }
                            }
                        } else {
                            record["reason"] = json!(attempt.reason);
                        }
                    } else {
                        record["status"] = json!("unattempted");
                        record["reason"] = json!(self.stop);
                    }
                }
                patches.push(patch);
                records.push(record);
            }
            rows.push(artifacts::publish_document(
                document,
                &self.evidence.join(index.to_string()),
                &self.config,
                records,
                &patches,
            )?);
        }
        let complete = rows.iter().all(|row| row["status"] == "completed");
        self.state.documents = rows;
        if let Some(stop) = self.stop
            && !self.state.causes.iter().any(|cause| cause == stop)
        {
            self.state.causes.push(stop.into());
        }
        self.state.status = if complete { "completed" } else { "partial" }.into();
        self.save()?;
        Ok(if complete { 0 } else { 2 })
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

pub(crate) fn run(args: Args) -> Result<u8> {
    let plan_path = args.plan.canonicalize()?;
    let result = artifacts::resolve(&args.result)?;
    let ledger = sibling(&plan_path, ".execution.json")?;
    let evidence = sibling(&plan_path, ".evidence")?;
    let locks = [sibling(&plan_path, ".lock")?, sibling(&result, ".lock")?];
    let plan = artifacts::load_plan(&plan_path, &result, &ledger, &evidence, &locks)?;
    let _plan_lock = lock(&locks[0])?;
    let _result_lock = lock(&locks[1])?;
    read_bound(&plan_path, &plan.digest, "PLAN_DRIFT")?;
    let mut execution = Execution::new(&plan, ledger, result, evidence, args.retry_failed)?;
    let engine = Engine {
        settings: &plan.settings,
        http: provider::client()?,
        paddle_token: std::env::var("PADDLEOCR_TOKEN")
            .ok()
            .filter(|s| !s.is_empty()),
        llm_auth: std::sync::OnceLock::new(),
    };
    execution.dispatch(&engine)?;
    execution.publish()
}

pub(crate) fn assemble(args: AssembleArgs) -> Result<u8> {
    let parts = args.execution_version.split('.').collect::<Vec<_>>();
    ensure!(
        parts.len() == 3
            && parts
                .iter()
                .all(|part| !part.is_empty() && part.bytes().all(|b| b.is_ascii_digit())),
        "execution version must be MAJOR.MINOR.PATCH"
    );
    let plan_path = args.plan.canonicalize()?;
    let journal = args.journal.canonicalize()?;
    ensure!(
        journal == sibling(&plan_path, ".execution.json")?,
        "journal is not the plan's native execution journal"
    );
    let raw = fs::read(&journal)?;
    let digest = sha(&raw);
    let state: Journal = serde_json::from_slice(&raw)?;
    ensure!(
        state.status == "running" && state.documents.is_empty(),
        "journal is not an incomplete admission history"
    );
    let result = state.result_path.clone();
    ensure!(
        result.parent() == plan_path.parent(),
        "original result must be a canonical sibling path"
    );
    let output = std::path::absolute(args.output_dir)?;
    artifacts::owned_path(&output)?;
    ensure!(!output.try_exists()?, "assembly directory must be new");
    let evidence = sibling(&plan_path, ".evidence")?;
    let locks = [sibling(&plan_path, ".lock")?, sibling(&result, ".lock")?];
    let mut plan = artifacts::load_plan(&plan_path, &result, &journal, &evidence, &locks)?;
    validate_mirror(&plan, &result)?;
    let controls = [
        &plan_path, &journal, &result, &evidence, &locks[0], &locks[1],
    ];
    let cache = plan.settings["cache_dir"].as_str().map(PathBuf::from);
    for path in plan
        .protected
        .iter()
        .chain(controls)
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
    let config = configuration(&plan.settings, &args.execution_version);
    let layout = Layout::new(&plan, &config, &evidence)?;
    let mut execution = Execution::from_journal(
        &plan,
        layout,
        config,
        None,
        result,
        evidence,
        state,
        Instant::now(),
        false,
    )?;
    read_bound(&plan_path, &plan.digest, "PLAN_DRIFT")?;
    read_bound(&journal, &digest, "JOURNAL_DRIFT")?;
    fs::create_dir(&output)?;
    execution.result = output.join("assembly.json");
    execution.evidence = output.join("evidence");
    execution.state.result_path = execution.result.clone();
    execution.state.recovery =
        Some(json!({"journal_sha256":digest, "execution_version":args.execution_version}));
    execution.publish()
}
