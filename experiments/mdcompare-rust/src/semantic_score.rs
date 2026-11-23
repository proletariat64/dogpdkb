use crate::{
    input::{Reader, check_output, publish},
    provider::{self, FORMAT, Outcome, PROMPT},
    score_context::{self, Packet},
    semantic_route::{self, Binding, Generation, Identity, digest, eligible, identity},
};
use anyhow::{Context, Result, anyhow, bail, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{File, OpenOptions},
    io::{self, Write},
    os::{fd::AsRawFd, unix::fs::OpenOptionsExt},
    path::{Path, PathBuf},
    sync::mpsc::{self, Receiver},
    thread,
};

pub const DEFAULT_MODEL: &str = "openai/gpt-6.1-terra";
pub const DEFAULT_EFFORT: &str = "medium";

pub struct Options {
    pub report: PathBuf,
    pub diff_index: PathBuf,
    pub output: PathBuf,
    pub model: String,
    pub effort: String,
    pub request_budget: usize,
    pub document_ids: Vec<String>,
    pub concurrency: usize,
    pub max_evidence_bytes: usize,
}

#[derive(Deserialize, Serialize)]
struct State {
    configuration: Value,
    attempts: Vec<Value>,
    held: Vec<Value>,
    stop: Option<String>,
    concurrency: usize,
}

fn protected_output(
    generation: &Generation,
    output: &Path,
    journal: &Path,
    lock: &Path,
) -> Result<()> {
    let destinations = [output, journal, lock];
    for (index, path) in destinations.iter().enumerate() {
        check_output(
            path,
            generation
                .inputs
                .iter()
                .map(PathBuf::as_path)
                .chain(destinations[..index].iter().copied()),
        )?;
    }
    Ok(())
}

fn save(path: &Path, value: &impl Serialize) -> Result<()> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    publish(&[(path.to_owned(), bytes)])
}

fn save_journal(path: &Path, state: &State) -> Result<()> {
    save(path, state)?;
    File::open(path.parent().context("Invalid journal directory")?)?.sync_all()?;
    Ok(())
}

fn record_outcome(state: &mut State, index: usize, operations: usize, outcome: Outcome) {
    let attempt = &mut state.attempts[index];
    let assessment = match (&outcome.reply, outcome.reason) {
        (Some(reply), None) => {
            attempt["reply"] = json!(reply);
            score_context::validate_reply(reply, operations)
        }
        (_, reason) => Err(reason.unwrap_or("invalid_response")),
    };
    attempt["usage_tokens"] = json!(outcome.usage);
    let reason = match assessment {
        Ok((score, ambiguous)) => {
            attempt["status"] = json!("scored");
            attempt["score"] = json!(score);
            attempt["ambiguous"] = json!(ambiguous);
            return;
        }
        Err(reason) => reason,
    };
    attempt["status"] = json!("unscored");
    attempt["reason"] = json!(reason);
    if let Some(status) = outcome.http_status {
        attempt["http_status"] = json!(status);
    }
    if outcome.http_status == Some(429) && attempt["concurrency"] == state.concurrency {
        if state.concurrency > 1 {
            state.concurrency -= 1;
        } else {
            state.stop.get_or_insert("rate_limited".to_owned());
        }
    } else if matches!(
        reason,
        "auth_error"
            | "oauth_required"
            | "sdk_unavailable"
            | "proxy_unavailable"
            | "bridge_error"
            | "unsupported_model"
            | "unsupported_transport"
            | "unknown_outcome"
    ) {
        state.stop.get_or_insert(reason.to_owned());
    }
}

fn relative(from: &Path, to: &Path) -> PathBuf {
    let shared = from
        .components()
        .zip(to.components())
        .take_while(|(a, b)| a == b)
        .count();
    let mut path = PathBuf::new();
    for _ in from.components().skip(shared) {
        path.push("..");
    }
    path.extend(to.components().skip(shared));
    path
}

fn suffixed(path: &Path, suffix: &str) -> Result<PathBuf> {
    let mut name = path
        .file_name()
        .context("Invalid score output path")?
        .to_owned();
    name.push(suffix);
    Ok(path.with_file_name(name))
}

