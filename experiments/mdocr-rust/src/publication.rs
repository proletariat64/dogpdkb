use crate::artifacts::{owned_path, sha};
use anyhow::{Context, Result, ensure};
use rustix::fs::{CWD, RenameFlags, XattrFlags, fgetxattr, fsetxattr, renameat_with};
use rustix::io::Errno;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

const OWNER_ATTRIBUTE: &str = "user.mdocrr.publication";

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
pub(crate) struct PublicationReceipt {
    pub(crate) path: PathBuf,
    pub(crate) sha256: String,
    id: String,
}

impl PublicationReceipt {
    pub(crate) fn new(path: PathBuf, digest: String) -> Result<Self> {
        let mut random = [0; 32];
        getrandom::fill(&mut random)
            .map_err(|error| anyhow::anyhow!("cannot generate publication identity: {error}"))?;
        Ok(Self {
            path,
            sha256: digest,
            id: sha(&random),
        })
    }

    pub(crate) fn validate(&self) -> Result<()> {
        ensure!(
            self.path.is_absolute()
                && self.id.len() == 64
                && self
                    .id
                    .bytes()
                    .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')),
            "invalid publication receipt identity"
        );
        Ok(())
    }

    fn stage(&self) -> Result<PathBuf> {
        self.validate()?;
        let stage = self
            .path
            .with_file_name(format!(".mdocrr-stage-{}", self.id));
        ensure!(stage != self.path, "staging path equals the output path");
        Ok(stage)
    }

    fn stamp(&self, file: &File) -> Result<String> {
        Ok(format!("{}:{}", self.id, file.metadata()?.ino()))
    }

    pub(crate) fn verify_output_if_present(&self) -> Result<bool> {
        self.validate()?;
        let Some(mut file) = open_regular(&self.path)? else {
            return Ok(false);
        };
        ensure!(
            has_stamp(&file, &self.stamp(&file)?)? == Some(true),
            "preexisting unowned document output: {}",
            self.path.display()
        );
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes)?;
        ensure!(
            sha(&bytes) == self.sha256,
            "OCR artifact has drifted: {}",
            self.path.display()
        );
        Ok(true)
    }

    // The caller holds the destination directory lock throughout this operation.
    pub(crate) fn write_output(&self, bytes: &[u8], directory: &File) -> Result<()> {
        if !self.verify_output_if_present()? {
            self.remove_stage()?;
            let stage = self.stage()?;
            // The receipt already records this name. Set ownership before writing bytes.
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&stage)?;
            fsetxattr(
                &file,
                OWNER_ATTRIBUTE,
                self.stamp(&file)?.as_bytes(),
                XattrFlags::CREATE,
            )?;
            file.write_all(bytes)?;
            file.sync_all()?;
            renameat_with(CWD, &stage, CWD, &self.path, RenameFlags::NOREPLACE).with_context(
                || format!("cannot publish document output: {}", self.path.display()),
            )?;
        }
        directory.sync_all()?;
        self.remove_stage()
    }

    pub(crate) fn remove_stage(&self) -> Result<()> {
        let stage = self.stage()?;
        if let Some(file) = open_regular(&stage)? {
            let owned = match has_stamp(&file, &self.stamp(&file)?)? {
                Some(matches) => matches,
                // A crash before setting the attribute leaves only an empty reserved file.
                None => file.metadata()?.len() == 0,
            };
            ensure!(owned, "unowned staged publication: {}", stage.display());
            fs::remove_file(&stage)?;
            File::open(parent(&stage)?)?.sync_all()?;
        }
        Ok(())
    }

    pub(crate) fn remove_output(&self) -> Result<()> {
        if let Some(directory) = lock_directory(parent(&self.path)?)? {
            if self.verify_output_if_present()? {
                fs::remove_file(&self.path)?;
                directory.sync_all()?;
            }
            self.remove_stage()?;
        }
        Ok(())
    }
}

fn has_stamp(file: &File, expected: &str) -> Result<Option<bool>> {
    let mut bytes = [0; 128];
    match fgetxattr(file, OWNER_ATTRIBUTE, &mut bytes[..]) {
        Ok(size) => Ok(Some(&bytes[..size] == expected.as_bytes())),
        Err(Errno::NODATA) => Ok(None),
        Err(Errno::RANGE) => Ok(Some(false)),
        Err(error) => Err(error.into()),
    }
}

fn open_regular(path: &Path) -> Result<Option<File>> {
    owned_path(path)?;
    match fs::symlink_metadata(path) {
        Ok(metadata) => ensure!(
            metadata.is_file(),
            "expected publication file: {}",
            path.display()
        ),
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    }
    match File::open(path) {
        Ok(file) => Ok(Some(file)),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.into()),
    }
}

pub(crate) fn parent(path: &Path) -> Result<&Path> {
    path.parent().context("missing publication directory")
}

pub(crate) fn lock_directory(path: &Path) -> Result<Option<File>> {
    owned_path(path)?;
    let directory = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    ensure!(
        directory.metadata()?.is_dir(),
        "expected output directory: {}",
        path.display()
    );
    directory
        .lock()
        .with_context(|| format!("cannot lock output directory {}", path.display()))?;
    Ok(Some(directory))
}

pub(crate) fn preflight<'a>(outputs: impl Iterator<Item = &'a Path>) -> Result<()> {
    let mut checked = HashSet::new();
    for output in outputs {
        if output.try_exists()? {
            continue;
        }
        let directory = parent(output)?
            .ancestors()
            .find(|path| path.is_dir())
            .context("no existing output directory ancestor")?;
        if checked.insert(directory) {
            let _lock = lock_directory(directory)?.context("output directory disappeared")?;
            probe(directory).with_context(|| format!("output filesystem requires user extended attributes and atomic no-clobber rename: {}", directory.display()))?;
        }
    }
    Ok(())
}

fn probe(directory: &Path) -> Result<()> {
    // Only empty probe files are visible before provider admission.
    let probe = tempfile::tempdir_in(directory)?;
    let source = probe.path().join("source");
    let destination = probe.path().join("destination");
    let file = File::create(&source)?;
    let stamp = format!("probe:{}", file.metadata()?.ino());
    fsetxattr(&file, OWNER_ATTRIBUTE, stamp.as_bytes(), XattrFlags::CREATE)?;
    File::create(&destination)?;
    ensure!(
        renameat_with(CWD, &source, CWD, &destination, RenameFlags::NOREPLACE) == Err(Errno::EXIST),
        "filesystem did not refuse replacing the probe file"
    );
    fs::remove_file(&destination)?;
    renameat_with(CWD, &source, CWD, &destination, RenameFlags::NOREPLACE)?;
    let published = File::open(&destination)?;
    ensure!(
        has_stamp(
            &published,
            &format!("probe:{}", published.metadata()?.ino())
        )? == Some(true),
        "filesystem did not preserve publication identity"
    );
    probe.close()?;
    Ok(())
}
