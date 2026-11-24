mod checkpoint;

use self::checkpoint::{DownloadCheckpoint, DownloadDocument, Outcome};
use crate::artifacts::{self, atomic, json_bytes, resolve, sha};
use crate::markdown::{self, Destination};
use crate::{config, progress, provider, runner, workspace::Workspace};
use anyhow::{Context, Result, bail, ensure};
use base64::{Engine, engine::general_purpose};
use clap::Args as ClapArgs;
use image::{ImageFormat, ImageReader};
use percent_encoding::percent_decode_str;
use reqwest::{Url, blocking::Client, header::LOCATION};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::{Cursor, ErrorKind, Read};
use std::path::{Component, Path, PathBuf};
use std::time::Duration;
use walkdir::WalkDir;

pub(crate) const FROZEN_SCHEMA: &str = "mdocr-frozen-plan-1";
pub(crate) const DOWNLOAD_SCHEMA: &str = "mdocr-download-1";
const WORK_SCHEMA: &str = "mdocr-work-1";
const MAX_IMAGE_BYTES: u64 = 64 * 1024 * 1024;

#[derive(ClapArgs)]
#[command(group(clap::ArgGroup::new("source").required(true).args(["input_single", "input_dir"])))]
pub(crate) struct DraftArgs {
    /// A Markdown document to add to the work plan.
    #[arg(long, requires = "output_single")]
    input_single: Option<PathBuf>,
    /// Output filename for the single document.
    #[arg(long, requires = "input_single")]
    output_single: Option<PathBuf>,
    /// Recursively enumerate Markdown documents in this directory.
    #[arg(long, requires = "output_dir")]
    input_dir: Option<PathBuf>,
    /// Destination directory, preserving paths relative to the input directory.
    #[arg(long, requires = "input_dir")]
    output_dir: Option<PathBuf>,
    /// Create this caller-owned work-plan JSON file.
    #[arg(long)]
    plan: PathBuf,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Work {
    schema_version: String,
    output_dir: PathBuf,
    documents: Vec<WorkDocument>,
}

impl Work {
    pub(crate) fn digest(&self) -> Result<String> {
        Ok(sha(&json_bytes(self)?))
    }

    pub(crate) fn outputs(&self) -> impl Iterator<Item = &Path> {
        self.documents
            .iter()
            .map(|doc| doc.output.as_deref().expect("normalized output"))
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct WorkDocument {
    path: PathBuf,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "optional"
    )]
    output: Option<PathBuf>,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "optional"
    )]
    select_images: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    download_remote: bool,
}

fn optional<'de, D: serde::Deserializer<'de>, T: Deserialize<'de>>(
    deserializer: D,
) -> std::result::Result<Option<T>, D::Error> {
    T::deserialize(deserializer).map(Some)
}

fn markdown_path(path: &Path) -> Result<PathBuf> {
    let path = path
        .canonicalize()
        .with_context(|| format!("read document {}", path.display()))?;
    ensure!(
        path.is_file() && is_markdown(&path),
        "expected a Markdown file: {}",
        path.display()
    );
    Ok(path)
}

fn is_markdown(path: &Path) -> bool {
    path.extension()
        .is_some_and(|extension| extension.eq_ignore_ascii_case("md"))
}

fn work_filename(path: &Path) -> Result<()> {
    ensure!(
        path.extension()
            .is_some_and(|extension| extension == "json"),
        "work plan filename must end in .json"
    );
    Ok(())
}

fn relative_to(base: &Path, path: &Path) -> PathBuf {
    if let Ok(relative) = path.strip_prefix(base) {
        return if relative.as_os_str().is_empty() {
            PathBuf::from(".")
        } else {
            relative.into()
        };
    }
    let common = base.ancestors().find(|ancestor| path.starts_with(ancestor));
    match common {
        Some(common) => {
            let mut relative = PathBuf::new();
            for _ in base.strip_prefix(common).expect("ancestor").components() {
                relative.push("..");
            }
            relative.push(path.strip_prefix(common).expect("shared ancestor"));
            relative
        }
        None => path.into(),
    }
}