fn open_lock(path: &Path) -> Result<File> {
    let file = OpenOptions::new()
        .read(true)
        .append(true)
        .create(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)?;
    ensure!(file.metadata()?.is_file(), "SCORE_EXECUTION_LOCK_INVALID");
    let result = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
    if result != 0 {
        return Err(io::Error::last_os_error()).context("SCORE_EXECUTION_LOCKED");
    }
    Ok(file)
}

fn output_path(path: &Path) -> Result<PathBuf> {
    match path.canonicalize() {
        Ok(path) => Ok(path),
        Err(err) if err.kind() == io::ErrorKind::NotFound => {
            let parent = path
                .parent()
                .filter(|dir| !dir.as_os_str().is_empty())
                .unwrap_or(Path::new("."));
            Ok(parent
                .canonicalize()?
                .join(path.file_name().context("Invalid score output path")?))
        }
        Err(err) => Err(err.into()),
    }
}

type Candidates = Vec<(Identity, Value)>;

fn candidates(
    generation: &Generation,
    ids: &[String],
) -> Result<(Candidates, Option<Vec<String>>)> {
    let mut rows = Vec::new();
    let mut found = BTreeSet::new();
    let selected: BTreeSet<_> = ids.iter().collect();
    ensure!(selected.len() == ids.len(), "SCORE_DOCUMENT_SCOPE_INVALID");
    for row in generation.results.iter().filter(|row| eligible(row)) {
        let key = identity(row)?;
        if !selected.is_empty() && !selected.contains(&key.1) {
            continue;
        }
        if !selected.is_empty() {
            ensure!(found.insert(key.1.clone()), "SCORE_DOCUMENT_SCOPE_INVALID");
        }
        let Binding { evidence, digest } = generation
            .bindings
            .get(&key)
            .context("SCORE_BINDING_INVALID")?;
        let value = json!({
            "knowledge_base": key.0,
            "document_id": key.1,
            "evidence": evidence,
            "evidence_sha256": digest,
        });
        rows.push((key, value));
    }
    ensure!(
        found.len() == selected.len(),
        "SCORE_DOCUMENT_SCOPE_INVALID"
    );
    Ok((
        rows,
        (!found.is_empty()).then(|| found.into_iter().collect()),
    ))
}

fn configure(opts: &Options, generation: &Generation, selected: Option<Vec<String>>) -> Value {
    let mut config = json!({
        "report_sha256": generation.report_sha256,
        "index_sha256": generation.index_sha256,
        "model": opts.model,
        "effort": opts.effort,
        "request_budget": opts.request_budget,
        "concurrency": opts.concurrency,
        "max_evidence_bytes": opts.max_evidence_bytes,
        "prompt_sha256": digest(PROMPT.as_bytes()),
        "format_sha256": digest(FORMAT.as_bytes()),
    });
    if let Some(ids) = selected {
        config["document_ids"] = json!(ids);
    }
    config
}

