use super::checkpoint::{DownloadCheckpoint, Outcome};
use super::{Disposition, MAX_IMAGE_BYTES, PendingImage, Work};
use crate::artifacts::{self, atomic, sha};
use crate::markdown::{self, Destination};
use crate::{progress, provider};
use anyhow::{Context, Result, bail, ensure};
use base64::{Engine, engine::general_purpose};
use image::{ImageFormat, ImageReader};
use percent_encoding::percent_decode_str;
use reqwest::{Url, blocking::Client, header::LOCATION};
use serde_json::Value;
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{Cursor, ErrorKind, Read};
use std::path::{Path, PathBuf};
use std::time::Duration;

#[derive(Clone)]
pub(super) struct Binding {
    pub(super) source: PathBuf,
    pub(super) link: String,
    pub(super) digest: String,
}

type BindingResult = std::result::Result<Binding, &'static str>;

pub(super) struct Staging<'a> {
    images: &'a Path,
    settings: &'a Value,
    http: Option<Client>,
    remote: HashMap<Url, BindingResult>,
    local: HashMap<PathBuf, BindingResult>,
}

impl<'a> Staging<'a> {
    pub(super) fn new(images: &'a Path, settings: &'a Value) -> Self {
        Self {
            images,
            settings,
            http: None,
            remote: HashMap::new(),
            local: HashMap::new(),
        }
    }

    pub(super) fn bind_recorded(
        &mut self,
        image: &PendingImage,
        checkpoint: &mut DownloadCheckpoint,
        position: usize,
        selected: usize,
        path: &Path,
    ) -> Result<BindingResult> {
        if let Some(binding) = self.saved_binding(image, checkpoint.outcome(position, selected))? {
            match &image.disposition {
                Disposition::Remote(url) => {
                    self.remote.insert(url.clone(), Ok(binding.clone()));
                }
                Disposition::Local(path) => {
                    self.local.insert(path.clone(), Ok(binding.clone()));
                }
                _ => {}
            }
            return Ok(Ok(binding));
        }
        let result = self.bind(image)?;
        let outcome = match &result {
            Ok(binding) => Outcome::Success {
                source_path: binding.source.clone(),
                sha256: binding.digest.clone(),
            },
            Err(reason) => Outcome::Failure {
                reason_code: (*reason).into(),
            },
        };
        checkpoint.record(path, position, selected, outcome)?;
        Ok(result)
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

pub(super) fn restore_staged(plan: &artifacts::Plan, request: &Work, images: &Path) -> Result<()> {
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

pub(super) fn read_image(path: &Path) -> std::result::Result<Vec<u8>, &'static str> {
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
    std::thread::scope(|scope| {
        let (finished, waiting) = std::sync::mpsc::channel::<()>();
        scope.spawn(move || {
            while let Err(std::sync::mpsc::RecvTimeoutError::Timeout) =
                waiting.recv_timeout(Duration::from_secs(5))
            {
                progress::emit("download_waiting", format_args!("phase=accept in_flight=1"));
            }
        });
        let result = fetch_image(client, url);
        drop(finished);
        result
    })
}

fn fetch_image(client: &Client, url: &Url) -> std::result::Result<Vec<u8>, &'static str> {
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
