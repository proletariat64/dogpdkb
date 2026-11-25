use std::path::PathBuf;

use clap::{Args, Parser, Subcommand, ValueEnum};

use crate::help;

#[derive(Parser)]
#[command(
    name = "mdcomparer",
    version,
    about = "Compare captured Markdown and route authored-body changes.",
    disable_help_subcommand = true,
    color = clap::ColorChoice::Never,
    after_help = help::ROOT,
)]
pub(crate) struct Cli {
    #[command(subcommand)]
    pub(crate) command: Command,
}

#[derive(Args)]
pub(crate) struct Plan {
    /// Path to a JSON work plan.
    #[arg(long, value_name = "FILE")]
    pub(crate) plan: PathBuf,
}

#[derive(Args)]
pub(crate) struct Config {
    /// Explicit TOML configuration file.
    #[arg(long, value_name = "FILE")]
    pub(crate) config: Option<PathBuf>,
}

#[derive(Clone, Copy, ValueEnum)]
pub(crate) enum HelpTopic {
    All,
}

#[derive(Subcommand)]
pub(crate) enum Command {
    /// Write an editable semantic work plan for two local snapshots.
    #[command(after_help = help::DRAFT)]
    Draft {
        /// Baseline Markdown snapshot.
        #[arg(value_name = "OLD.md")]
        old: PathBuf,
        /// Incoming Markdown snapshot.
        #[arg(value_name = "NEW.md")]
        new: PathBuf,
        #[command(flatten)]
        work: Plan,
    },
    /// Validate and freeze a comparison work plan.
    #[command(after_help = help::ACCEPT)]
    Accept {
        #[command(flatten)]
        work: Plan,
        #[command(flatten)]
        settings: Config,
    },
    /// Execute an offline comparison or resource inventory.
    #[command(after_help = help::RUN)]
    Run {
        #[command(flatten)]
        work: Plan,
        #[command(flatten)]
        settings: Config,
    },
    /// Score explicitly selected changes with a request budget.
    #[command(after_help = help::SCORE)]
    Score {
        #[command(flatten)]
        work: Plan,
        #[command(flatten)]
        settings: Config,
        /// Clear an explicit cancellation and resume under the frozen budget.
        #[arg(long)]
        resume: bool,
        /// Publish an already settled journal offline without admitting requests.
        #[arg(long, conflicts_with_all = ["resume", "config"])]
        recover: bool,
    },
    /// Apply routing policy to a comparison and its bound scores offline.
    #[command(after_help = help::ROUTE)]
    Route {
        #[command(flatten)]
        work: Plan,
        /// Bound native score result or external score file.
        #[arg(long, value_name = "FILE")]
        scores: PathBuf,
        #[command(flatten)]
        settings: Config,
    },
    /// Read the state and verified artifacts of a work plan.
    #[command(after_help = help::STATUS)]
    Status {
        #[command(flatten)]
        work: Plan,
    },
    /// Stop new score admissions and let in-flight requests settle.
    #[command(after_help = help::CANCEL)]
    Cancel {
        #[command(flatten)]
        work: Plan,
    },
    /// Check local runtime and configured scoring prerequisites.
    #[command(after_help = help::DOCTOR)]
    Doctor {
        #[command(flatten)]
        settings: Config,
        /// Indent the diagnostic JSON for reading in a terminal.
        #[arg(long)]
        pretty: bool,
    },
    /// Read the complete offline contract.
    #[command(after_help = help::HELP)]
    Help { topic: HelpTopic },
}

impl Command {
    pub(crate) fn name(&self) -> &'static str {
        match self {
            Self::Draft { .. } => "draft",
            Self::Accept { .. } => "accept",
            Self::Run { .. } => "run",
            Self::Score { .. } => "score",
            Self::Route { .. } => "route",
            Self::Status { .. } => "status",
            Self::Cancel { .. } => "cancel",
            Self::Doctor { .. } => "doctor",
            Self::Help { .. } => "help",
        }
    }
}