fn load_state(
    config: Value,
    output: &Path,
    journal: &Path,
    lock: &File,
    opts: &Options,
    candidates: &Candidates,
) -> Result<State> {
    let state = match crate::input::read(journal, u64::MAX) {
        Ok((bytes, _)) => {
            let state: State =
                serde_json::from_slice(&bytes).context("SCORE_EXECUTION_BINDING_INVALID")?;
            ensure!(
                state.configuration == config,
                "SCORE_EXECUTION_BINDING_INVALID"
            );
            state
        }
        Err(code) => {
            if journal.symlink_metadata().is_ok() {
                bail!("{code}");
            }
            ensure!(
                lock.metadata()?.len() == 0,
                "SCORE_EXECUTION_JOURNAL_MISSING"
            );
            ensure!(!output.try_exists()?, "SCORE_OUTPUT_UNOWNED");
            State {
                configuration: config,
                attempts: Vec::new(),
                held: Vec::new(),
                stop: None,
                concurrency: opts.concurrency,
            }
        }
    };
    let bindings: BTreeMap<_, _> = candidates.iter().map(|(key, value)| (key, value)).collect();
    let mut seen = BTreeSet::new();
    ensure!(
        (1..=opts.concurrency).contains(&state.concurrency)
            && state.attempts.len() <= opts.request_budget,
        "SCORE_EXECUTION_BINDING_INVALID"
    );
    for row in state.attempts.iter().chain(&state.held) {
        let key = identity(row).context("SCORE_EXECUTION_BINDING_INVALID")?;
        let binding = bindings
            .get(&key)
            .context("SCORE_EXECUTION_BINDING_INVALID")?;
        ensure!(
            row["evidence"] == binding["evidence"]
                && row["evidence_sha256"] == binding["evidence_sha256"]
                && seen.insert(key),
            "SCORE_EXECUTION_BINDING_INVALID"
        );
    }
    for attempt in &state.attempts {
        ensure!(
            matches!(
                attempt["status"].as_str(),
                Some("scored" | "admitted" | "unscored")
            ) && attempt["concurrency"]
                .as_u64()
                .is_some_and(|n| (1..=opts.concurrency as u64).contains(&n))
                && (attempt["status"] != "scored"
                    || (attempt["score"]
                        .as_u64()
                        .is_some_and(|n| (1..=5).contains(&n))
                        && attempt["ambiguous"].is_boolean())),
            "SCORE_EXECUTION_BINDING_INVALID"
        );
    }
    Ok(state)
}

type Completion = (usize, usize, Outcome);

fn settle(
    result: Completion,
    state: &mut State,
    active: &mut BTreeMap<usize, usize>,
    journal: &Path,
) -> Result<()> {
    let (index, operations, outcome) = result;
    ensure!(
        active.remove(&index).is_some(),
        "Duplicate scorer completion"
    );
    record_outcome(state, index, operations, outcome);
    save_journal(journal, state)
}

fn await_capacity(
    completed: &Receiver<Completion>,
    state: &mut State,
    active: &mut BTreeMap<usize, usize>,
    journal: &Path,
) -> Result<()> {
    while !active.is_empty() {
        let over_capacity = active.len() >= state.concurrency
            || active.values().any(|&tier| tier > state.concurrency)
            || state.stop.is_some();
        let next = if over_capacity {
            completed.recv().context("Missing scorer completion")?
        } else {
            match completed.try_recv() {
                Ok(next) => next,
                Err(mpsc::TryRecvError::Empty) => break,
                Err(err) => return Err(err.into()),
            }
        };
        settle(next, state, active, journal)?;
    }
    Ok(())
}

fn score_pending(
    opts: &Options,
    directory: &Path,
    journal: &Path,
    state: &mut State,
    candidates: &Candidates,
    assess: &(impl Fn(&str, &str, &str) -> Outcome + Sync),
) -> Result<()> {
    let known = state
        .attempts
        .iter()
        .chain(&state.held)
        .map(identity)
        .collect::<Result<BTreeSet<_>>>()?;
    let (sender, completed) = mpsc::channel::<Completion>();
    let mut active = BTreeMap::new();
    thread::scope(|scope| -> Result<()> {
        for (key, binding) in candidates {
            if known.contains(key) {
                continue;
            }
            if state.stop.is_some() || state.attempts.len() >= opts.request_budget {
                break;
            }
            let path = directory.join(
                binding["evidence"]
                    .as_str()
                    .context("Invalid evidence path")?,
            );
            let (bytes, stamp) =
                crate::input::read(&path, u64::MAX).map_err(|_| anyhow!("SCORE_EVIDENCE_DRIFT"))?;
            ensure!(
                binding["evidence_sha256"] == digest(&bytes),
                "SCORE_EVIDENCE_DRIFT"
            );
            let packet = match score_context::prepare(&bytes)? {
                Ok(packet) if packet.evidence.len() <= opts.max_evidence_bytes => packet,
                held => {
                    let reason = held.err().unwrap_or("OVERSIZED_EVIDENCE");
                    let mut row = binding.clone();
                    row["reason"] = json!(reason);
                    state.held.push(row);
                    continue;
                }
            };
            await_capacity(&completed, state, &mut active, journal)?;
            if state.stop.is_some() {
                break;
            }
            // Validate the exact artifact immediately before accepting its evidence.
            ensure!(stamp.current(&path), "SCORE_EVIDENCE_DRIFT");
            let mut attempt = binding.clone();
            attempt["status"] = json!("admitted");
            attempt["packet_sha256"] = json!(digest(packet.evidence.as_bytes()));
            attempt["concurrency"] = json!(state.concurrency);
            let index = state.attempts.len();
            state.attempts.push(attempt);
            save_journal(journal, state)?;
            let sender = sender.clone();
            active.insert(index, state.concurrency);
            scope.spawn(move || {
                let Packet {
                    evidence,
                    operations,
                } = packet;
                let outcome = assess(&opts.model, &opts.effort, &evidence);
                let _ = sender.send((index, operations, outcome));
            });
        }
        drop(sender);
        while !active.is_empty() {
            let next = completed.recv().context("Missing scorer completion")?;
            settle(next, state, &mut active, journal)?;
        }
        Ok(())
    })
}

