use crate::{
    config::{Config, Scope, ScoreConfig},
    provider::{self, Connection, FORMAT, Outcome as ProviderOutcome, PROMPT},
    score_context::{self, Packet, ReplyValidation},
    semantic_route,
    summary::{Outcome, Progress, Summary},
    workspace::{self, Workspace, checksum as digest},
};
use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::{self, Write},
    os::fd::AsRawFd,
    path::{Path, PathBuf},
    sync::mpsc,
    thread,
    time::Duration,
};

const WORK: &str = "mdcompare-score-work-1";
const PLAN: &str = "mdcompare-score-plan-1";
const JOURNAL: &str = "mdcompare-score-journal-1";
const RESULT: &str = "mdcompare-score-result-1";
const EXECUTION_LOCK: &str = "execution.lock";
const JOURNAL_LOCK: &str = "admission.lock";

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Identity {
    knowledge_base: String,
    document_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ScoreWork {
    schema_version: String,
    comparison_plan: PathBuf,
    comparison_sha256: String,
    documents: Vec<Identity>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct Binding {
    #[serde(flatten)]
    identity: Identity,
    evidence: String,
    evidence_sha256: String,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Frozen {
    schema_version: String,
    source: PathBuf,
    source_sha256: String,
    comparison_plan: PathBuf,
    comparison_sha256: String,
    documents: Vec<Binding>,
    config: ScoreConfig,
    config_source: Option<PathBuf>,
    prompt_sha256: String,
    format_sha256: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum AttemptState {
    Admitted,
    Scored,
    Unscored,
    Unknown,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Attempt {
    document: usize,
    packet_sha256: String,
    state: AttemptState,
    reason_code: Option<String>,
    score: Option<u8>,
    ambiguous: Option<bool>,
    usage_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    validation: Option<ReplyValidation>,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Journal {
    schema_version: String,
    score_plan_sha256: String,
    attempts: Vec<Attempt>,
    held: BTreeMap<usize, String>,
    concurrency: u8,
    throttled: bool,
    stop: Option<String>,
    cancel_requested: bool,
    started: bool,
    terminal: Option<Terminal>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Terminal {
    Complete,
    Partial,
    Cancelled,
}

impl Terminal {
    fn name(self) -> &'static str {
        match self {
            Self::Complete => "complete",
            Self::Partial => "partial",
            Self::Cancelled => "cancelled",
        }
    }

    fn exit(self) -> u8 {
        match self {
            Self::Complete => 0,
            Self::Partial => 2,
            Self::Cancelled => 3,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScoreRow {
    pub knowledge_base: String,
    pub document_id: String,
    pub state: String,
    pub reason_code: Option<String>,
    pub admission: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub evidence: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub evidence_sha256: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub score: Option<u8>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ambiguous: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScoreResult {
    pub schema_version: String,
    pub state: String,
    pub comparison_sha256: String,
    pub score_plan_sha256: String,
    pub journal_sha256: String,
    pub rows: Vec<ScoreRow>,
}

struct Task {
    workspace: Workspace,
    frozen: Frozen,
    plan_sha256: String,
}

fn is_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

fn bytes(value: &impl Serialize) -> Result<Vec<u8>> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    Ok(bytes)
}

fn read<T: DeserializeOwned>(path: &Path) -> Result<(T, String)> {
    let (bytes, _) = crate::input::read(path, u64::MAX)
        .map_err(anyhow::Error::msg)
        .context("binding_invalid")?;
    let parsed = workspace::parse_json(&bytes)?;
    Ok((serde_json::from_value(parsed)?, digest(&bytes)))
}

fn comparison(comparison: &Path, sha: &str) -> Result<(PathBuf, BTreeMap<Identity, Binding>)> {
    let workspace = Workspace::locate(comparison).context("binding_invalid")?;
    let generation = semantic_route::load(&workspace)?;
    ensure!(generation.comparison_sha256 == sha, "binding_invalid");
    let bindings = generation
        .results
        .iter()
        .filter(|row| semantic_route::eligible(row))
        .map(|row| {
            let (knowledge_base, document_id) = semantic_route::identity(row)?;
            let raw = generation
                .bindings
                .get(&(knowledge_base.clone(), document_id.clone()))
                .context("binding_invalid")?;
            let identity = Identity {
                knowledge_base,
                document_id,
            };
            Ok((
                identity.clone(),
                Binding {
                    identity,
                    evidence: raw.evidence.clone(),
                    evidence_sha256: raw.digest.clone(),
                },
            ))
        })
        .collect::<Result<_>>()?;
    Ok((generation.directory, bindings))
}

fn work(workspace: &Workspace) -> Result<ScoreWork> {
    let (work, sha): (ScoreWork, _) = read(&workspace.source).context("invalid_input")?;
    ensure!(
        sha == workspace.source_sha256
            && work.schema_version == WORK
            && is_digest(&work.comparison_sha256)
            && work
                .documents
                .iter()
                .all(|doc| !doc.knowledge_base.is_empty() && !doc.document_id.is_empty())
            && work.documents.iter().collect::<BTreeSet<_>>().len() == work.documents.len(),
        "invalid_input"
    );
    Ok(work)
}

fn settings(config: &ScoreConfig) -> Connection {
    Connection {
        oauth_file: config.oauth_file.clone(),
        proxy: config.proxy.clone(),
        ca_file: config.ca_file.clone(),
        endpoint: config.endpoint.clone(),
    }
}

fn score_config(workspace: &Workspace, explicit: Option<&Path>) -> Result<Config> {
    let config =
        Config::load(Some(&workspace.source), explicit, Scope::Score).context("invalid_config")?;
    ensure!(config.score.request_budget.is_some(), "invalid_config");
    Ok(config)
}

fn seal_path(workspace: &Workspace) -> PathBuf {
    workspace.source.with_extension("mdcompare.score-seal")
}

fn exists(path: &Path) -> Result<bool> {
    match path.symlink_metadata() {
        Ok(_) => Ok(true),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error).context("binding_invalid"),
    }
}

fn protect_score_destinations(
    workspace: &Workspace,
    comparison: &Workspace,
    config: &Config,
) -> Result<()> {
    let plan = workspace::load_frozen(comparison)?;
    let protected = || {
        plan.inputs
            .keys()
            .map(PathBuf::as_path)
            .chain([
                workspace.source.as_path(),
                comparison.root.as_path(),
                comparison.source.as_path(),
            ])
            .chain(config.source.as_deref())
            .chain(config.score.oauth_file.as_deref())
            .chain(config.score.ca_file.as_deref())
    };
    crate::input::check_output(&workspace.root, protected()).context("binding_invalid")?;
    crate::input::check_output(&seal_path(workspace), protected()).context("binding_invalid")
}

fn ensure_seal(task: &Task, path: &Path) -> Result<()> {
    let parent = path.parent().context("binding_invalid")?;
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .context("binding_invalid")?;
    let path = workspace::artifact_path(parent, name)?;
    let seal = format!("{}\n", task.plan_sha256);
    if exists(&path)? {
        let (existing, _) = crate::input::read(&path, 65)
            .map_err(anyhow::Error::msg)
            .context("binding_invalid")?;
        ensure!(existing == seal.as_bytes(), "binding_invalid");
    } else {
        // Adoption is only safe for an accepted workspace that never began
        // execution. A spent journal without its ownership seal is damaged.
        let (journal, _) = task.journal()?;
        ensure!(
            !journal.started && journal.attempts.is_empty(),
            "binding_invalid"
        );
        let mut temp = tempfile::NamedTempFile::new_in(parent).context("publish_failed")?;
        temp.write_all(seal.as_bytes()).context("publish_failed")?;
        temp.as_file().sync_all().context("publish_failed")?;
        temp.persist_noclobber(&path).context("publish_failed")?;
    }
    File::open(parent)?.sync_all().context("publish_failed")?;
    Ok(())
}

fn prepare_task(workspace: Workspace, explicit: Option<&Path>) -> Result<Task> {
    let _creation = workspace.creation_lock()?;
    let seal = seal_path(&workspace);
    if !exists(&workspace.root)? {
        ensure!(!exists(&seal)?, "binding_invalid");
        let work = work(&workspace)?;
        let pair = workspace
            .source
            .parent()
            .context("invalid_input")?
            .join(work.comparison_plan);
        let pair = pair.canonicalize().context("binding_invalid")?;
        let (_, eligible) = comparison(&pair, &work.comparison_sha256)?;
        let documents = work
            .documents
            .iter()
            .map(|id| eligible.get(id).cloned().context("invalid_input"))
            .collect::<Result<_>>()?;
        let config = score_config(&workspace, explicit)?;
        let pair_workspace = Workspace::locate(&pair)?;
        protect_score_destinations(&workspace, &pair_workspace, &config)?;
        let frozen = Frozen {
            schema_version: PLAN.to_owned(),
            source: workspace.source.clone(),
            source_sha256: workspace.source_sha256.clone(),
            comparison_plan: pair,
            comparison_sha256: work.comparison_sha256,
            documents,
            config: config.score,
            config_source: config.source,
            prompt_sha256: digest(PROMPT.as_bytes()),
            format_sha256: digest(FORMAT.as_bytes()),
        };
        let plan = bytes(&frozen)?;
        let journal = Journal::new(digest(&plan), frozen.config.concurrency);
        workspace.initialize(&[
            ("plan.json", plan),
            ("journal.json", bytes(&journal)?),
            (EXECUTION_LOCK, Vec::new()),
            (JOURNAL_LOCK, Vec::new()),
        ])?;
    }
    let task = Task::open(workspace)?;
    ensure_seal(&task, &seal)?;
    if explicit.is_some() {
        ensure!(
            score_config(&task.workspace, explicit)?.score == task.frozen.config,
            "invalid_config"
        );
    }
    ensure!(
        task.frozen.prompt_sha256 == digest(PROMPT.as_bytes())
            && task.frozen.format_sha256 == digest(FORMAT.as_bytes()),
        "binding_invalid"
    );
    Ok(task)
}

fn frozen(root: &Path) -> Result<(Frozen, String)> {
    let file = workspace::artifact_path(root, "plan.json")?;
    let (frozen, sha): (Frozen, _) = read(&file).context("binding_invalid")?;
    ensure!(
        frozen.schema_version == PLAN
            && is_digest(&frozen.comparison_sha256)
            && (1..=3).contains(&frozen.config.concurrency)
            && frozen.config.request_budget.is_some()
            && frozen
                .documents
                .iter()
                .map(|item| &item.identity)
                .collect::<BTreeSet<_>>()
                .len()
                == frozen.documents.len(),
        "binding_invalid"
    );
    Ok((frozen, sha))
}

fn load_journal(root: &Path, frozen: &Frozen, plan_sha: &str) -> Result<(Journal, String)> {
    let file = workspace::artifact_path(root, "journal.json").context("binding_invalid")?;
    let (journal, sha): (Journal, _) = read(&file).context("binding_invalid")?;
    journal.verify(frozen, plan_sha)?;
    Ok((journal, sha))
}

impl Task {
    fn open(workspace: Workspace) -> Result<Self> {
        ensure!(workspace.root.try_exists()?, "workspace_missing");
        let (frozen, plan_sha256) = frozen(&workspace.root)?;
        ensure!(
            frozen.source == workspace.source && frozen.source_sha256 == workspace.source_sha256,
            "binding_invalid"
        );
        Ok(Self {
            workspace,
            frozen,
            plan_sha256,
        })
    }

    fn journal(&self) -> Result<(Journal, String)> {
        load_journal(&self.workspace.root, &self.frozen, &self.plan_sha256)
    }

    fn save_journal(&self, journal: &Journal) -> Result<String> {
        let data = bytes(journal)?;
        workspace::atomic_write(&self.workspace.root.join("journal.json"), &data)
            .context("publish_failed")?;
        Ok(digest(&data))
    }

    fn comparison(&self) -> Result<PathBuf> {
        let (directory, bindings) =
            comparison(&self.frozen.comparison_plan, &self.frozen.comparison_sha256)?;
        ensure!(
            self.frozen
                .documents
                .iter()
                .all(|binding| bindings.get(&binding.identity) == Some(binding)),
            "binding_invalid"
        );
        Ok(directory)
    }

    fn protect_oauth_file(&self) -> Result<()> {
        let Some(oauth) = self.frozen.config.oauth_file.as_deref() else {
            return Ok(());
        };
        if self.frozen.config.endpoint.is_some() {
            return Ok(());
        }
        let pair = Workspace::locate(&self.frozen.comparison_plan)?;
        let pair_plan = workspace::load_frozen(&pair)?;
        let seal = seal_path(&self.workspace);
        crate::input::check_output(
            oauth,
            pair_plan
                .inputs
                .keys()
                .map(PathBuf::as_path)
                .chain([
                    pair.root.as_path(),
                    pair.source.as_path(),
                    self.workspace.root.as_path(),
                    self.workspace.source.as_path(),
                    seal.as_path(),
                ])
                .chain(self.frozen.config_source.as_deref())
                .chain(self.frozen.config.ca_file.as_deref()),
        )
        .context("binding_invalid")
    }

    fn result(&self, journal: &Journal, journal_sha256: String) -> Result<ScoreResult> {
        journal.result(&self.frozen, &self.plan_sha256, journal_sha256)
    }

    fn summary(
        &self,
        command: &'static str,
        state: &str,
        journal: &Journal,
        running: bool,
        artifact: bool,
    ) -> Summary {
        let mut summary = Summary::new(command, state)
            .comparison(self.frozen.comparison_sha256.clone())
            .artifact("plan", self.workspace.root.join("plan.json"))
            .artifact("journal", self.workspace.root.join("journal.json"))
            .count("selected", self.frozen.documents.len())
            .count("admitted", journal.attempts.len())
            .count("scored", journal.scored())
            .count("in_flight", if running { journal.pending() } else { 0 });
        if let Some(reason) = match state {
            "cancel_requested" | "cancelled" => Some("cancelled"),
            "partial" => journal.stop.as_deref(),
            _ => None,
        } {
            summary = summary.reason(reason);
        }
        if artifact {
            summary.artifact("result", self.workspace.root.join("result.json"))
        } else {
            summary
        }
    }
}

impl Journal {
    fn new(score_plan_sha256: String, concurrency: u8) -> Self {
        Self {
            schema_version: JOURNAL.to_owned(),
            score_plan_sha256,
            attempts: Vec::new(),
            held: BTreeMap::new(),
            concurrency,
            throttled: false,
            stop: None,
            cancel_requested: false,
            started: false,
            terminal: None,
        }
    }

    fn verify(&self, frozen: &Frozen, sha: &str) -> Result<()> {
        ensure!(
            self.schema_version == JOURNAL
                && self.score_plan_sha256 == sha
                && self.attempts.len()
                    <= frozen.config.request_budget.context("binding_invalid")?
                && (1..=frozen.config.concurrency).contains(&self.concurrency),
            "binding_invalid"
        );
        let mut used = BTreeSet::new();
        for attempt in &self.attempts {
            ensure!(
                attempt.document < frozen.documents.len()
                    && used.insert(attempt.document)
                    && is_digest(&attempt.packet_sha256)
                    && attempt.validation.is_none_or(|validation| {
                        attempt.state == AttemptState::Unscored
                            && attempt.reason_code.as_deref() == Some(validation.reason_code())
                            && validation != ReplyValidation::InsufficientInput
                    })
                    && match attempt.state {
                        AttemptState::Admitted =>
                            self.terminal.is_none()
                                && attempt.score.is_none()
                                && attempt.ambiguous.is_none()
                                && attempt.reason_code.is_none(),
                        AttemptState::Scored =>
                            attempt.score.is_some_and(|score| (1..=5).contains(&score))
                                && attempt.ambiguous.is_some()
                                && attempt.reason_code.is_none(),
                        AttemptState::Unscored | AttemptState::Unknown =>
                            attempt.score.is_none()
                                && attempt.ambiguous.is_none()
                                && attempt
                                    .reason_code
                                    .as_ref()
                                    .is_some_and(|reason| !reason.is_empty()),
                    },
                "binding_invalid"
            );
        }
        for (&document, reason) in &self.held {
            ensure!(
                document < frozen.documents.len() && used.insert(document) && !reason.is_empty(),
                "binding_invalid"
            );
        }
        match self.terminal {
            Some(Terminal::Complete) => {
                ensure!(self.scored() == frozen.documents.len(), "binding_invalid")
            }
            Some(Terminal::Partial) => ensure!(
                self.scored() < frozen.documents.len()
                    && !self.cancel_requested
                    && (used.len() == frozen.documents.len()
                        || self.stop.is_some()
                        || self.attempts.len() == frozen.config.request_budget.unwrap_or_default()),
                "binding_invalid"
            ),
            Some(Terminal::Cancelled) => ensure!(
                self.cancel_requested && used.len() < frozen.documents.len(),
                "binding_invalid"
            ),
            None => {}
        }
        Ok(())
    }

    fn scored(&self) -> usize {
        self.attempts
            .iter()
            .filter(|item| item.state == AttemptState::Scored)
            .count()
    }

    fn pending(&self) -> usize {
        self.attempts
            .iter()
            .filter(|item| item.state == AttemptState::Admitted)
            .count()
    }

    fn result(
        &self,
        frozen: &Frozen,
        plan_sha: &str,
        journal_sha256: String,
    ) -> Result<ScoreResult> {
        let terminal = self.terminal.context("binding_invalid")?;
        let attempts: BTreeMap<_, _> = self
            .attempts
            .iter()
            .enumerate()
            .map(|(n, row)| (row.document, (n + 1, row)))
            .collect();
        let rows = frozen
            .documents
            .iter()
            .enumerate()
            .map(|(index, binding)| {
                let mut row = ScoreRow {
                    knowledge_base: binding.identity.knowledge_base.clone(),
                    document_id: binding.identity.document_id.clone(),
                    state: "not_sent".to_owned(),
                    reason_code: None,
                    admission: None,
                    evidence: None,
                    evidence_sha256: None,
                    score: None,
                    ambiguous: None,
                };
                if let Some(&(sequence, attempt)) = attempts.get(&index) {
                    row.admission = Some(sequence);
                    row.reason_code = attempt.reason_code.clone();
                    row.state = match attempt.state {
                        AttemptState::Scored => {
                            row.score = attempt.score;
                            row.ambiguous = attempt.ambiguous;
                            row.evidence = Some(binding.evidence.clone());
                            row.evidence_sha256 = Some(binding.evidence_sha256.clone());
                            "scored"
                        }
                        AttemptState::Unscored => "unscored",
                        AttemptState::Unknown => "unknown",
                        AttemptState::Admitted => bail!("binding_invalid"),
                    }
                    .to_owned();
                } else {
                    row.reason_code = Some(
                        self.held
                            .get(&index)
                            .cloned()
                            .or_else(|| {
                                (terminal == Terminal::Cancelled).then(|| "cancelled".to_owned())
                            })
                            .or_else(|| self.stop.clone())
                            .unwrap_or_else(|| "request_budget_exhausted".to_owned()),
                    );
                }
                Ok(row)
            })
            .collect::<Result<_>>()?;
        Ok(ScoreResult {
            schema_version: RESULT.to_owned(),
            state: terminal.name().to_owned(),
            comparison_sha256: frozen.comparison_sha256.clone(),
            score_plan_sha256: plan_sha.to_owned(),
            journal_sha256,
            rows,
        })
    }
}

fn open_lock(root: &Path, name: &str, blocking: bool) -> Result<Option<File>> {
    let file = File::open(workspace::artifact_path(root, name)?).context("binding_invalid")?;
    let mode = libc::LOCK_EX | if blocking { 0 } else { libc::LOCK_NB };
    loop {
        if unsafe { libc::flock(file.as_raw_fd(), mode) } == 0 {
            return Ok(Some(file));
        }
        let error = io::Error::last_os_error();
        match error.kind() {
            io::ErrorKind::WouldBlock if !blocking => return Ok(None),
            io::ErrorKind::Interrupted => {}
            _ => return Err(error.into()),
        }
    }
}

fn journal_lock(root: &Path) -> Result<File> {
    open_lock(root, JOURNAL_LOCK, true)?.context("workspace_busy")
}

fn load_result(
    root: &Path,
    expected: Option<ScoreResult>,
) -> Result<Option<(ScoreResult, String)>> {
    let path = root.join("result.json");
    if path
        .symlink_metadata()
        .is_err_and(|err| err.kind() == io::ErrorKind::NotFound)
    {
        return Ok(None);
    }
    let path = workspace::artifact_path(root, "result.json")?;
    let (raw, _) = crate::input::read(&path, u64::MAX)
        .map_err(anyhow::Error::msg)
        .context("binding_invalid")?;
    let parsed = workspace::parse_json(&raw).context("binding_invalid")?;
    let expected = expected.context("binding_invalid")?;
    ensure!(
        parsed == serde_json::to_value(&expected)?,
        "binding_invalid"
    );
    Ok(Some((expected, digest(&raw))))
}

pub fn verified_result(path: &Path) -> Result<(ScoreResult, String)> {
    ensure!(
        path.file_name().and_then(|part| part.to_str()) == Some("result.json"),
        "binding_invalid"
    );
    let root = path
        .parent()
        .filter(|path| !path.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let _lock = journal_lock(root)?;
    let (frozen, plan_sha) = frozen(root)?;
    let (journal, journal_sha) = load_journal(root, &frozen, &plan_sha)?;
    ensure!(
        matches!(
            journal.terminal,
            Some(Terminal::Complete | Terminal::Partial)
        ),
        "binding_invalid"
    );
    let expected = journal.result(&frozen, &plan_sha, journal_sha)?;
    load_result(root, Some(expected))?.context("binding_invalid")
}

fn observe(plan: &Path, cancel: bool) -> Result<Outcome> {
    let task = Task::open(Workspace::locate(plan)?)?;
    let _state = journal_lock(&task.workspace.root)?;
    let running = open_lock(&task.workspace.root, EXECUTION_LOCK, false)?.is_none();
    let (mut journal, sha) = task.journal()?;
    let expected = journal
        .terminal
        .map(|_| task.result(&journal, sha))
        .transpose()?;
    let published = load_result(&task.workspace.root, expected)?.is_some();
    if cancel && journal.terminal.is_none() && !journal.cancel_requested {
        journal.cancel_requested = true;
        task.save_journal(&journal)?;
    }
    let state = if published {
        journal.terminal.context("binding_invalid")?.name()
    } else if journal.terminal == Some(Terminal::Cancelled) {
        "cancelled"
    } else if journal.cancel_requested {
        "cancel_requested"
    } else if running {
        "running"
    } else {
        "accepted"
    };
    let command = if cancel { "cancel" } else { "status" };
    let mut summary = task.summary(command, state, &journal, running, published);
    if state == "accepted" && !running && journal.started {
        summary = summary.reason("interrupted");
    }
    Ok(summary.exit(0))
}

pub fn cancel(plan: &Path) -> Result<Outcome> {
    observe(plan, true)
}

pub fn status(plan: &Path) -> Result<Outcome> {
    observe(plan, false)
}

fn prepare_packet(
    directory: &Path,
    binding: &Binding,
    limit: u64,
) -> Result<Result<Packet, String>> {
    let file = workspace::artifact_path(directory, &binding.evidence)?;
    let (data, _) = crate::input::read(&file, u64::MAX)
        .map_err(anyhow::Error::msg)
        .context("binding_invalid")?;
    ensure!(digest(&data) == binding.evidence_sha256, "binding_invalid");
    Ok(score_context::prepare(&data, limit)?.map_err(str::to_ascii_lowercase))
}

fn settle(task: &Task, index: usize, operations: usize, outcome: ProviderOutcome) -> Result<()> {
    let _lock = journal_lock(&task.workspace.root)?;
    let (mut journal, _) = task.journal()?;
    let attempt = journal.attempts.get_mut(index).context("binding_invalid")?;
    ensure!(attempt.state == AttemptState::Admitted, "binding_invalid");
    attempt.usage_tokens = outcome.usage;
    let result = if let Some(reply) = outcome.reply {
        score_context::validate_reply(&reply, operations).map_err(|validation| {
            let reason = validation.reason_code();
            if reason == "invalid_response" {
                attempt.validation = Some(validation);
            }
            reason
        })
    } else {
        Err(outcome.reason.unwrap_or("invalid_response"))
    };
    match result {
        Ok((score, ambiguous)) => {
            attempt.state = AttemptState::Scored;
            attempt.score = Some(score);
            attempt.ambiguous = Some(ambiguous);
        }
        Err(reason) => {
            attempt.state = if reason == "unknown_outcome" {
                AttemptState::Unknown
            } else {
                AttemptState::Unscored
            };
            attempt.reason_code = Some(reason.to_ascii_lowercase());
            if outcome.http_status == Some(429) {
                journal.throttled = true;
            } else if matches!(
                reason,
                "unknown_outcome"
                    | "auth_error"
                    | "oauth_required"
                    | "unsupported_transport"
                    | "proxy_unavailable"
                    | "ca_unavailable"
            ) {
                journal.stop.get_or_insert(reason.to_owned());
            }
        }
    }
    task.save_journal(&journal)?;
    Ok(())
}

fn run_pending(task: &Task, directory: &Path, progress: &Progress) -> Result<()> {
    let config = &task.frozen.config;
    let budget = config.request_budget.context("invalid_config")?;
    let connection = settings(config);
    let (sent, finished) = mpsc::channel();
    let (initial, _) = task.journal()?;
    let attempted: BTreeSet<_> = initial
        .attempts
        .iter()
        .map(|attempt| attempt.document)
        .collect();
    let mut remaining = task
        .frozen
        .documents
        .iter()
        .enumerate()
        .filter(|(document, _)| {
            !attempted.contains(document) && !initial.held.contains_key(document)
        })
        .peekable();
    let mut packet: Option<(usize, Packet)> = None;
    let mut checked = false;
    let mut progress_counts = None;
    thread::scope(|scope| -> Result<()> {
        loop {
            while let Ok((sequence, operations, outcome)) = finished.try_recv() {
                settle(task, sequence, operations, outcome)?;
            }
            {
                let _lock = journal_lock(&task.workspace.root)?;
                let (mut journal, _) = task.journal()?;
                let pending = journal.pending();
                if journal.throttled && pending == 0 {
                    journal.throttled = false;
                    if journal.concurrency == 1 {
                        journal.stop.get_or_insert("rate_limited".to_owned());
                    } else {
                        journal.concurrency -= 1;
                    }
                    task.save_journal(&journal)?;
                }
                let counts = (journal.attempts.len(), pending, journal.held.len());
                if progress_counts != Some(counts) {
                    progress_counts = Some(counts);
                    drop(_lock);
                    let (admitted, pending, held) = counts;
                    progress.update(
                        "scoring",
                        admitted - pending + held,
                        task.frozen.documents.len(),
                        admitted,
                        pending,
                    );
                    continue;
                }
                if journal.stop.is_some()
                    || journal.cancel_requested
                    || journal.attempts.len() >= budget
                    || (remaining.peek().is_none() && packet.is_none())
                {
                    if pending == 0 {
                        break;
                    }
                } else if !journal.throttled && pending < usize::from(journal.concurrency) {
                    if let Some((document, prepared)) = packet.take() {
                        let Packet {
                            evidence,
                            operations,
                        } = prepared;
                        let sequence = journal.attempts.len();
                        journal.attempts.push(Attempt {
                            document,
                            packet_sha256: digest(evidence.as_bytes()),
                            state: AttemptState::Admitted,
                            reason_code: None,
                            score: None,
                            ambiguous: None,
                            usage_tokens: None,
                            validation: None,
                        });
                        task.save_journal(&journal)?;
                        let sent = sent.clone();
                        let connection = &connection;
                        scope.spawn(move || {
                            let outcome = provider::assess_with_connection(
                                &config.model,
                                &config.effort,
                                &evidence,
                                connection,
                            );
                            let _ = sent.send((sequence, operations, outcome));
                        });
                        continue;
                    }
                    drop(_lock);
                    let (document, binding) = remaining.next().context("binding_invalid")?;
                    match prepare_packet(directory, binding, config.max_evidence_bytes)? {
                        Ok(prepared) => {
                            // Only load scoring prerequisites for a sendable packet.
                            if !checked {
                                task.protect_oauth_file()?;
                                provider::check_oauth_settings(
                                    &config.model,
                                    &config.effort,
                                    &connection,
                                )
                                .map_err(|reason| anyhow::anyhow!("invalid_config: {reason}"))?;
                                checked = true;
                            }
                            packet = Some((document, prepared));
                        }
                        Err(reason) => {
                            let _lock = journal_lock(&task.workspace.root)?;
                            let (mut journal, _) = task.journal()?;
                            if !journal.cancel_requested {
                                journal.held.insert(document, reason);
                                task.save_journal(&journal)?;
                            }
                        }
                    }
                    continue;
                }
            }
            if let Ok((sequence, operations, outcome)) =
                finished.recv_timeout(Duration::from_millis(200))
            {
                settle(task, sequence, operations, outcome)?;
            }
        }
        Ok(())
    })
}

pub fn run(
    plan: &Path,
    config: Option<&Path>,
    resume: bool,
    progress: &Progress,
) -> Result<Outcome> {
    let task = prepare_task(Workspace::locate(plan)?, config)?;
    let directory = task.comparison()?;
    let _execution = {
        let _lock = journal_lock(&task.workspace.root)?;
        // Claim execution and clear the previous cancellation together.
        // Cancel cannot observe this run before the transition is durable.
        let execution =
            open_lock(&task.workspace.root, EXECUTION_LOCK, false)?.context("workspace_busy")?;
        let (mut journal, sha) = task.journal()?;
        if let Some(terminal) = journal.terminal {
            let expected = task.result(&journal, sha)?;
            let published = load_result(&task.workspace.root, Some(expected.clone()))?.is_some();
            if terminal != Terminal::Cancelled || !resume {
                if !published {
                    workspace::atomic_write(
                        &task.workspace.root.join("result.json"),
                        &bytes(&expected)?,
                    )?;
                }
                return Ok(task
                    .summary("score", terminal.name(), &journal, false, true)
                    .exit(terminal.exit()));
            }
            if published {
                fs::remove_file(task.workspace.root.join("result.json"))?;
                File::open(&task.workspace.root)?.sync_all()?;
            }
            journal.terminal = None;
        } else {
            ensure!(
                load_result(&task.workspace.root, None)?.is_none(),
                "binding_invalid"
            );
        }
        if resume {
            journal.cancel_requested = false;
        }
        journal.started = true;
        for previous in &mut journal.attempts {
            if previous.state == AttemptState::Admitted {
                previous.state = AttemptState::Unknown;
                previous.reason_code = Some("interrupted".to_owned());
            }
        }
        task.save_journal(&journal)?;
        execution
    };
    run_pending(&task, &directory, progress)?;
    let _lock = journal_lock(&task.workspace.root)?;
    let (mut journal, _) = task.journal()?;
    let selected = task.frozen.documents.len();
    let terminal = if journal.scored() == selected {
        journal.cancel_requested = false;
        Terminal::Complete
    } else if journal.cancel_requested && journal.attempts.len() + journal.held.len() < selected {
        Terminal::Cancelled
    } else {
        journal.cancel_requested = false;
        Terminal::Partial
    };
    journal.terminal = Some(terminal);
    let sha = task.save_journal(&journal)?;
    let result = task.result(&journal, sha)?;
    workspace::atomic_write(&task.workspace.root.join("result.json"), &bytes(&result)?)?;
    Ok(task
        .summary("score", terminal.name(), &journal, false, true)
        .exit(terminal.exit()))
}
