use super::owned_path;
use anyhow::{Context, Result, ensure};
use serde_json::Value;
use std::path::{Path, PathBuf};

pub(crate) fn field<'a>(object: &'a Value, key: &str) -> Result<&'a Value> {
    object
        .get(key)
        .with_context(|| format!("plan is missing {key}"))
}

pub(crate) fn string<'a>(object: &'a Value, key: &str) -> Result<&'a str> {
    field(object, key)?
        .as_str()
        .with_context(|| format!("invalid plan {key}"))
}

pub(crate) fn optional_string<'a>(object: &'a Value, key: &str) -> Result<Option<&'a str>> {
    let value = field(object, key)?;
    if value.is_null() {
        Ok(None)
    } else {
        Ok(Some(
            value
                .as_str()
                .with_context(|| format!("invalid plan {key}"))?,
        ))
    }
}

pub(crate) fn absolute(value: &str, kind: &str) -> Result<PathBuf> {
    let path = Path::new(value);
    ensure!(path.is_absolute(), "plan {kind} must be an absolute path");
    owned_path(path)?;
    Ok(path.to_path_buf())
}
