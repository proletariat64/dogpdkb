use super::{Frozen, ScoreResult, ScoreRow, bytes, digest, frozen, is_digest, read};
use crate::{
    provider::Outcome as ProviderOutcome,
    score_context::{self, ReplyValidation},
    summary::{Outcome, Summary},
    workspace::{self, Workspace},
};
use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io,
    os::fd::AsRawFd,
    path::Path,
};

const JOURNAL: &str = "mdcompare-score-journal-1";
const RESULT: &str = "mdcompare-score-result-1";
const EXECUTION_LOCK: &str = "execution.lock";
const JOURNAL_LOCK: &str = "admission.lock";

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

fn load_journal(root: &Path, frozen: &Frozen, plan_sha: &str) -> Result<(Journal, String)> {
    let file = workspace::artifact_path(root, "journal.json").context("binding_invalid")?;
    let (journal, sha): (Journal, _) = read(&file).context("binding_invalid")?;
    journal.verify(frozen, plan_sha)?;
    Ok((journal, sha))
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

pub(super) fn initialize(workspace: &Workspace, plan: Vec<u8>, concurrency: u8) -> Result<()> {
    let journal = Journal::new(digest(&plan), concurrency);
    workspace.initialize(&[
        ("plan.json", plan),
        ("journal.json", bytes(&journal)?),
        (EXECUTION_LOCK, Vec::new()),
        (JOURNAL_LOCK, Vec::new()),
    ])
}

/// A journal is always interpreted against its frozen selection and plan digest.
pub(super) struct Store<'a> {
    root: &'a Path,
    frozen: &'a Frozen,
    plan_sha: &'a str,
}

impl<'a> Store<'a> {
    pub(super) fn new(root: &'a Path, frozen: &'a Frozen, plan_sha: &'a str) -> Self {
        Self {
            root,
            frozen,
            plan_sha,
        }
    }

    pub(super) fn unstarted(&self) -> Result<bool> {
        let (journal, _) = load_journal(self.root, self.frozen, self.plan_sha)?;
        Ok(!journal.started && journal.attempts.is_empty())
    }

    pub(super) fn known_documents(&self) -> Result<BTreeSet<usize>> {
        self.frozen
            .config
            .request_budget
            .context("invalid_config")?;
        let (journal, _) = load_journal(self.root, self.frozen, self.plan_sha)?;
        Ok(journal
            .attempts
            .iter()
            .map(|attempt| attempt.document)
            .chain(journal.held.keys().copied())
            .collect())
    }

    pub(super) fn lock(self) -> Result<LockedStore<'a>> {
        let guard = journal_lock(self.root)?;
        Ok(LockedStore {
            store: self,
            _guard: guard,
        })
    }
}

/// Lock acquisition precedes loading: claim/observe execution while cancel and
/// admission are serialized, maintaining the existing error and lock ordering.
pub(super) struct LockedStore<'a> {
    store: Store<'a>,
    _guard: File,
}

impl<'a> LockedStore<'a> {
    pub(super) fn claim_execution(&self) -> Result<File> {
        open_lock(self.store.root, EXECUTION_LOCK, false)?.context("workspace_busy")
    }

    pub(super) fn running(&self) -> Result<bool> {
        Ok(open_lock(self.store.root, EXECUTION_LOCK, false)?.is_none())
    }

    pub(super) fn load(self) -> Result<LockedJournal<'a>> {
        let (journal, sha) = load_journal(self.store.root, self.store.frozen, self.store.plan_sha)?;
        Ok(LockedJournal {
            locked: self,
            journal,
            sha,
        })
    }
}

pub(super) enum Next {
    Done,
    Wait,
    Prepare,
    Admitted(usize),
}

/// All mutating transitions both hold the admission lock and persist their
/// journal before returning. Dispatch can only follow a durable admission.
pub(super) struct LockedJournal<'a> {
    locked: LockedStore<'a>,
    journal: Journal,
    sha: String,
}