pub fn run(opts: Options) -> Result<bool> {
    run_with_assessor(&opts, &provider::assess)
}

fn run_with_assessor(
    opts: &Options,
    assess: &(impl Fn(&str, &str, &str) -> Outcome + Sync),
) -> Result<bool> {
    ensure!((1..=3).contains(&opts.concurrency), "INVALID_ARGUMENTS");
    let output = output_path(&opts.output).context("SCORE_BINDING_INVALID")?;
    let journal = suffixed(&output, ".execution.json")?;
    let lock_path = suffixed(&output, ".lock")?;
    let mut reader = Reader::new(u64::MAX);
    let generation = semantic_route::load(&opts.report, &opts.diff_index, &mut reader)
        .map_err(|_| anyhow!("SCORE_BINDING_INVALID"))?;
    protected_output(&generation, &output, &journal, &lock_path)
        .map_err(|_| anyhow!("SCORE_BINDING_INVALID"))?;
    let (candidates, selected) = candidates(&generation, &opts.document_ids)?;
    let config = configure(opts, &generation, selected);

    let mut lock = open_lock(&lock_path)?;
    let mut state = load_state(config, &output, &journal, &lock, opts, &candidates)?;
    if lock.metadata()?.len() == 0 {
        lock.write_all(b"mdcompare-semantic-score\n")?;
        lock.sync_all()?;
    }
    for previous in &mut state.attempts {
        if previous["status"] == "admitted" {
            previous["status"] = json!("unscored");
            previous["reason"] = json!("unknown_outcome");
        }
    }
    score_pending(
        opts,
        &generation.directory,
        &journal,
        &mut state,
        &candidates,
        assess,
    )?;
    ensure!(reader.changed_paths().is_empty(), "SCORE_EVIDENCE_DRIFT");
    let rows: BTreeMap<_, _> = state
        .attempts
        .iter()
        .chain(&state.held)
        .map(|row| Ok((identity(row)?, row)))
        .collect::<Result<_>>()?;
    let mut scored = Vec::new();
    let mut unscored = Vec::new();
    for (key, mut binding) in candidates {
        let row = match rows.get(&key) {
            Some(row) => {
                let mut row = (*row).clone();
                row.as_object_mut()
                    .context("Invalid score row")?
                    .remove("reply");
                row
            }
            None => {
                binding["reason"] =
                    json!(state.stop.as_deref().unwrap_or("REQUEST_BUDGET_EXHAUSTED"));
                binding
            }
        };
        if row["status"] == "scored" {
            scored.push(row);
        } else {
            unscored.push(row);
        }
    }
    let complete = unscored.is_empty();
    save_journal(&journal, &state)?;
    save(
        &output,
        &json!({
            "schema_version": "mdcompare-semantic-scores-1",
            "diff_index": relative(output.parent().context("Invalid scores directory")?, &opts.diff_index.canonicalize()?),
            "scores": scored,
            "unscored": unscored,
            "execution": {
                "configuration": state.configuration,
                "requests": state.attempts.len(),
                "concurrency": state.concurrency,
                "stop": state.stop,
            },
        }),
    )?;
    Ok(complete)
}

#[cfg(test)]
mod tests;