pub(crate) fn draft(args: DraftArgs) -> Result<PathBuf> {
    let plan = resolve(&args.plan)?;
    work_filename(&plan)?;
    let base = plan
        .parent()
        .context("work plan needs a parent directory")?;
    let (output, mapping) = match (
        &args.input_single,
        &args.output_single,
        &args.input_dir,
        &args.output_dir,
    ) {
        (Some(input), Some(output), None, None) => {
            let input = markdown_path(input)?;
            let output = resolve(output)?;
            ensure!(
                input != output && plan != output,
                "output overlaps a source or work plan"
            );
            let leaf = output.file_name().context("output needs a filename")?;
            let parent = output.parent().context("output needs a parent directory")?;
            (parent.to_path_buf(), vec![(input, PathBuf::from(leaf))])
        }
        (None, None, Some(input), Some(output)) => {
            let input = input.canonicalize()?;
            let output = resolve(output)?;
            ensure!(
                input.is_dir() && output != input,
                "expected distinct input and output directories"
            );
            let mut entries = Vec::new();
            for entry in WalkDir::new(&input)
                .min_depth(1)
                .into_iter()
                .filter_entry(|entry| {
                    entry.path() != output
                        && !(entry.file_type().is_dir()
                            && entry.path().extension().is_some_and(|ext| ext == "mdocr"))
                })
            {
                let entry = entry?;
                if entry.file_type().is_file() && is_markdown(entry.path()) {
                    let relative = entry.path().strip_prefix(&input)?.to_path_buf();
                    entries.push((entry.into_path(), relative));
                }
            }
            entries.sort_unstable_by(|left, right| left.1.cmp(&right.1));
            (output, entries)
        }
        _ => bail!("select exactly one matching input and output shape"),
    };
    ensure!(!mapping.is_empty(), "input contains no Markdown documents");
    let work = Work {
        schema_version: WORK_SCHEMA.into(),
        output_dir: relative_to(base, &output),
        documents: mapping
            .into_iter()
            .map(|(path, output)| WorkDocument {
                path: relative_to(base, &path),
                output: Some(output),
                select_images: None,
                download_remote: false,
            })
            .collect(),
    };
    atomic(&plan, &json_bytes(&work)?, true)?;
    Ok(plan)
}

pub(crate) fn normalize(work: &Path) -> Result<Work> {
    work_filename(work)?;
    ensure!(work.is_file(), "work plan is not a file");
    let mut value: Work =
        serde_json::from_slice(&fs::read(work)?).context("invalid work plan JSON")?;
    ensure!(
        value.schema_version == WORK_SCHEMA,
        "unsupported work plan schema"
    );
    ensure!(
        !value.documents.is_empty(),
        "work plan requires at least one document"
    );
    ensure!(
        !value.output_dir.as_os_str().is_empty(),
        "output_dir must not be empty"
    );
    let base = work
        .parent()
        .context("work plan needs a parent directory")?;
    value.output_dir = resolve(&base.join(&value.output_dir))?;
    if value.output_dir.try_exists()? {
        ensure!(value.output_dir.is_dir(), "output_dir must be a directory");
    }
    let mut sources = HashSet::new();
    let mut destinations = HashSet::new();
    for document in &mut value.documents {
        let path = markdown_path(&base.join(&document.path))?;
        ensure!(
            sources.insert(path.clone()),
            "duplicate document input: {}",
            path.display()
        );
        document.path = path;
        let relative = document.output.clone().unwrap_or_else(|| {
            document
                .path
                .file_name()
                .expect("source has a filename")
                .into()
        });
        ensure!(
            relative.file_name().is_some()
                && relative
                    .components()
                    .all(|part| matches!(part, Component::Normal(_) | Component::CurDir)),
            "document output must be a relative file path inside output_dir"
        );
        let output = resolve(&value.output_dir.join(relative))?;
        ensure!(
            output != value.output_dir && output.starts_with(&value.output_dir),
            "document output escapes output_dir"
        );
        ensure!(
            destinations.insert(output.clone()),
            "output collision: {}",
            output.display()
        );
        document.output = Some(output);
        if let Some(selection) = &mut document.select_images {
            selection.sort_unstable();
            selection.dedup();
        }
    }
    Ok(value)
}

