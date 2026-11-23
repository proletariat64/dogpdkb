mod artifacts;
mod config;
mod llm;
mod markdown;
mod paddle;
mod plan;
mod progress;
mod provider;
mod runner;
mod workspace;

use anyhow::{Context, Result, ensure};
use clap::{Args, Parser, Subcommand, ValueEnum};
use pulldown_cmark::{CodeBlockKind, Event, Parser as Markdown, Tag, TagEnd};
use serde_json::{Value, json};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use workspace::{Purged, Workspace};

const FULL_HELP: &str = include_str!("../../../.scratch/mdocr-rust-cli/spec.md");

type Outcome = Result<(u8, Value)>;

#[derive(Parser)]
#[command(
    name = "mdocr",
    version,
    about = "Extract OCR from Markdown images with durable batch recovery",
    disable_help_subcommand = true,
    after_help = "First use:\n  mdocr draft --input-single source.md --output-single enriched.md --plan work.json\n  mdocr run --plan work.json --config provider.toml\n\nRun 'mdocr <command> --help' for options or 'mdocr help all' for the complete manual."
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Args)]
struct BatchArgs {
    /// Path to the input JSON work plan.
    #[arg(long)]
    plan: PathBuf,
}

#[derive(Args)]
struct ConfiguredBatch {
    #[command(flatten)]
    batch: BatchArgs,
    /// TOML file for this batch. At first acceptance, the default is the neighboring default.toml, then built-in values.
    #[arg(long)]
    config: Option<PathBuf>,
}

#[derive(Clone, ValueEnum)]
enum HelpScope {
    All,
}

#[derive(Subcommand)]
enum Command {
    /// Write an editable JSON work plan without opening an execution workspace.
    #[command(
        after_help = "Example: mdocr draft --input-dir docs --output-dir enriched --plan work.json"
    )]
    Draft(Box<plan::DraftArgs>),

    /// Validate and freeze a work plan and its authorized image inputs.
    #[command(
        after_help = "Example: mdocr accept --plan work.json --config provider.toml\nDownloads selected images when authorized. Exit 0 means the work plan was accepted. Check the reported image counts for failures."
    )]
    Accept(ConfiguredBatch),

    /// Execute a work plan or continue its saved batch.
    #[command(
        after_help = "Example: mdocr run --plan work.json > summary.json 2>> mdocr.log\nThe first run accepts the plan. Repeated runs use the saved policy, budget and journal. Exit 0 means all documents completed, 2 means partial output, and 3 means canceled."
    )]
    Run {
        #[command(flatten)]
        input: ConfiguredBatch,
        /// Continue a batch that was canceled explicitly.
        #[arg(long)]
        resume: bool,
        /// Retry eligible known failures from a previous run.
        #[arg(long)]
        retry_failed: bool,
    },

    /// Stop new requests and save the outcome of in-flight work.
    #[command(
        after_help = "Example: mdocr cancel --plan work.json\nResume a canceled batch with 'mdocr run --plan work.json --resume'."
    )]
    Cancel(BatchArgs),

    /// Recover publishable Markdown offline from an accepted journal.
    #[command(
        after_help = "Example: mdocr assemble --plan work.json --output-dir recovered\nThe directory must be new. The original workspace and provider budget are preserved."
    )]
    Assemble {
        #[command(flatten)]
        batch: BatchArgs,
        /// New directory for recovered Markdown, evidence, and assembly.json.
        #[arg(long)]
        output_dir: PathBuf,
    },

    /// Delete the named batch's workspace with explicit consent.
    #[command(
        after_help = "Example: mdocr purge --plan work.json\nIncomplete batches require a second confirmation. Published Markdown and source files survive cleanup."
    )]
    Purge {
        #[command(flatten)]
        batch: BatchArgs,
        /// Confirm every prompt automatically. A live workspace must finish first.
        #[arg(short = 'Y')]
        yes: bool,
    },

    /// Check local runtime readiness without network access.
    #[command(
        after_help = "Examples:\n  mdocr doctor\n  mdocr doctor --config provider.toml\nExit 2 means one or more local checks failed."
    )]
    Doctor {
        /// TOML file to check. Omission checks the built-in defaults.
        #[arg(long)]
        config: Option<PathBuf>,
    },

    /// Check Mermaid fences in one Markdown file and report JSON diagnostics.
    CheckMermaid {
        /// Markdown file, or '-' for standard input.
        input: PathBuf,
    },

    /// Print notices for bundled assets.
    Licenses,

    /// Print the complete embedded English manual.
    Help {
        #[arg(value_enum)]
        scope: HelpScope,
    },
}

