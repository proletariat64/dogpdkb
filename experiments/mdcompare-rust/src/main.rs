mod catalog;
mod cli;
mod compare;
mod config;
mod doctor;
mod help;
mod input;
mod knowledge;
mod knowledge_units;
mod ocr;
mod offline;
mod provider;
mod resources;
mod score_context;
mod semantic;
mod semantic_route;
mod semantic_score;
mod summary;
mod text;
mod workspace;

use std::{
    io::{self, Write},
    path::Path,
    process::ExitCode,
};

use anyhow::{Context, Result, bail};
use clap::{Parser, error::ErrorKind};

use crate::{
    cli::{Cli, Command, HelpTopic},
    summary::{Outcome, Progress, Summary},
    workspace::Workspace,
};

fn status(path: &Path) -> Result<Outcome> {
    let workspace = Workspace::locate(path)?;
    match workspace.schema_version()?.as_str() {
        "mdcompare-work-1" => {
            let frozen = workspace::load_frozen(&workspace)?;
            offline::status(&workspace, &frozen)
        }
        "mdcompare-score-work-1" => semantic_score::status(path),
        _ => bail!("invalid_input: unsupported work plan schema"),
    }
}

fn run(command: Command, progress: &Progress) -> Result<Outcome> {
    match command {
        Command::Draft { old, new, work } => {
            let plan = workspace::draft(&old, &new, &work.plan)?;
            Ok(Summary::new("draft", "complete")
                .artifact("plan", plan)
                .count("documents", 1)
                .exit(0))
        }
        Command::Accept { work, settings } => {
            let (workspace, frozen) = workspace::accept(&work.plan, settings.config.as_deref())?;
            let documents = &frozen.work.documents;
            let baseline = documents
                .iter()
                .filter(|doc| doc.baseline.is_some())
                .count();
            let incoming = documents
                .iter()
                .filter(|doc| doc.incoming.is_some())
                .count();
            Ok(Summary::new("accept", "accepted")
                .artifact("plan", workspace.root.join("plan.json"))
                .count("documents", documents.len())
                .count("baseline", baseline)
                .count("incoming", incoming)
                .exit(0))
        }
        Command::Run { work, settings } => {
            offline::run(&work.plan, settings.config.as_deref(), progress)
        }
        Command::Score {
            work,
            settings,
            resume,
        } => semantic_score::run(&work.plan, settings.config.as_deref(), resume, progress),
        Command::Route {
            work,
            scores,
            settings,
        } => semantic_route::run(&work.plan, &scores, settings.config.as_deref()),
        Command::Status { work } => status(&work.plan).context("read work status"),
        Command::Cancel { work } => semantic_score::cancel(&work.plan),
        Command::Doctor { settings, .. } => Ok(doctor::run(settings.config.as_deref())),
        Command::Help { .. } => unreachable!("help was dispatched before business commands"),
    }
}

fn main() -> ExitCode {
    std::panic::set_hook(Box::new(|_| {
        let _ = writeln!(
            io::stderr(),
            "mdcomparer stopped after an unexpected internal failure."
        );
    }));
    let args: Vec<_> = std::env::args_os().collect();
    let cli = match Cli::try_parse_from(&args) {
        Ok(cli) => cli,
        Err(error)
            if match (error.kind(), &args[1..]) {
                (ErrorKind::DisplayHelp, [flag] | [_, flag]) => flag == "--help" || flag == "-h",
                (ErrorKind::DisplayVersion, [flag]) => flag == "--version" || flag == "-V",
                _ => false,
            } =>
        {
            return ExitCode::from(u8::from(error.print().is_err()));
        }
        Err(_) => {
            // Clap's diagnostic can echo untrusted argv. Keep this fixed so an
            // accidentally pasted credential or Markdown never reaches logs.
            let _ = writeln!(
                io::stderr(),
                "Invalid command or arguments. Run mdcomparer --help for usage."
            );
            return ExitCode::from(64);
        }
    };
    if matches!(
        cli.command,
        Command::Help {
            topic: HelpTopic::All
        }
    ) {
        return ExitCode::from(u8::from(
            io::stdout().write_all(help::ALL.as_bytes()).is_err(),
        ));
    }

    let name = cli.command.name();
    let pretty = matches!(cli.command, Command::Doctor { pretty: true, .. });
    let (progress, heartbeat) = Progress::start(name);
    let outcome =
        run(cli.command, &progress).unwrap_or_else(|error| summary::failure(name, &error));
    drop(heartbeat);
    progress.finish(&outcome.summary);
    if outcome.summary.print(pretty).is_err() {
        let _ = writeln!(io::stderr(), "Failed to write the final summary.");
        return ExitCode::FAILURE;
    }
    ExitCode::from(outcome.code)
}