struct PlannedDocument {
    path: PathBuf,
    output: PathBuf,
    digest: String,
    images: Vec<PendingImage>,
}

struct PendingImage {
    occurrence: markdown::Occurrence,
    disposition: Disposition,
    selected: bool,
}

enum Disposition {
    Local(PathBuf),
    Remote(Url),
    Inline,
    Failed(&'static str),
}

fn inputs(
    work: Work,
    workspace: &Path,
    protected: &mut HashSet<PathBuf>,
) -> Result<Vec<PlannedDocument>> {
    let mut planned = Vec::with_capacity(work.documents.len());
    for doc in work.documents {
        ensure!(
            !doc.path.starts_with(workspace),
            "source overlaps the execution workspace"
        );
        protected.insert(doc.path.clone());
        let source = fs::read_to_string(&doc.path)
            .with_context(|| format!("read Markdown source {}", doc.path.display()))?;
        let entries = markdown::occurrences(&source);
        let keys = entries
            .iter()
            .map(|entry| entry.target.as_str())
            .collect::<HashSet<_>>();
        if let Some(selection) = &doc.select_images {
            ensure!(
                selection.iter().all(|key| keys.contains(key.as_str())),
                "work plan selects an unknown image in {}",
                doc.path.display()
            );
        }
        let mut images = Vec::with_capacity(entries.len());
        for occurrence in entries {
            let selected = doc
                .select_images
                .as_ref()
                .is_none_or(|list| list.binary_search(&occurrence.target).is_ok());
            let disposition = match markdown::destination(&occurrence.target) {
                Destination::Local(relative) => {
                    let path = doc
                        .path
                        .parent()
                        .expect("source has a parent")
                        .join(relative);
                    match resolve(&path) {
                        Ok(path) => {
                            ensure!(
                                !path.starts_with(workspace),
                                "local image overlaps the execution workspace"
                            );
                            protected.insert(path.clone());
                            Disposition::Local(path)
                        }
                        Err(_) => Disposition::Failed("IMAGE_UNREADABLE"),
                    }
                }
                Destination::Remote(url) if doc.download_remote => Disposition::Remote(url),
                Destination::Remote(_) => Disposition::Failed("REMOTE_IMAGE_NOT_DOWNLOADED"),
                Destination::Inline => Disposition::Inline,
                Destination::Unsupported => Disposition::Failed("UNSUPPORTED_IMAGE_TARGET"),
            };
            images.push(PendingImage {
                occurrence,
                disposition,
                selected,
            });
        }
        planned.push(PlannedDocument {
            path: doc.path,
            output: doc.output.context("output was not resolved")?,
            digest: sha(source.as_bytes()),
            images,
        });
    }
    Ok(planned)
}

fn check_paths(
    workspace: &Path,
    docs: &[PlannedDocument],
    protected: &mut HashSet<PathBuf>,
    settings: &Value,
) -> Result<()> {
    let cache = settings["cache_dir"].as_str().map(Path::new);
    for key in ["prompt_file", "ca_file", "token_file", "oauth_file"] {
        if let Some(path) = settings[key].as_str() {
            protected.insert(resolve(Path::new(path))?);
        }
    }
    if let Some(cache) = cache {
        ensure!(
            !cache.starts_with(workspace) && !workspace.starts_with(cache),
            "OCR cache overlaps the execution workspace"
        );
    }
    let outputs = docs.iter().map(|doc| &doc.output).collect::<HashSet<_>>();
    for path in protected.iter().chain(outputs.iter().copied()) {
        ensure!(
            !path.starts_with(workspace) && !workspace.starts_with(path),
            "output or input overlaps the execution workspace: {}",
            path.display()
        );
        if let Some(cache) = cache {
            ensure!(
                !path.starts_with(cache) && !cache.starts_with(path),
                "output or input overlaps the OCR cache: {}",
                path.display()
            );
        }
    }
    for path in outputs.iter().copied() {
        ensure!(
            !protected
                .iter()
                .any(|input| path.starts_with(input) || input.starts_with(path)),
            "output overlaps an input: {}",
            path.display()
        );
        ensure!(
            !path
                .ancestors()
                .skip(1)
                .any(|parent| outputs.iter().any(|output| *output == parent)),
            "output nested under another output: {}",
            path.display()
        );
        ensure!(
            !path.try_exists()?,
            "output already exists: {}",
            path.display()
        );
    }
    Ok(())
}

#[derive(Clone)]
struct Binding {
    source: PathBuf,
    link: String,
    digest: String,
}

type BindingResult = std::result::Result<Binding, &'static str>;

struct Staging<'a> {
    images: &'a Path,
    settings: &'a Value,
    http: Option<Client>,
    remote: HashMap<Url, BindingResult>,
    local: HashMap<PathBuf, BindingResult>,
}

