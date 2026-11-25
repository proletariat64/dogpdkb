use crate::{
    config::{Config, Scope, ScoreConfig},
    provider::{FORMAT, PROMPT, Provider},
    score_context::{self, Packet},
    semantic_generation,
    summary::{Outcome, Progress},
    workspace::{self, Workspace, checksum as digest},
};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    io::{self, Write},
    path::{Path, PathBuf},
    sync::mpsc,
    thread,
    time::Duration,
};

mod journal;

use journal::Next;

const WORK: &str = "mdcompare-score-work-1";
const PLAN: &str = "mdcompare-score-plan-1";

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
    let generation = semantic_generation::load(&workspace)?;
    ensure!(generation.comparison_sha256 == sha, "binding_invalid");
    let bindings = generation
        .score_candidates()?
        .into_iter()
        .map(|((knowledge_base, document_id), raw)| {
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
        plan.input_paths()
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

fn seal_file(path: &Path) -> Result<PathBuf> {
    let parent = path.parent().context("binding_invalid")?;
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .context("binding_invalid")?;
    workspace::artifact_path(parent, name)
}

fn verify_seal(task: &Task, path: &Path) -> Result<()> {
    let (existing, _) = crate::input::read(path, 65)
        .map_err(anyhow::Error::msg)
        .context("binding_invalid")?;
    ensure!(
        existing == format!("{}\n", task.plan_sha256).as_bytes(),
        "binding_invalid"
    );
    Ok(())
}

fn ensure_seal(task: &Task, path: &Path) -> Result<()> {
    let path = seal_file(path)?;
    let parent = path.parent().context("binding_invalid")?;
    if exists(&path)? {
        verify_seal(task, &path)?;
    } else {
        // Adoption is only safe for an accepted workspace that never began
        // execution. A spent journal without its ownership seal is damaged.
        ensure!(task.journal().unstarted()?, "binding_invalid");
        let mut temp = tempfile::NamedTempFile::new_in(parent).context("publish_failed")?;
        writeln!(temp, "{}", task.plan_sha256).context("publish_failed")?;
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
        journal::initialize(&workspace, plan, frozen.config.concurrency)?;
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

    fn journal(&self) -> journal::Store<'_> {
        journal::Store::new(&self.workspace.root, &self.frozen, &self.plan_sha256)
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
                .input_paths()
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
}

pub use journal::verified_result;

fn observe(plan: &Path, cancel: bool) -> Result<Outcome> {
    let task = Task::open(Workspace::locate(plan)?)?;
    let locked = task.journal().lock()?;
    let running = locked.running()?;
    locked.load()?.observe(cancel, running)
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

fn run_pending(task: &Task, directory: &Path, progress: &Progress) -> Result<()> {
    let config = &task.frozen.config;
    let provider = Provider::new(config);
    let (sent, finished) = mpsc::channel();
    let known = task.journal().known_documents()?;
    let mut remaining = task
        .frozen
        .documents
        .iter()
        .enumerate()
        .filter(|(document, _)| !known.contains(document))
        .peekable();
    let mut packet: Option<(usize, Packet)> = None;
    let mut checked = false;
    let mut progress_counts = None;
    thread::scope(|scope| -> Result<()> {
        loop {
            while let Ok((sequence, operations, outcome)) = finished.try_recv() {
                task.journal()
                    .lock()?
                    .load()?
                    .settle(sequence, operations, outcome)?;
            }
            {
                let mut journal = task.journal().lock()?.load()?;
                journal.drain_throttle()?;
                let counts = journal.counts();
                if progress_counts != Some(counts) {
                    progress_counts = Some(counts);
                    drop(journal);
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
                match journal.next(
                    remaining.peek().is_some() || packet.is_some(),
                    packet
                        .as_ref()
                        .map(|(document, packet)| (*document, packet.evidence.as_str())),
                )? {
                    Next::Done => break,
                    Next::Wait => {}
                    Next::Admitted(sequence) => {
                        let (
                            _,
                            Packet {
                                evidence,
                                operations,
                            },
                        ) = packet.take().context("binding_invalid")?;
                        let sent = sent.clone();
                        let provider = &provider;
                        scope.spawn(move || {
                            let outcome = provider.assess(&evidence);
                            let _ = sent.send((sequence, operations, outcome));
                        });
                        continue;
                    }
                    Next::Prepare => {
                        drop(journal);
                        let (document, binding) = remaining.next().context("binding_invalid")?;
                        match prepare_packet(directory, binding, config.max_evidence_bytes)? {
                            Ok(prepared) => {
                                // Only load scoring prerequisites for a sendable packet.
                                if !checked {
                                    task.protect_oauth_file()?;
                                    provider.score_preflight().map_err(|reason| {
                                        anyhow::anyhow!("invalid_config: {reason}")
                                    })?;
                                    checked = true;
                                }
                                packet = Some((document, prepared));
                            }
                            Err(reason) => task.journal().lock()?.load()?.hold(document, reason)?,
                        }
                        continue;
                    }
                }
            }
            if let Ok((sequence, operations, outcome)) =
                finished.recv_timeout(Duration::from_millis(200))
            {
                task.journal()
                    .lock()?
                    .load()?
                    .settle(sequence, operations, outcome)?;
            }
        }
        Ok(())
    })
}

/// Reconstruct only an already settled receipt. This entry point cannot
/// initialize a score plan, recover unknown admissions or dispatch requests.
pub fn recover(plan: &Path) -> Result<Outcome> {
    let task = Task::open(Workspace::locate(plan)?)?;
    let locked = task.journal().lock()?;
    let _execution = locked.claim_execution()?;
    verify_seal(&task, &seal_file(&seal_path(&task.workspace))?)?;
    task.comparison()?;
    locked.load()?.recover()
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
        let locked = task.journal().lock()?;
        // Claim execution and clear the previous cancellation together.
        // Cancel cannot observe this run before the transition is durable.
        let execution = locked.claim_execution()?;
        if let Some(outcome) = locked.load()?.start(resume)? {
            return Ok(outcome);
        }
        execution
    };
    run_pending(&task, &directory, progress)?;
    task.journal().lock()?.load()?.finish()
}
