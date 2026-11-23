mod catalog;
mod compare;
mod doctor;
mod help;
mod input;
mod knowledge;
mod knowledge_units;
mod ocr;
mod provider;
mod resources;
mod score_context;
mod semantic;
mod semantic_route;
mod semantic_score;
mod text;

use anyhow::{Result, ensure};
use clap::{
    Args, Parser, Subcommand, ValueEnum,
    error::{ContextKind, ErrorKind},
};
use std::path::PathBuf;
use std::process::ExitCode;

const DEFAULT_FILE_BYTES: u64 = 16 * 1024 * 1024;
const DEFAULT_DIFF_WORK: usize = 2_000_000;

#[derive(Parser)]
#[command(
    version,
    about = "Compare captured Markdown offline or catalog its resource evidence.",
    disable_help_subcommand = true,
    args_override_self = true,
    after_help = help::ROOT,
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Args)]
struct Pair {
    /// Baseline JSON manifest of immutable snapshots.
    #[arg(long)]
    baseline: PathBuf,
    /// Incoming JSON manifest paired by scoped identity.
    #[arg(long)]
    incoming: PathBuf,
    /// Authoritative JSON report. Its parent must exist.
    #[arg(long)]
    output: PathBuf,
    /// Maximum bytes per document or evidence file.
    #[arg(long, default_value_t = DEFAULT_FILE_BYTES, value_parser = positive_limit)]
    max_file_bytes: u64,
    /// Maximum work spent explaining differences.
    #[arg(long, default_value_t = DEFAULT_DIFF_WORK)]
    max_diff_work: usize,
}

impl Pair {
    fn options(&self) -> text::Options {
        text::Options {
            file_bytes: self.max_file_bytes,
            normalize: false,
            evidence: false,
            work: self.max_diff_work,
            chars: 8000,
            char_region: 4096,
        }
    }
}

#[derive(Args)]
struct Evidence {
    #[command(flatten)]
    pair: Pair,
    /// Optional private Markdown report.
    #[arg(long)]
    report: Option<PathBuf>,
    /// Include bounded body evidence.
    #[arg(long)]
    include_evidence: bool,
    /// Maximum number of body characters in evidence.
    #[arg(long, default_value_t = 8000)]
    max_evidence_chars: usize,
}

impl Evidence {
    fn options(&self) -> text::Options {
        text::Options {
            evidence: self.include_evidence,
            chars: self.max_evidence_chars,
            ..self.pair.options()
        }
    }
}

#[derive(Clone, Copy, ValueEnum)]
enum Mode {
    Offline,
    Llm,
}

#[derive(Clone, Copy, ValueEnum)]
enum Effort {
    Low,
    Medium,
    High,
    Xhigh,
}

impl Effort {
    fn name(self) -> &'static str {
        match self {
            Self::Low => "low",
            Self::Medium => "medium",
            Self::High => "high",
            Self::Xhigh => "xhigh",
        }
    }
}

#[derive(Clone, Copy, ValueEnum)]
enum YesNo {
    Yes,
    No,
}

#[derive(Subcommand)]
enum Command {
    /// Strict file and processing-evidence comparison.
    #[command(after_help = help::COMPARE, args_override_self = true)]
    Compare {
        #[command(flatten)]
        files: Evidence,
        /// Normalize CRLF and lone CR to LF.
        #[arg(long)]
        normalize_line_endings: bool,
        /// Maximum combined length for character explanation.
        #[arg(long, default_value_t = 4096)]
        max_char_region: usize,
    },
    /// Compare body knowledge while excluding images and OCR.
    #[command(after_help = help::KNOWLEDGE, args_override_self = true)]
    Knowledge {
        #[command(flatten)]
        files: Evidence,
    },
    /// Route authored-body changes with durable difference evidence.
    #[command(after_help = help::SEMANTIC, args_override_self = true)]
    Semantic {
        #[command(flatten)]
        pair: Pair,
        #[arg(long)]
        diff_output: PathBuf,
        #[arg(long)]
        recovery: Option<PathBuf>,
    },
    /// Score authored changes through Pi OAuth.
    #[command(after_help = help::SCORE, args_override_self = true)]
    SemanticScore {
        #[arg(long)]
        report: PathBuf,
        #[arg(long)]
        diff_index: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long, default_value = semantic_score::DEFAULT_MODEL)]
        model: String,
        #[arg(long, value_enum, default_value = semantic_score::DEFAULT_EFFORT)]
        effort: Effort,
        #[arg(long)]
        request_budget: usize,
        #[arg(long = "document-id")]
        document_ids: Vec<String>,
        #[arg(long, default_value_t = 3, value_parser = clap::value_parser!(u8).range(1..=3))]
        concurrency: u8,
        #[arg(long, default_value_t = 65536, value_parser = clap::value_parser!(u64).range(1..))]
        max_evidence_bytes: u64,
    },
    /// Route changed documents from bound offline scores.
    #[command(after_help = help::ROUTE, args_override_self = true)]
    SemanticRoute {
        #[arg(long)]
        report: PathBuf,
        #[arg(long)]
        diff_index: PathBuf,
        #[arg(long)]
        scores: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long, default_value_t = 3, value_parser = clap::value_parser!(u8).range(1..=5))]
        compile_threshold: u8,
        #[arg(long, value_enum, default_value = "no")]
        skip_ambiguity_intercept: YesNo,
    },
    /// Build an offline resource manifest from Markdown.
    #[command(after_help = help::CATALOG, args_override_self = true)]
    Catalog {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Check the local comparison or scoring environment.
    #[command(after_help = help::DOCTOR, args_override_self = true)]
    Doctor {
        #[arg(long, value_enum, default_value = "offline")]
        mode: Mode,
        #[arg(long)]
        model: Option<String>,
        #[arg(long, value_enum)]
        effort: Option<Effort>,
        #[arg(long)]
        json: bool,
    },
}