impl<'a> Staging<'a> {
    fn new(images: &'a Path, settings: &'a Value) -> Self {
        Self {
            images,
            settings,
            http: None,
            remote: HashMap::new(),
            local: HashMap::new(),
        }
    }

    fn http(&mut self) -> Result<&Client> {
        if self.http.is_none() {
            self.http = Some(provider::client_for(self.settings)?);
        }
        Ok(self.http.as_ref().expect("download client initialized"))
    }

    fn saved_binding(
        &self,
        image: &PendingImage,
        outcome: Option<&Outcome>,
    ) -> Result<Option<Binding>> {
        let Some(Outcome::Success {
            source_path,
            sha256,
        }) = outcome
        else {
            return Ok(None);
        };
        let bytes = match &image.disposition {
            Disposition::Local(path) if path == source_path => read_image(path).ok(),
            Disposition::Remote(_) | Disposition::Inline
                if source_path.parent() == Some(self.images) =>
            {
                read_staged(source_path)?
            }
            _ => return Ok(None),
        };
        let Some(bytes) = bytes.filter(|data| sha(data) == *sha256) else {
            return Ok(None);
        };
        let Ok((extension, _)) = image_extension(&bytes, self.settings["mode"] == "llm") else {
            return Ok(None);
        };
        let staged = matches!(
            image.disposition,
            Disposition::Remote(_) | Disposition::Inline
        );
        if staged && *source_path != self.images.join(format!("{sha256}.{extension}")) {
            return Ok(None);
        }
        let link = match &image.disposition {
            Disposition::Remote(url) => url.as_str(),
            _ => source_path
                .to_str()
                .context("invalid image path in download checkpoint")?,
        };
        Ok(Some(Binding {
            source: source_path.clone(),
            link: link.into(),
            digest: sha256.clone(),
        }))
    }

    fn bind(&mut self, image: &PendingImage) -> Result<BindingResult> {
        let llm = self.settings["mode"] == "llm";
        let value = match &image.disposition {
            Disposition::Local(path) => self
                .local
                .entry(path.clone())
                .or_insert_with(|| {
                    let bytes = read_image(path)?;
                    image_extension(&bytes, llm)?;
                    Ok(Binding {
                        source: path.clone(),
                        link: path.to_str().ok_or("IMAGE_UNREADABLE")?.into(),
                        digest: sha(&bytes),
                    })
                })
                .clone(),
            Disposition::Remote(url) => match self.remote.get(url) {
                Some(binding) => binding.clone(),
                None => {
                    let binding = match download(self.http()?, url) {
                        Ok(bytes) => self.stage(&bytes, Some(url.as_str()), None, None)?,
                        Err(reason) => Err(reason),
                    };
                    self.remote.insert(url.clone(), binding.clone());
                    binding
                }
            },
            Disposition::Inline => match decode_data(&image.occurrence.target) {
                Ok((mime, bytes)) => self.stage(&bytes, None, Some(mime), None)?,
                Err(reason) => Err(reason),
            },
            Disposition::Failed(reason) => Err(*reason),
        };
        Ok(value)
    }

