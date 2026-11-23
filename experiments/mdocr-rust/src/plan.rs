use crate::artifacts::{atomic, json_bytes, resolve, sha};
use anyhow::{Context, Result, ensure};
use clap::{Args as ClapArgs, ValueEnum};
use percent_encoding::percent_decode_str;
use pulldown_cmark::{Event, Parser, Tag};
use regex::Regex;
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

#[derive(Clone, Copy, PartialEq, ValueEnum)]
enum Mode {
    Paddle,
    Llm,
}

#[derive(ClapArgs)]
#[command(group(clap::ArgGroup::new("source").required(true).args(["input", "list", "directory"])))]
pub struct Args {
    #[arg(long, value_enum)]
    mode: Mode,
    #[arg(long, requires = "output")]
    input: Option<PathBuf>,
    #[arg(long, requires = "output_dir")]
    list: Option<PathBuf>,
    #[arg(long, requires = "output_dir")]
    directory: Option<PathBuf>,
    #[arg(long, requires = "input")]
    output: Option<PathBuf>,
    #[arg(long, conflicts_with = "input")]
    output_dir: Option<PathBuf>,
    #[arg(long)]
    plan: PathBuf,
    #[arg(long)]
    model: Option<String>,
    #[arg(long, value_parser = ["low", "medium", "high", "xhigh"])]
    effort: Option<String>,
    #[arg(long)]
    prompt_file: Option<PathBuf>,
    #[arg(long)]
    token_budget: Option<u64>,
    #[arg(long)]
    request_budget: Option<u64>,
    #[arg(long, default_value_t = 0)]
    fix: u64,
    #[arg(long, default_value_t = 3, value_parser = clap::value_parser!(u8).range(1..=100))]
    concurrency: u8,
    #[arg(long, default_value_t = 900.0)]
    max_seconds: f64,
    #[arg(long)]
    endpoint: Option<String>,
    #[arg(long)]
    selection: Option<PathBuf>,
    #[arg(long)]
    cache_dir: Option<PathBuf>,
    #[arg(long)]
    download: bool,
    #[arg(long, requires = "download")]
    download_list: Option<PathBuf>,
}

fn markdown_path(path: &Path) -> Result<PathBuf> {
    let path = path
        .canonicalize()
        .with_context(|| path.display().to_string())?;
    ensure!(
        path.is_file() && is_markdown(&path),
        "expected a Markdown file: {}",
        path.display()
    );
    Ok(path)
}

fn is_markdown(path: &Path) -> bool {
    path.extension()
        .is_some_and(|ext| ext.eq_ignore_ascii_case("md"))
}

fn input_outputs(args: &Args) -> Result<Vec<(PathBuf, PathBuf)>> {
    if let (Some(input), Some(output)) = (&args.input, &args.output) {
        return Ok(vec![(markdown_path(input)?, resolve(output)?)]);
    }
    let out = resolve(args.output_dir.as_deref().context("missing --output-dir")?)?;
    let mut pairs = Vec::new();
    if let Some(list) = &args.list {
        let list = list.canonicalize()?;
        let files: Vec<PathBuf> = serde_json::from_slice(&fs::read(&list)?)?;
        let root = list.parent().context("list has no parent directory")?;
        for file in files {
            let path = markdown_path(&root.join(file))?;
            let dest = resolve(&out.join(path.file_name().context("source has no basename")?))?;
            pairs.push((path, dest));
        }
    } else if let Some(dir) = &args.directory {
        let dir = dir.canonicalize()?;
        ensure!(
            dir.is_dir() && !out.starts_with(&dir),
            "output directory must be outside the input directory"
        );
        for entry in walkdir::WalkDir::new(&dir).follow_links(false) {
            let entry = entry?;
            if entry.file_type().is_file() && is_markdown(entry.path()) {
                let dest = resolve(&out.join(entry.path().strip_prefix(&dir)?))?;
                pairs.push((entry.into_path(), dest));
            }
        }
        pairs.sort();
    }
    Ok(pairs)
}