impl Command {
    fn name(&self) -> &'static str {
        match self {
            Self::Draft(_) => "draft",
            Self::Accept(_) => "accept",
            Self::Run { .. } => "run",
            Self::Cancel(_) => "cancel",
            Self::Assemble { .. } => "assemble",
            Self::Purge { .. } => "purge",
            Self::Doctor { .. } => "doctor",
            Self::CheckMermaid { .. } => "check-mermaid",
            Self::Licenses => "licenses",
            Self::Help { .. } => "help",
        }
    }
}

fn accept(workspace: &Workspace, explicit: Option<&Path>) -> Result<Value> {
    artifacts::owned_path(&workspace.plan)?;
    let fresh = !workspace.plan.try_exists()?;
    let (settings, config_source) = if fresh || explicit.is_some() {
        let source = config::source(Some(&workspace.work), explicit)?;
        (Some(config::load(None, source.as_deref())?), source)
    } else {
        (None, None)
    };
    let http = if fresh {
        Some(provider::client_for(
            settings.as_ref().context("missing batch settings")?,
        )?)
    } else {
        None
    };
    let summary = plan::accept(
        &workspace.work,
        &workspace.plan,
        &workspace.images,
        settings.as_ref(),
        http.as_ref(),
        config_source.as_deref(),
    )?;
    let counts = &summary["counts"];
    progress::emit(
        "accepted",
        format_args!(
            "phase=accept new={fresh} documents={} selected={} staged={} failed={}",
            counts["documents"], counts["selected"], counts["staged"], counts["failed"]
        ),
    );
    Ok(summary)
}

fn execute(command: Command) -> Outcome {
    match command {
        Command::Draft(args) => {
            let path = plan::draft(*args)?;
            Ok((0, json!({"status":"drafted", "work_plan_path":path})))
        }
        Command::Accept(input) => {
            let ws = Workspace::new(&input.batch.plan)?;
            let _lock = ws.create_and_lock()?;
            let mut output = accept(&ws, input.config.as_deref())?;
            output["status"] = json!("accepted");
            Ok((0, output))
        }
        Command::Run {
            input,
            resume,
            retry_failed,
        } => {
            let ws = Workspace::new(&input.batch.plan)?;
            let _lock = ws.create_and_lock()?;
            if resume || retry_failed {
                ensure!(
                    ws.plan.try_exists()?,
                    "no accepted batch exists to resume or retry"
                );
            }
            accept(&ws, input.config.as_deref())?;
            runner::run(&ws, resume, retry_failed)
        }
        Command::Cancel(batch) => {
            let ws = Workspace::new(&batch.plan)?;
            ws.ensure_live()?;
            ws.ensure_owner()?;
            runner::cancel(&ws)
        }
        Command::Assemble { batch, output_dir } => {
            let ws = Workspace::new(&batch.plan)?;
            let _lock = ws.lock_existing()?;
            runner::assemble(&ws, output_dir)
        }
        Command::Purge { batch, yes } => workspace::purge(&Workspace::new(&batch.plan)?, yes),
        Command::Doctor { config } => config::doctor(config.as_deref()),
        Command::CheckMermaid { input } => check_mermaid(input),
        Command::Help { .. } | Command::Licenses => unreachable!("text commands handled in main"),
    }
}