    fn stage(
        &self,
        bytes: &[u8],
        link: Option<&str>,
        mime: Option<&str>,
        frozen: Option<(&Path, &str)>,
    ) -> Result<BindingResult> {
        let (extension, content_type) = match image_extension(bytes, self.settings["mode"] == "llm")
        {
            Ok(format) => format,
            Err(reason) => return Ok(Err(reason)),
        };
        if mime.is_some_and(|declared| !declared.eq_ignore_ascii_case(content_type)) {
            return Ok(Err("IMAGE_MIME_MISMATCH"));
        }
        let digest = sha(bytes);
        let source = self.images.join(format!("{digest}.{extension}"));
        if let Some((path, expected)) = frozen {
            ensure!(
                source == path && digest == expected,
                "IMAGE_DRIFT: replacement does not match frozen image {}",
                path.display()
            );
        }
        if read_staged(&source)?.is_none_or(|previous| previous != bytes) {
            atomic(&source, bytes, !source.try_exists()?)?;
        }
        let link = link
            .unwrap_or(source.to_str().context("invalid staged image path")?)
            .into();
        Ok(Ok(Binding {
            source,
            link,
            digest,
        }))
    }
}

fn restore_staged(plan: &artifacts::Plan, request: &Work, images: &Path) -> Result<()> {
    ensure!(
        plan.documents.len() == request.documents.len(),
        "WORK_PLAN_CHANGED: frozen document list does not match the work plan"
    );
    let mut repairs = Vec::new();
    for (doc, work_doc) in plan.documents.iter().zip(&request.documents) {
        ensure!(
            doc.path == work_doc.path && Some(&doc.output) == work_doc.output.as_ref(),
            "WORK_PLAN_CHANGED: frozen document paths do not match the work plan"
        );
        for (image, occurrence) in doc.images.iter().zip(markdown::occurrences(&doc.text)) {
            let selected = work_doc
                .select_images
                .as_ref()
                .is_none_or(|list| list.binary_search(&occurrence.target).is_ok());
            ensure!(
                selected == image.selected,
                "WORK_PLAN_CHANGED: frozen image selection does not match the work plan"
            );
            let (Some(path), Some(digest)) = (&image.source_path, &image.sha256) else {
                continue;
            };
            if path.parent() != Some(images) {
                continue;
            }
            let destination = markdown::destination(&occurrence.target);
            match destination {
                Destination::Remote(_) if work_doc.download_remote => {}
                Destination::Inline => {}
                _ => bail!("frozen image does not have staging permission"),
            }
            if read_staged(path)?.is_none_or(|bytes| sha(&bytes) != *digest) {
                repairs.push((path, digest, occurrence.target, destination));
            }
        }
    }
    let mut staging = Staging::new(images, &plan.settings);
    for (path, digest, target, destination) in repairs {
        // Different occurrences can share a content-addressed staged file.
        if read_staged(path)?.is_some_and(|bytes| sha(&bytes) == *digest) {
            continue;
        }
        let (mime, bytes) = match destination {
            Destination::Remote(url) => download(staging.http()?, &url).map(|bytes| (None, bytes)),
            Destination::Inline => decode_data(&target).map(|(mime, bytes)| (Some(mime), bytes)),
            _ => unreachable!("repairs contain only staged images"),
        }
        .map_err(|reason| anyhow::anyhow!("IMAGE_DRIFT: {}: {reason}", path.display()))?;
        staging
            .stage(&bytes, None, mime, Some((path, digest)))?
            .map_err(|reason| anyhow::anyhow!("IMAGE_DRIFT: {}: {reason}", path.display()))?;
    }
    Ok(())
}

fn read_staged(path: &Path) -> Result<Option<Vec<u8>>> {
    artifacts::owned_path(path)?;
    match read_image(path) {
        Ok(bytes) => Ok(Some(bytes)),
        Err("IMAGE_NOT_FOUND" | "IMAGE_TOO_LARGE") => Ok(None),
        Err(reason) => bail!("{reason}: {}", path.display()),
    }
}