struct Scanner {
    images: Regex,
    ocr: Regex,
    scheme: Regex,
}

impl Scanner {
    fn new() -> Result<Self> {
        Ok(Self {
            images: Regex::new(
                r#"!\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))(?:\s+(?:"[^"\r\n]*"|'[^'\r\n]*'))?\)"#,
            )?,
            ocr: Regex::new(r"(?s)<!-- mdocr:ocr-begin\b.*?<!-- mdocr:ocr-end -->")?,
            scheme: Regex::new(r"^[A-Za-z][A-Za-z0-9+.-]*:")?,
        })
    }

    fn images(
        &self,
        text: &str,
        root: &Path,
        selected: Option<&HashSet<String>>,
    ) -> Result<Vec<Value>> {
        let masked = Parser::new(text)
            .into_offset_iter()
            .filter_map(|(event, range)| {
                matches!(
                    event,
                    Event::Start(Tag::CodeBlock(_) | Tag::HtmlBlock)
                        | Event::Code(_)
                        | Event::Html(_)
                        | Event::InlineHtml(_)
                )
                .then_some(range)
            })
            .chain(self.ocr.find_iter(text).map(|m| m.range()))
            .collect::<Vec<_>>();

        let mut output = Vec::new();
        let mut previous_byte = 0;
        let mut char_offset = 0;
        for capture in self.images.captures_iter(text) {
            let span = capture.get(0).context("missing image match")?;
            if masked.iter().any(|range| range.contains(&span.start())) {
                continue;
            }
            let raw = capture
                .get(1)
                .or_else(|| capture.get(2))
                .context("missing image target")?
                .as_str();
            let raw = raw.split('#').next().unwrap_or_default();
            let scheme = self
                .scheme
                .find(raw)
                .map(|s| s.as_str().to_ascii_lowercase());
            let remote = matches!(scheme.as_deref(), Some("http:" | "https:"));
            let unsupported = !remote && (scheme.is_some() || raw.starts_with('/'));
            let target = if remote || unsupported {
                raw.into()
            } else {
                percent_decode_str(raw).decode_utf8_lossy()
            };
            let mut source = None;
            let mut digest = None;
            let error = if unsupported {
                Some("UNSUPPORTED_IMAGE_TARGET")
            } else if remote {
                Some("REMOTE_IMAGE_NOT_DOWNLOADED")
            } else {
                let path = resolve(&root.join(target.as_ref()))?;
                let data = fs::read(&path);
                source = Some(path);
                match data {
                    Ok(data) => {
                        digest = Some(sha(&data));
                        None
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                        Some("IMAGE_NOT_FOUND")
                    }
                    Err(error) => return Err(error).context("cannot read planned local image"),
                }
            };
            char_offset += text[previous_byte..span.start()].chars().count();
            let start = char_offset;
            let end = start + span.as_str().chars().count();
            char_offset = end;
            previous_byte = span.end();
            let identity = sha(format!("{start}:{end}:{target}").as_bytes());
            output.push(json!({
                "id": format!("image-{}-{}", output.len() + 1, &identity[..12]),
                "resource_id": target,
                "original_link": source.as_ref().and_then(|p| p.to_str()).unwrap_or(&target),
                "start": start,
                "end": end,
                "source_path": source,
                "sha256": digest,
                "selected": selected.is_none_or(|set| set.contains(target.as_ref())),
                "error": error,
            }));
        }
        Ok(output)
    }
}

