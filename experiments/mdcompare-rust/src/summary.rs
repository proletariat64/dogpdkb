use anyhow::Error;
use serde::Serialize;
use std::{
    collections::BTreeMap,
    io::{self, Write},
    path::PathBuf,
    sync::{Arc, Mutex, mpsc},
    thread::{self, JoinHandle},
    time::{Duration, SystemTime, UNIX_EPOCH},
};

#[derive(Serialize)]
pub(crate) struct Summary {
    schema_version: &'static str,
    command: &'static str,
    state: String,
    artifacts: BTreeMap<String, PathBuf>,
    counts: BTreeMap<String, usize>,
    reason_code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    comparison_sha256: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    diagnostics: Option<serde_json::Value>,
}

pub(crate) struct Outcome {
    pub(crate) summary: Summary,
    pub(crate) code: u8,
}

impl Summary {
    pub(crate) fn new(command: &'static str, state: impl Into<String>) -> Self {
        Self {
            schema_version: "mdcompare-summary-1",
            command,
            state: state.into(),
            artifacts: BTreeMap::new(),
            counts: BTreeMap::new(),
            reason_code: None,
            comparison_sha256: None,
            diagnostics: None,
        }
    }

    pub(crate) fn artifact(mut self, name: impl Into<String>, path: PathBuf) -> Self {
        debug_assert!(path.is_absolute(), "artifact paths must be absolute");
        self.artifacts.insert(name.into(), path);
        self
    }

    pub(crate) fn count(mut self, name: impl Into<String>, count: usize) -> Self {
        self.counts.insert(name.into(), count);
        self
    }

    pub(crate) fn reason(mut self, code: impl Into<String>) -> Self {
        self.reason_code = Some(code.into());
        self
    }

    pub(crate) fn comparison(mut self, sha256: impl Into<String>) -> Self {
        self.comparison_sha256 = Some(sha256.into());
        self
    }

    pub(crate) fn diagnostics(mut self, details: serde_json::Value) -> Self {
        self.diagnostics = Some(details);
        self
    }

    pub(crate) fn exit(self, code: u8) -> Outcome {
        Outcome {
            summary: self,
            code,
        }
    }

    pub(crate) fn state(&self) -> &str {
        &self.state
    }

    pub(crate) fn reason_code(&self) -> Option<&str> {
        self.reason_code.as_deref()
    }

    pub(crate) fn print(&self, pretty: bool) -> io::Result<()> {
        let mut stdout = io::stdout().lock();
        if pretty {
            serde_json::to_writer_pretty(&mut stdout, self)?;
        } else {
            serde_json::to_writer(&mut stdout, self)?;
        }
        stdout.write_all(b"\n")?;
        stdout.flush()
    }
}

const ERROR_CODES: &[&str] = &[
    "invalid_input",
    "invalid_config",
    "input_drift",
    "binding_invalid",
    "workspace_busy",
    "publish_failed",
    "workspace_missing",
];

pub(crate) fn failure(command: &'static str, error: &Error) -> Outcome {
    // Never expose the originating error string. It can include snapshot text,
    // config credentials, or provider reply data.
    let reason = error
        .chain()
        .find_map(|cause| {
            let text = cause.to_string();
            let tag = text.split_once(':').map_or(text.as_str(), |(tag, _)| tag);
            ERROR_CODES
                .iter()
                .copied()
                .find(|code| tag.eq_ignore_ascii_case(code))
        })
        .unwrap_or("invalid_input");
    Summary::new(command, "error").reason(reason).exit(1)
}

#[derive(Clone, Copy)]
struct ProgressState {
    phase: &'static str,
    completed: usize,
    selected: usize,
    admitted: usize,
    in_flight: usize,
}

#[derive(Clone)]
pub(crate) struct Progress {
    command: &'static str,
    latest: Arc<Mutex<ProgressState>>,
}

pub(crate) struct Heartbeat {
    stop: Option<mpsc::Sender<()>>,
    thread: Option<JoinHandle<()>>,
}

impl Drop for Heartbeat {
    fn drop(&mut self) {
        drop(self.stop.take());
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

impl Progress {
    pub(crate) fn start(command: &'static str) -> (Self, Heartbeat) {
        let progress = Self {
            command,
            latest: Arc::new(Mutex::new(ProgressState {
                phase: "start",
                completed: 0,
                selected: 0,
                admitted: 0,
                in_flight: 0,
            })),
        };
        progress.emit("progress", None);
        let (stop, receiver) = mpsc::channel();
        let reporter = progress.clone();
        let worker = thread::spawn(move || {
            while matches!(
                receiver.recv_timeout(Duration::from_secs(5)),
                Err(mpsc::RecvTimeoutError::Timeout)
            ) {
                reporter.emit("heartbeat", None);
            }
        });
        (
            progress,
            Heartbeat {
                stop: Some(stop),
                thread: Some(worker),
            },
        )
    }

    pub(crate) fn update(
        &self,
        phase: &'static str,
        completed: usize,
        selected: usize,
        admitted: usize,
        in_flight: usize,
    ) {
        let mut latest = self.latest.lock().expect("progress state poisoned");
        *latest = ProgressState {
            phase,
            completed,
            selected,
            admitted,
            in_flight,
        };
        drop(latest);
        self.emit("progress", None);
    }

    pub(crate) fn finish(&self, summary: &Summary) {
        let mut latest = self.latest.lock().expect("progress state poisoned");
        if let Some(selected) = summary.counts.get("selected") {
            latest.selected = *selected;
        }
        if let Some(admitted) = summary.counts.get("admitted") {
            latest.admitted = *admitted;
        }
        if let Some(in_flight) = summary.counts.get("in_flight") {
            latest.in_flight = *in_flight;
        }
        drop(latest);
        self.emit(
            "finish",
            Some(summary.reason_code().unwrap_or(summary.state())),
        );
    }

    fn emit(&self, event: &str, reason: Option<&str>) {
        let latest = *self.latest.lock().expect("progress state poisoned");
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis();
        let mut stderr = io::stderr().lock();
        let _ = writeln!(
            stderr,
            "timestamp_ms={timestamp} command={} event={event} phase={} documents={}/{} requests={} in_flight={} reason={}",
            self.command,
            latest.phase,
            latest.completed,
            latest.selected,
            latest.admitted,
            latest.in_flight,
            reason.unwrap_or("-"),
        );
        let _ = stderr.flush();
    }
}