fn read_image(path: &Path) -> std::result::Result<Vec<u8>, &'static str> {
    let metadata = fs::metadata(path).map_err(|error| match error.kind() {
        ErrorKind::NotFound => "IMAGE_NOT_FOUND",
        _ => "IMAGE_UNREADABLE",
    })?;
    if !metadata.is_file() {
        return Err("IMAGE_UNREADABLE");
    }
    if metadata.len() > MAX_IMAGE_BYTES {
        return Err("IMAGE_TOO_LARGE");
    }
    let file = File::open(path).map_err(|_| "IMAGE_UNREADABLE")?;
    bounded_bytes(file, "IMAGE_UNREADABLE")
}

fn bounded_bytes(
    input: impl Read,
    read_error: &'static str,
) -> std::result::Result<Vec<u8>, &'static str> {
    let mut output = Vec::new();
    input
        .take(MAX_IMAGE_BYTES + 1)
        .read_to_end(&mut output)
        .map_err(|_| read_error)?;
    if output.len() as u64 > MAX_IMAGE_BYTES {
        return Err("IMAGE_TOO_LARGE");
    }
    Ok(output)
}

fn decode_data(target: &str) -> std::result::Result<(&str, Vec<u8>), &'static str> {
    let invalid = "INVALID_IMAGE_DATA_URL";
    let (_, body) = target.split_once(':').ok_or(invalid)?;
    let (header, payload) = body.split_once(',').ok_or(invalid)?;
    let (mime, encoding) = header.split_once(';').ok_or(invalid)?;
    if !mime
        .get(..6)
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case("image/"))
        || !encoding.eq_ignore_ascii_case("base64")
        || payload.is_empty()
    {
        return Err(invalid);
    }
    if payload.len() as u64 > MAX_IMAGE_BYTES * 4 + 4 {
        return Err("IMAGE_TOO_LARGE");
    }
    let decoded_url = percent_decode_str(payload).collect::<Vec<_>>();
    let bytes = general_purpose::STANDARD
        .decode(&decoded_url)
        .or_else(|_| general_purpose::STANDARD_NO_PAD.decode(&decoded_url))
        .map_err(|_| invalid)?;
    if bytes.len() as u64 > MAX_IMAGE_BYTES {
        return Err("IMAGE_TOO_LARGE");
    }
    Ok((mime, bytes))
}

fn image_extension(
    bytes: &[u8],
    llm: bool,
) -> std::result::Result<(&'static str, &'static str), &'static str> {
    let mut reader = ImageReader::new(Cursor::new(bytes))
        .with_guessed_format()
        .map_err(|_| "INVALID_IMAGE")?;
    if let Some(format) = reader.format() {
        let mut limits = image::Limits::default();
        limits.max_image_width = Some(32_768);
        limits.max_image_height = Some(32_768);
        reader.limits(limits);
        reader.decode().map_err(|_| "INVALID_IMAGE")?;
        let format = match format {
            ImageFormat::Ico => ("ico", "image/x-icon"),
            _ => (format.extensions_str()[0], format.to_mime_type()),
        };
        return Ok(format);
    }
    let source = std::str::from_utf8(bytes).map_err(|_| "INVALID_IMAGE")?;
    let xml = roxmltree::Document::parse(source).map_err(|_| "INVALID_IMAGE")?;
    let root = xml.root_element();
    if root.has_tag_name("svg")
        && matches!(
            root.tag_name().namespace(),
            None | Some("http://www.w3.org/2000/svg")
        )
    {
        if !llm {
            return Err("UNSUPPORTED_IMAGE_FORMAT");
        }
        if root
            .descendants()
            .any(|node| node.has_tag_name("foreignObject"))
        {
            return Err("svg_text_renderer_unavailable");
        }
        return Ok(("svg", "image/svg+xml"));
    }
    Err("INVALID_IMAGE")
}