impl LockedJournal<'_> {
    fn root(&self) -> &Path {
        self.locked.store.root
    }

    fn frozen(&self) -> &Frozen {
        self.locked.store.frozen
    }

    fn save(&mut self) -> Result<()> {
        let data = bytes(&self.journal)?;
        workspace::atomic_write(&self.root().join("journal.json"), &data)
            .context("publish_failed")?;
        self.sha = digest(&data);
        Ok(())
    }

    fn result(&self) -> Result<ScoreResult> {
        self.journal
            .result(self.frozen(), self.locked.store.plan_sha, self.sha.clone())
    }

    fn summary(
        &self,
        command: &'static str,
        state: &str,
        running: bool,
        artifact: bool,
    ) -> Summary {
        let mut summary = Summary::new(command, state)
            .comparison(self.frozen().comparison_sha256.clone())
            .artifact("plan", self.root().join("plan.json"))
            .artifact("journal", self.root().join("journal.json"))
            .count("selected", self.frozen().documents.len())
            .count("admitted", self.journal.attempts.len())
            .count("scored", self.journal.scored())
            .count(
                "in_flight",
                if running { self.journal.pending() } else { 0 },
            );
        if let Some(reason) = match state {
            "cancel_requested" | "cancelled" => Some("cancelled"),
            "partial" => self.journal.stop.as_deref(),
            _ => None,
        } {
            summary = summary.reason(reason);
        }
        if artifact {
            summary.artifact("result", self.root().join("result.json"))
        } else {
            summary
        }
    }

    pub(super) fn observe(&mut self, cancel: bool, running: bool) -> Result<Outcome> {
        let expected = self.journal.terminal.map(|_| self.result()).transpose()?;
        let published = load_result(self.root(), expected)?.is_some();
        if cancel && self.journal.terminal.is_none() && !self.journal.cancel_requested {
            self.journal.cancel_requested = true;
            self.save()?;
        }
        let state = if published {
            self.journal.terminal.context("binding_invalid")?.name()
        } else if self.journal.terminal == Some(Terminal::Cancelled) {
            "cancelled"
        } else if self.journal.cancel_requested {
            "cancel_requested"
        } else if running {
            "running"
        } else {
            "accepted"
        };
        let command = if cancel { "cancel" } else { "status" };
        let mut summary = self.summary(command, state, running, published);
        if state == "accepted" && !running && self.journal.started {
            summary = summary.reason("interrupted");
        }
        Ok(summary.exit(0))
    }

    pub(super) fn start(&mut self, resume: bool) -> Result<Option<Outcome>> {
        if let Some(terminal) = self.journal.terminal {
            if terminal != Terminal::Cancelled || !resume {
                return self.finish_settled().map(Some);
            }
            let expected = self.result()?;
            if load_result(self.root(), Some(expected))?.is_some() {
                fs::remove_file(self.root().join("result.json"))?;
                File::open(self.root())?.sync_all()?;
            }
            self.journal.terminal = None;
        } else {
            ensure!(load_result(self.root(), None)?.is_none(), "binding_invalid");
        }
        if resume {
            self.journal.cancel_requested = false;
        }
        self.journal.started = true;
        for previous in &mut self.journal.attempts {
            if previous.state == AttemptState::Admitted {
                previous.state = AttemptState::Unknown;
                previous.reason_code = Some("interrupted".to_owned());
            }
        }
        self.save()?;
        Ok(None)
    }

    pub(super) fn counts(&self) -> (usize, usize, usize) {
        (
            self.journal.attempts.len(),
            self.journal.pending(),
            self.journal.held.len(),
        )
    }

    pub(super) fn drain_throttle(&mut self) -> Result<()> {
        if self.journal.throttled && self.journal.pending() == 0 {
            self.journal.throttled = false;
            if self.journal.concurrency == 1 {
                self.journal.stop.get_or_insert("rate_limited".to_owned());
            } else {
                self.journal.concurrency -= 1;
            }
            self.save()?;
        }
        Ok(())
    }

    pub(super) fn next(&mut self, has_work: bool, packet: Option<(usize, &str)>) -> Result<Next> {
        let budget = self
            .frozen()
            .config
            .request_budget
            .context("invalid_config")?;
        let pending = self.journal.pending();
        if self.journal.stop.is_some()
            || self.journal.cancel_requested
            || self.journal.attempts.len() >= budget
            || !has_work
        {
            return Ok(if pending == 0 { Next::Done } else { Next::Wait });
        }
        if self.journal.throttled || pending >= usize::from(self.journal.concurrency) {
            return Ok(Next::Wait);
        }
        let Some((document, evidence)) = packet else {
            return Ok(Next::Prepare);
        };
        let sequence = self.journal.attempts.len();
        self.journal.attempts.push(Attempt {
            document,
            packet_sha256: digest(evidence.as_bytes()),
            state: AttemptState::Admitted,
            reason_code: None,
            score: None,
            ambiguous: None,
            usage_tokens: None,
            validation: None,
        });
        self.save()?;
        Ok(Next::Admitted(sequence))
    }

    pub(super) fn hold(&mut self, document: usize, reason: String) -> Result<()> {
        if !self.journal.cancel_requested {
            self.journal.held.insert(document, reason);
            self.save()?;
        }
        Ok(())
    }

    pub(super) fn settle(
        &mut self,
        index: usize,
        operations: usize,
        outcome: ProviderOutcome,
    ) -> Result<()> {
        let journal = &mut self.journal;
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
        self.save()?;
        Ok(())
    }

    fn finish_settled(&self) -> Result<Outcome> {
        let terminal = self.journal.terminal.context("binding_invalid")?;
        let expected = self.result()?;
        if load_result(self.root(), Some(expected.clone()))?.is_none() {
            workspace::atomic_write(&self.root().join("result.json"), &bytes(&expected)?)?;
        }
        Ok(self
            .summary("score", terminal.name(), false, true)
            .exit(terminal.exit()))
    }

    pub(super) fn recover(&mut self) -> Result<Outcome> {
        ensure!(
            self.journal.started && !self.journal.cancel_requested && self.journal.pending() == 0,
            "invalid_input"
        );
        if self.journal.terminal.is_none() {
            let selected = self.frozen().documents.len();
            let budget = self
                .frozen()
                .config
                .request_budget
                .context("binding_invalid")?;
            ensure!(
                self.journal.attempts.len() + self.journal.held.len() == selected
                    || self.journal.attempts.len() == budget
                    || self.journal.stop.is_some(),
                "invalid_input"
            );
            // An existing result with a nonterminal journal is contradictory.
            ensure!(load_result(self.root(), None)?.is_none(), "binding_invalid");
            self.journal.terminal = Some(if self.journal.scored() == selected {
                Terminal::Complete
            } else {
                Terminal::Partial
            });
            self.save()?;
        }
        ensure!(
            matches!(
                self.journal.terminal,
                Some(Terminal::Complete | Terminal::Partial)
            ),
            "invalid_input"
        );
        self.finish_settled()
    }

    pub(super) fn finish(&mut self) -> Result<Outcome> {
        let selected = self.frozen().documents.len();
        let terminal = if self.journal.scored() == selected {
            self.journal.cancel_requested = false;
            Terminal::Complete
        } else if self.journal.cancel_requested
            && self.journal.attempts.len() + self.journal.held.len() < selected
        {
            Terminal::Cancelled
        } else {
            self.journal.cancel_requested = false;
            Terminal::Partial
        };
        self.journal.terminal = Some(terminal);
        self.save()?;
        let result = self.result()?;
        workspace::atomic_write(&self.root().join("result.json"), &bytes(&result)?)?;
        Ok(self
            .summary("score", terminal.name(), false, true)
            .exit(terminal.exit()))
    }
}
