//! Locations and atomic writes of the files the coach keeps.

use std::env;
use std::fs;
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};

pub fn data_dir() -> PathBuf {
    env::var_os("XDG_DATA_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env::var_os("HOME").unwrap_or_default()).join(".local/share"))
        .join("keyboard-coach")
}

/// Write JSON to a temporary sibling and rename it into place, so readers never
/// see a partial file.
pub fn write_json(path: &Path, value: &impl serde::Serialize) -> Result<()> {
    let parent = path.parent().context("output path has no directory")?;
    fs::create_dir_all(parent)?;
    let stem = path.file_name().and_then(|name| name.to_str()).unwrap_or("index");
    let temporary = parent.join(format!(".{stem}-{}", std::process::id()));
    // Captured labels can come from web pages, so the files are private.
    let mut file = fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&temporary)?;
    file.write_all(serde_json::to_string_pretty(value)?.as_bytes())?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    fs::rename(&temporary, path)?;
    Ok(())
}