fn download(client: &Client, url: &Url) -> std::result::Result<Vec<u8>, &'static str> {
    let mut current = url.clone();
    let failed = "REMOTE_IMAGE_DOWNLOAD_FAILED";
    for _ in 0..=8 {
        let response = client
            .get(current.clone())
            .timeout(Duration::from_secs(30))
            .send()
            .map_err(|_| failed)?;
        if response.status().is_success() {
            if response
                .content_length()
                .is_some_and(|size| size > MAX_IMAGE_BYTES)
            {
                return Err("IMAGE_TOO_LARGE");
            }
            return bounded_bytes(response, failed);
        }
        if !response.status().is_redirection() {
            return Err(failed);
        }
        let location = response
            .headers()
            .get(LOCATION)
            .ok_or(failed)?
            .to_str()
            .map_err(|_| failed)?;
        let next = current.join(location).map_err(|_| failed)?;
        if !matches!(next.scheme(), "http" | "https")
            || !next.username().is_empty()
            || next.password().is_some()
        {
            return Err(failed);
        }
        current = next;
    }
    Err(failed)
}

fn planned_json(
    doc: PlannedDocument,
    staging: &mut Staging<'_>,
    checkpoint: &mut DownloadCheckpoint,
    position: usize,
    download_path: &Path,
) -> Result<Value> {
    let mut selected_index = 0;
    let mut images = Vec::with_capacity(doc.images.len());
    for (index, image) in doc.images.into_iter().enumerate() {
        let resource = image.occurrence.resource_id();
        let mut value = json!({
            "id": format!("image-{}", index + 1),
            "resource_id": resource,
            "span": [image.occurrence.span.start, image.occurrence.span.end],
            "edit": [image.occurrence.edit.start, image.occurrence.edit.end],
            "selected": image.selected, "source_path": null,
            "original_link": null, "sha256": null, "error": null,
        });
        if image.selected {
            let record = &mut checkpoint.documents[position].images[selected_index];
            let outcome = match staging.saved_binding(&image, record.outcome.as_ref())? {
                Some(binding) => Ok(binding),
                None => staging.bind(&image)?,
            };
            record.outcome = Some(match outcome {
                Ok(binding) => {
                    value["source_path"] = json!(&binding.source);
                    value["original_link"] = json!(binding.link);
                    value["sha256"] = json!(&binding.digest);
                    Outcome::Success {
                        source_path: binding.source,
                        sha256: binding.digest,
                    }
                }
                Err(reason) => {
                    value["error"] = json!(reason);
                    Outcome::Failure {
                        reason_code: reason.into(),
                    }
                }
            });
            selected_index += 1;
            atomic(download_path, &json_bytes(checkpoint)?, false)?;
            progress::emit(
                "image_checkpoint",
                format_args!(
                    "phase=accept document={} image={} verified={}",
                    position + 1,
                    index + 1,
                    value["error"].is_null()
                ),
            );
        }
        images.push(value);
    }
    Ok(
        json!({"document_id": doc.path, "path": doc.path, "output": doc.output,
        "sha256": doc.digest, "images": images}),
    )
}

fn summary(plan: &Value, frozen: &Path, images: &Path) -> Result<Value> {
    let documents = plan["documents"]
        .as_array()
        .context("invalid frozen document list")?;
    let mut selected = 0;
    let mut unselected = 0;
    let mut staged = 0;
    let mut failed = 0;
    for doc in documents {
        for image in doc["images"]
            .as_array()
            .context("invalid frozen image list")?
        {
            if image["selected"] == true {
                selected += 1;
                failed += usize::from(!image["error"].is_null());
                staged += usize::from(
                    image["source_path"]
                        .as_str()
                        .is_some_and(|path| Path::new(path).starts_with(images)),
                );
            } else {
                unselected += 1;
            }
        }
    }
    Ok(
        json!({"status":"accepted", "frozen_plan_path":frozen, "counts":{
            "documents":documents.len(), "selected":selected, "unselected":unselected,
            "staged":staged, "failed":failed,
        }}),
    )
}