fn check_mermaid(input: PathBuf) -> Outcome {
    let markdown = if input.as_os_str() == "-" {
        let mut markdown = String::new();
        io::stdin().read_to_string(&mut markdown)?;
        markdown
    } else {
        std::fs::read_to_string(input)?
    };
    let parser = merman_core::SemanticParser::new();
    let mut block = None;
    let mut count = 0;
    let mut errors = Vec::new();
    for (event, range) in Markdown::new(&markdown).into_offset_iter() {
        match event {
            Event::Start(Tag::CodeBlock(CodeBlockKind::Fenced(info)))
                if info
                    .split_whitespace()
                    .next()
                    .is_some_and(|s| s.eq_ignore_ascii_case("mermaid")) =>
            {
                count += 1;
                let opener = markdown[range.clone()]
                    .lines()
                    .next()
                    .unwrap_or_default()
                    .trim_start();
                let marker = opener.chars().next().unwrap_or_default();
                let width = opener.chars().take_while(|&c| c == marker).count();
                let closed = markdown[range].lines().skip(1).last().is_some_and(|line| {
                    let mut line = line.trim();
                    while let Some(quoted) = line.strip_prefix('>') {
                        line = quoted.trim_start();
                    }
                    line.len() >= width && line.chars().all(|c| c == marker)
                });
                block = Some((String::new(), closed));
            }
            Event::Text(text) => {
                if let Some((source, _)) = &mut block {
                    source.push_str(&text);
                }
            }
            Event::End(TagEnd::CodeBlock) => {
                if let Some((source, closed)) = block.take() {
                    let message = if closed {
                        parser
                            .parse_diagram_sync(&source)
                            .err()
                            .map(|err| err.to_string())
                    } else {
                        Some("Unclosed Mermaid fence".into())
                    };
                    if let Some(message) = message {
                        errors.push(json!({"block": count, "message": message}));
                    }
                }
            }
            _ => {}
        }
    }
    let ok = errors.is_empty();
    let exit_code = if ok { 0 } else { 2 };
    let status = if ok { "valid" } else { "invalid" };
    Ok((
        exit_code,
        json!({"status": status, "ok": ok, "mermaid_errors": errors}),
    ))
}

fn write_summary(command: &str, mut summary: Value) -> Result<()> {
    summary["schema_version"] = json!("mdocr-summary-1");
    summary["command"] = json!(command);
    let mut stdout = io::stdout().lock();
    serde_json::to_writer(&mut stdout, &summary)?;
    writeln!(stdout)?;
    stdout.flush()?;
    Ok(())
}

fn main() -> ExitCode {
    let command = match Cli::try_parse() {
        Ok(cli) => cli.command,
        Err(error) => {
            let usage = error.use_stderr();
            let _ = error.print();
            return ExitCode::from(if usage { 64 } else { 0 });
        }
    };
    let text = match &command {
        Command::Help {
            scope: HelpScope::All,
        } => Some(FULL_HELP),
        Command::Licenses => Some(llm::font_licenses()),
        _ => None,
    };
    if let Some(text) = text {
        return match writeln!(io::stdout().lock(), "{text}") {
            Ok(()) => ExitCode::SUCCESS,
            Err(_) => ExitCode::FAILURE,
        };
    }

    let name = command.name();
    let (code, summary) = match execute(command) {
        Ok(outcome) => outcome,
        Err(error) => {
            let (status, reason) = if error.is::<Purged>() {
                ("purged", "WORKSPACE_PURGED")
            } else if error.chain().any(|cause| {
                cause
                    .downcast_ref::<std::fs::TryLockError>()
                    .is_some_and(|error| matches!(error, std::fs::TryLockError::WouldBlock))
            }) {
                ("error", "WORKSPACE_ACTIVE")
            } else {
                ("error", "COMMAND_FAILED")
            };
            let message = format!("{error:#}");
            let message = message.replace(char::is_control, " ");
            let _ = writeln!(io::stderr().lock(), "mdocr: {name}: {message}");
            (1, json!({"status":status, "reason_code":reason}))
        }
    };
    match write_summary(name, summary) {
        Ok(()) => ExitCode::from(code),
        Err(error) => {
            let _ = writeln!(io::stderr().lock(), "mdocr: cannot write summary: {error}");
            ExitCode::FAILURE
        }
    }
}
