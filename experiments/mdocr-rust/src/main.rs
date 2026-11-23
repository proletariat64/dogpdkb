mod artifacts;
mod llm;
mod paddle;
mod plan;
mod provider;
mod runner;

use anyhow::Result;
use clap::{Parser, Subcommand};
use pulldown_cmark::{CodeBlockKind, Event, Parser as Markdown, Tag, TagEnd};
use serde_json::json;
use std::io::{self, Read};
use std::path::PathBuf;
use std::process::ExitCode;

#[derive(Parser)]
#[command(version, about = "Experimental Rust mdocr")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Freeze an OCR plan without running inference.
    Plan(Box<plan::Args>),
    /// Execute a frozen OCR plan and persist its result.
    Run(runner::Args),
    /// Assemble already accepted patches into a fresh directory without network access.
    Assemble(runner::AssembleArgs),
    /// Print notices for the fonts embedded for SVG rendering.
    Licenses,
    /// Check Mermaid fences in one Markdown document and print JSON diagnostics.
    CheckMermaid {
        /// Markdown file, or '-' for standard input.
        input: PathBuf,
    },
}

fn check_mermaid(input: PathBuf) -> Result<bool> {
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
    println!("{}", json!({"ok": ok, "mermaid_errors": errors}));
    Ok(ok)
}

fn main() -> ExitCode {
    let result = match Cli::parse().command {
        Command::Plan(args) => {
            return match plan::write(*args) {
                Ok(()) => ExitCode::SUCCESS,
                Err(error) => {
                    eprintln!("mdocr: plan: {error:#}");
                    ExitCode::FAILURE
                }
            };
        }
        Command::Run(args) => {
            return match runner::run(args) {
                Ok(code) => ExitCode::from(code),
                Err(error) => {
                    eprintln!("mdocr: run: {error:#}");
                    ExitCode::FAILURE
                }
            };
        }
        Command::Assemble(args) => {
            return match runner::assemble(args) {
                Ok(code) => ExitCode::from(code),
                Err(error) => {
                    eprintln!("mdocr: assemble: {error:#}");
                    ExitCode::FAILURE
                }
            };
        }
        Command::Licenses => {
            println!("{}", llm::font_licenses());
            return ExitCode::SUCCESS;
        }
        Command::CheckMermaid { input } => check_mermaid(input),
    };
    match result {
        Ok(true) => ExitCode::SUCCESS,
        Ok(false) => ExitCode::from(1),
        Err(error) => {
            eprintln!("mdocr: {error:#}");
            ExitCode::from(2)
        }
    }
}
