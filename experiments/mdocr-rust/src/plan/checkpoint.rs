use super::{Disposition, PlannedDocument, read_image};
use crate::artifacts::{atomic, json_bytes, owned_path, read_bound, sha, validate_settings};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};

const SCHEMA: &str = "mdocr-download-1";

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct DownloadCheckpoint {
    schema_version: String,
    work_path: PathBuf,
    work_sha256: String,
    pub(super) settings: Value,
    pub(super) config_source: Option<PathBuf>,
    pub(super) documents: Vec<DownloadDocument>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct DownloadDocument {
    path: PathBuf,
    sha256: String,
    pub(super) images: Vec<DownloadImage>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct DownloadImage {
    id: String,
    source_id: String,
    pub(super) outcome: Option<Outcome>,
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub(super) enum Outcome {
    Success {
        source_path: PathBuf,
        sha256: String,
    },
    Failure {
        reason_code: String,
    },
}

impl DownloadDocument {
    pub(super) fn from_input(doc: &PlannedDocument) -> Self {
        Self {
            path: doc.path.clone(),
            sha256: doc.digest.clone(),
            images: doc
                .images
                .iter()
                .enumerate()
                .filter(|(_, image)| image.selected)
                .map(|(index, image)| DownloadImage {
                    id: format!("image-{}", index + 1),
                    source_id: sha(match &image.disposition {
                        Disposition::Remote(url) => url.as_str().as_bytes(),
                        _ => image.occurrence.target.as_bytes(),
                    }),
                    outcome: None,
                })
                .collect(),
        }
    }
}

impl DownloadCheckpoint {
    pub(super) fn read(path: &Path, work: &Path, digest: &str) -> Result<Option<Self>> {
        owned_path(path)?;
        let checkpoint: Self = match fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).context("invalid download checkpoint")?,
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error).context("read download checkpoint"),
        };
        ensure!(
            checkpoint.schema_version == SCHEMA,
            "invalid download checkpoint schema"
        );
        ensure!(
            checkpoint.work_path == work && checkpoint.work_sha256 == digest,
            "WORK_PLAN_CHANGED: download checkpoint is bound to a different work plan"
        );
        for image in checkpoint.documents.iter().flat_map(|doc| &doc.images) {
            match &image.outcome {
                Some(Outcome::Success {
                    source_path,
                    sha256,
                }) => ensure!(
                    source_path.is_absolute()
                        && sha256.len() == 64
                        && sha256
                            .bytes()
                            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)),
                    "invalid successful download checkpoint record"
                ),
                Some(Outcome::Failure { reason_code }) => ensure!(
                    !reason_code.is_empty(),
                    "invalid failed download checkpoint record"
                ),
                None => {}
            }
        }
        validate_settings(&checkpoint.settings)?;
        Ok(Some(checkpoint))
    }

    pub(super) fn create(
        path: &Path,
        work_path: PathBuf,
        work_sha256: String,
        settings: Value,
        config_source: Option<PathBuf>,
        documents: Vec<DownloadDocument>,
    ) -> Result<Self> {
        let initial = Self {
            schema_version: SCHEMA.into(),
            work_path,
            work_sha256,
            settings,
            config_source,
            documents,
        };
        atomic(path, &json_bytes(&initial)?, true)?;
        Ok(initial)
    }

    pub(super) fn verify_inputs(&self, requested: &[DownloadDocument]) -> Result<()> {
        ensure!(
            self.documents.len() == requested.len()
                && self
                    .documents
                    .iter()
                    .zip(requested)
                    .all(|(saved, current)| {
                        saved.path == current.path
                            && saved.sha256 == current.sha256
                            && saved.images.len() == current.images.len()
                            && saved
                                .images
                                .iter()
                                .zip(&current.images)
                                .all(|(left, right)| {
                                    left.id == right.id && left.source_id == right.source_id
                                })
                    }),
            "SOURCE_DRIFT: sources or selected occurrences changed during staging"
        );
        Ok(())
    }

    pub(super) fn verify_files(&self) -> Result<()> {
        for document in &self.documents {
            read_bound(&document.path, &document.sha256, "SOURCE_DRIFT")?;
            for image in &document.images {
                if let Outcome::Success {
                    source_path,
                    sha256,
                } = image
                    .outcome
                    .as_ref()
                    .context("selected image has no checkpoint result")?
                {
                    owned_path(source_path)?;
                    let bytes = read_image(source_path).map_err(|reason| {
                        anyhow::anyhow!("IMAGE_DRIFT: {}: {reason}", source_path.display())
                    })?;
                    ensure!(
                        sha(&bytes) == *sha256,
                        "IMAGE_DRIFT: {}",
                        source_path.display()
                    );
                }
            }
        }
        Ok(())
    }
}