pub(crate) fn accept(
    workspace: &Workspace,
    explicit: Option<&Path>,
    repair_canceled: bool,
) -> Result<Value> {
    let work = &workspace.work;
    let frozen = &workspace.plan;
    let images = &workspace.images;
    let download_path = &workspace.download;
    for path in [work, frozen, images, download_path] {
        artifacts::owned_path(path)?;
    }
    let request = normalize(work)?;
    let work_digest = request.digest()?;
    workspace.verify_reset_binding(&work_digest)?;
    match fs::read(frozen) {
        Ok(bytes) => {
            let previous: Value = serde_json::from_slice(&bytes).context("invalid frozen plan")?;
            ensure!(
                previous["schema_version"] == FROZEN_SCHEMA,
                "unsupported frozen plan schema"
            );
            ensure!(
                previous["work_path"] == json!(work) && previous["work_sha256"] == work_digest,
                "WORK_PLAN_CHANGED: workspace is bound to a different work plan"
            );
            if let Some(explicit) = explicit {
                ensure!(
                    config::load(None, Some(explicit))? == previous["settings"],
                    "CONFIG_CHANGED: workspace is bound to a different effective policy"
                );
            }
            let strict_load = || {
                artifacts::load_plan(
                    frozen,
                    &workspace.result,
                    &workspace.journal,
                    &workspace.evidence,
                    &[],
                )
            };
            if let Err(error) = strict_load() {
                let digest = sha(&bytes);
                if !repair_canceled && runner::frozen_batch_canceled(workspace, &digest)? {
                    return Err(error);
                }
                let plan = artifacts::load_plan_for_staged_repair(
                    frozen,
                    &workspace.result,
                    &workspace.journal,
                    &workspace.evidence,
                )?;
                ensure!(plan.digest == digest, "PLAN_DRIFT: frozen plan changed");
                restore_staged(&plan, &request, images)?;
                strict_load()?;
            }
            return summary(&previous, frozen, images);
        }
        Err(error) if error.kind() == ErrorKind::NotFound => {}
        Err(error) => return Err(error).context("read frozen plan"),
    }
    let saved = DownloadCheckpoint::read(download_path, work, &work_digest)?;
    let (settings, config_source) = if let Some(checkpoint) = &saved {
        if let Some(explicit) = explicit {
            ensure!(
                config::load(None, Some(explicit))? == checkpoint.settings,
                "CONFIG_CHANGED: download checkpoint is bound to a different effective policy"
            );
        }
        (
            checkpoint.settings.clone(),
            checkpoint.config_source.clone(),
        )
    } else {
        let source = config::source(Some(work), explicit)?;
        let settings = config::load(None, source.as_deref())?;
        provider::ca_certificates(&settings)?;
        (settings, source)
    };
    let mut protected = HashSet::from([work.to_path_buf()]);
    if let Some(path) = &config_source {
        ensure!(
            path.is_absolute(),
            "invalid config source in download checkpoint"
        );
        artifacts::owned_path(path)?;
        protected.insert(path.clone());
    }
    let docs = inputs(request, &workspace.root, &mut protected)?;
    check_paths(&workspace.root, &docs, &mut protected, &settings)?;
    let bindings = docs
        .iter()
        .map(DownloadDocument::from_input)
        .collect::<Vec<_>>();
    let mut checkpoint = match saved {
        Some(checkpoint) => {
            checkpoint.verify_inputs(&bindings)?;
            checkpoint
        }
        None => DownloadCheckpoint::create(
            download_path,
            work.clone(),
            work_digest.clone(),
            settings.clone(),
            config_source,
            bindings,
        )?,
    };
    let mut staging = Staging::new(images, &settings);
    let documents = docs
        .into_iter()
        .enumerate()
        .map(|(position, doc)| {
            planned_json(doc, &mut staging, &mut checkpoint, position, download_path)
        })
        .collect::<Result<Vec<_>>>()?;
    checkpoint.verify_files()?;
    let mut protected = protected.into_iter().collect::<Vec<_>>();
    protected.sort_unstable();
    let plan = json!({"schema_version":FROZEN_SCHEMA, "work_path": work, "work_sha256":work_digest,
        "settings":settings, "documents":documents, "protected_inputs":protected});
    atomic(frozen, &json_bytes(&plan)?, true)?;
    summary(&plan, frozen, images)
}