pub fn write(args: Args) -> Result<()> {
    ensure!(
        args.max_seconds.is_finite() && args.max_seconds >= 0.0,
        "deadline must be finite and nonnegative"
    );
    let llm = args.mode == Mode::Llm;
    ensure!(
        llm || [
            args.model.is_none(),
            args.effort.is_none(),
            args.prompt_file.is_none(),
            args.token_budget.is_none()
        ]
        .into_iter()
        .all(|x| x),
        "model, effort, prompt and token budget are only valid in llm mode"
    );
    ensure!(
        !llm || args.endpoint.is_none(),
        "--endpoint is only valid in paddle mode"
    );
    let mut settings = json!({
        "mode": if llm { "llm" } else { "paddle" },
        "model": llm.then(|| args.model.as_deref().filter(|s| !s.is_empty()).unwrap_or("openai/gpt-6.1-terra")),
        "effort": llm.then(|| args.effort.as_deref().unwrap_or("medium")),
        "prompt": match &args.prompt_file {
            Some(path) => fs::read_to_string(path)?,
            None => include_str!("../../../src/mdocr/default_prompt.txt").trim().into(),
        },
        "token_budget": args.token_budget, "request_budget": args.request_budget,
        "concurrency": args.concurrency, "fix": args.fix, "endpoint": args.endpoint,
        "max_seconds": args.max_seconds, "cache_dir": args.cache_dir.as_deref().map(resolve).transpose()?,
    });
    if llm {
        let rounds = std::env::var("MDOCR_MERMAID_REPAIR_ROUNDS").unwrap_or_else(|_| "3".into());
        ensure!(
            !rounds.is_empty() && rounds.bytes().all(|b| b.is_ascii_digit()),
            "invalid MDOCR_MERMAID_REPAIR_ROUNDS"
        );
        settings["llm_schema"] = json!("mdocr-image-content-1");
        settings["mermaid_repair_rounds"] = json!(rounds.parse::<u64>()?);
    }
    let pairs = input_outputs(&args)?;
    let plan = resolve(&args.plan)?;
    let mut protected = pairs
        .iter()
        .map(|(src, _)| src.clone())
        .collect::<HashSet<_>>();
    ensure!(protected.len() == pairs.len(), "duplicate input path");
    let download_sources: Option<HashSet<PathBuf>> = args
        .download_list
        .as_ref()
        .map(|path| serde_json::from_slice(&fs::read(path)?).context("invalid --download-list"))
        .transpose()?;
    if let Some(sources) = &download_sources {
        ensure!(
            sources.is_subset(&protected),
            "download list names an unknown input document"
        );
    }
    let selection: Option<HashMap<PathBuf, HashSet<String>>> = args
        .selection
        .as_ref()
        .map(|p| serde_json::from_slice(&fs::read(p)?).context("invalid --selection"))
        .transpose()?;
    if let Some(selection) = &selection {
        ensure!(
            selection.keys().all(|path| protected.contains(path)),
            "selection names an unknown input document"
        );
    }
    for path in [
        &args.list,
        &args.prompt_file,
        &args.selection,
        &args.download_list,
    ]
    .into_iter()
    .flatten()
    {
        protected.insert(resolve(path)?);
    }
    let scanner = Scanner::new()?;
    let mut docs = Vec::with_capacity(pairs.len());
    let mut destinations = HashSet::from([plan.clone()]);
    let empty = HashSet::new();
    for (path, output) in pairs {
        ensure!(
            destinations.insert(output.clone()),
            "output collision: {}",
            output.display()
        );
        let markdown = fs::read_to_string(&path)?;
        let images = scanner.images(
            &markdown,
            path.parent().context("missing source parent")?,
            selection
                .as_ref()
                .map(|map| map.get(&path).unwrap_or(&empty)),
        )?;
        protected.extend(
            images
                .iter()
                .filter_map(|img| img["source_path"].as_str())
                .map(PathBuf::from),
        );
        docs.push(json!({"document_id": path, "path": path, "sha256": sha(markdown.as_bytes()), "output": output, "images": images}));
    }
    for dest in &destinations {
        ensure!(
            !protected.contains(dest),
            "destination overlaps an input: {}",
            dest.display()
        );
        ensure!(
            !dest.try_exists()?,
            "destination already exists: {}",
            dest.display()
        );
    }
    if args.download {
        let assets = plan.with_file_name(format!(
            "{}.images",
            plan.file_name()
                .and_then(|name| name.to_str())
                .context("invalid plan filename")?
        ));
        ensure!(
            protected
                .iter()
                .chain(&destinations)
                .all(|path| !path.starts_with(&assets) && !assets.starts_with(path)),
            "image staging directory overlaps a planned input or destination"
        );
        let images = docs
            .iter_mut()
            .filter(|doc| {
                download_sources.as_ref().is_none_or(|sources| {
                    doc["path"]
                        .as_str()
                        .is_some_and(|path| sources.contains(Path::new(path)))
                })
            })
            .flat_map(|doc| doc["images"].as_array_mut().expect("planned image list"))
            .filter(|image| {
                image["selected"] == true && image["error"] == "REMOTE_IMAGE_NOT_DOWNLOADED"
            })
            .collect::<Vec<_>>();
        if !images.is_empty() {
            bind_downloads(images, &assets, llm)?;
        }
    }
    let mut protected = protected.into_iter().collect::<Vec<_>>();
    protected.sort();
    let data = json_bytes(
        &json!({"schema_version":"mdocr-plan-2", "settings":settings,
        "documents":docs, "protected_inputs":protected}),
    )?;
    atomic(&plan, &data, true)?;
    Ok(())
}