fn positive_limit(value: &str) -> std::result::Result<u64, &'static str> {
    let limit = value.parse().map_err(|_| "expected nonnegative integer")?;
    if limit == 0 {
        Err("File limit must be positive")
    } else {
        Ok(limit)
    }
}

fn run(command: Command) -> Result<bool> {
    match command {
        Command::Compare {
            files,
            normalize_line_endings,
            max_char_region,
        } => {
            let options = text::Options {
                normalize: normalize_line_endings,
                char_region: max_char_region,
                ..files.options()
            };
            compare::run(
                &files.pair.baseline,
                &files.pair.incoming,
                &files.pair.output,
                files.report.as_deref(),
                &options,
            )
        }
        Command::Knowledge { files } => knowledge::run(
            &files.pair.baseline,
            &files.pair.incoming,
            &files.pair.output,
            files.report.as_deref(),
            &files.options(),
        ),
        Command::Semantic {
            pair,
            diff_output,
            recovery,
        } => semantic::run(
            &pair.baseline,
            &pair.incoming,
            &pair.output,
            &diff_output,
            recovery.as_deref(),
            &pair.options(),
        ),
        Command::SemanticScore {
            report,
            diff_index,
            output,
            model,
            effort,
            request_budget,
            document_ids,
            concurrency,
            max_evidence_bytes,
        } => semantic_score::run(semantic_score::Options {
            report,
            diff_index,
            output,
            model,
            effort: effort.name().to_owned(),
            request_budget,
            document_ids,
            concurrency: usize::from(concurrency),
            max_evidence_bytes: usize::try_from(max_evidence_bytes)?,
        }),
        Command::SemanticRoute {
            report,
            diff_index,
            scores,
            output,
            compile_threshold,
            skip_ambiguity_intercept,
        } => semantic_route::run(
            &report,
            &diff_index,
            &scores,
            &output,
            compile_threshold,
            matches!(skip_ambiguity_intercept, YesNo::Yes),
        ),
        Command::Catalog { input, output } => {
            catalog::publish(&input, &output)?;
            Ok(true)
        }
        Command::Doctor {
            mode,
            model,
            effort,
            json,
        } => {
            let llm = matches!(mode, Mode::Llm);
            ensure!(
                llm || (model.is_none() && effort.is_none()),
                "INVALID_ARGUMENTS: --model and --effort require --mode llm"
            );
            doctor::run(llm, json, model.as_deref(), effort.map(Effort::name))
        }
    }
}

fn main() -> ExitCode {
    let mut args: Vec<_> = std::env::args_os().collect();
    for arg in &mut args[1..] {
        if arg == "-h" {
            *arg = "--help".into();
        }
    }
    let full = args.iter().position(|arg| arg == "--full");
    if let Some(index) = full {
        if !args.iter().any(|arg| arg == "--help") {
            eprintln!("mdcompare: INVALID_ARGUMENTS: --full requires -h or --help");
            return ExitCode::FAILURE;
        }
        if let Some(command) = args
            .get(2)
            .and_then(|arg| arg.to_str())
            .filter(|arg| index == 1 && help::is_command(arg))
        {
            eprintln!(
                "mdcompare: INVALID_ARGUMENTS: place --full after the command: mdcompare {command} --help --full"
            );
            return ExitCode::FAILURE;
        }
        args.remove(index);
    }
    let command = args
        .get(1)
        .and_then(|arg| arg.to_str())
        .filter(|arg| help::is_command(arg));
    let cli = match Cli::try_parse_from(&args) {
        Ok(cli) => cli,
        Err(error) if error.kind() == ErrorKind::DisplayVersion => {
            println!("{}", env!("CARGO_PKG_VERSION"));
            return ExitCode::SUCCESS;
        }
        Err(error) => {
            if error.use_stderr() {
                eprint!("mdcompare: INVALID_ARGUMENTS: ");
                if error.kind() == ErrorKind::InvalidSubcommand {
                    eprint!("invalid choice. ");
                }
                if error.kind() == ErrorKind::UnknownArgument
                    && let Some(arg) = error.get(ContextKind::InvalidArg)
                {
                    eprintln!("unrecognized arguments: {arg}");
                }
            }
            if error.print().is_err() {
                return ExitCode::FAILURE;
            }
            if full.is_some() && error.kind() == ErrorKind::DisplayHelp {
                help::print_full(command);
            } else if error.use_stderr() {
                let scope = command.map_or(String::new(), |command| format!(" {command}"));
                eprintln!("See mdcompare{scope} --help for usage.");
            }
            return ExitCode::from(u8::from(error.use_stderr()));
        }
    };
    match run(cli.command) {
        Ok(true) => ExitCode::SUCCESS,
        Ok(false) => ExitCode::from(2),
        Err(err) => {
            eprintln!("mdcompare: {err}");
            ExitCode::FAILURE
        }
    }
}