fn download_url(url: &reqwest::Url) -> bool {
    matches!(url.scheme(), "http" | "https")
        && url.username().is_empty()
        && url.password().is_none()
}

fn bind_downloads(images: Vec<&mut Value>, assets: &Path, llm: bool) -> Result<()> {
    use reqwest::{Url, blocking::Client, redirect::Policy};

    fs::create_dir_all(assets.parent().context("missing image staging parent")?)?;
    fs::create_dir(assets)?;
    let client = Client::builder()
        .timeout(Duration::from_secs(30))
        .redirect(Policy::custom(|attempt| {
            if !download_url(attempt.url()) || attempt.previous().len() >= 10 {
                attempt.stop()
            } else {
                attempt.follow()
            }
        }))
        .build()?;
    let mut downloads: HashMap<String, Value> = HashMap::new();
    for image in images {
        let link = image["resource_id"]
            .as_str()
            .context("missing remote image URL")?;
        if !downloads.contains_key(link) {
            let fields = match Url::parse(link) {
                Ok(url) if download_url(&url) => {
                    let extension = Path::new(url.path())
                        .extension()
                        .and_then(|ext| ext.to_str())
                        .unwrap_or_default()
                        .to_ascii_lowercase();
                    if !llm
                        && !matches!(
                            extension.as_str(),
                            "png" | "webp" | "gif" | "jpg" | "jpeg" | "tif" | "tiff"
                        )
                    {
                        json!({"error":"REMOTE_IMAGE_FORMAT_UNSUPPORTED"})
                    } else {
                        match client.get(url).send().and_then(|response| {
                            if response.status().is_success() {
                                response.bytes().map(Some)
                            } else {
                                Ok(None)
                            }
                        }) {
                            Ok(Some(bytes)) => {
                                let mut name = sha(link.as_bytes());
                                if !llm {
                                    name.push('.');
                                    name.push_str(&extension);
                                }
                                let path = assets.join(name);
                                atomic(&path, &bytes, true)?;
                                json!({"error":null, "source_path":path, "sha256":sha(&bytes)})
                            }
                            Ok(None) | Err(_) => json!({"error":"REMOTE_IMAGE_DOWNLOAD_FAILED"}),
                        }
                    }
                }
                _ => json!({"error":"REMOTE_IMAGE_DOWNLOAD_FAILED"}),
            };
            downloads.insert(link.into(), fields);
        }
        for (key, value) in downloads[link].as_object().expect("download fields") {
            image[key] = value.clone();
        }
    }
    Ok(())
}
