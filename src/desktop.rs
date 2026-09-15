//! Installed applications from freedesktop desktop entries.

use std::collections::BTreeSet;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DesktopApp {
    pub id: String,
    pub name: String,
    pub exec: String,
    pub startup_wm_class: Option<String>,
    /// The launched program's file name, after unwrapping `env` and flags.
    pub command: Option<String>,
    /// The ELF file that implements the app, after following wrapper scripts.
    pub binary: Option<PathBuf>,
    pub path: PathBuf,
}

impl DesktopApp {
    /// Window classes this app is likely to use, lower-cased. The launched
    /// command counts only when no other entry launches it: every LibreOffice
    /// module runs `libreoffice`, but each window carries its module's class.
    pub fn classes(&self, command_is_unique: bool) -> Vec<String> {
        let mut classes = BTreeSet::new();
        classes.insert(self.id.to_lowercase());
        if let Some(class) = &self.startup_wm_class {
            classes.insert(class.to_lowercase());
        }
        if let (Some(command), true) = (&self.command, command_is_unique) {
            classes.insert(command.to_lowercase());
        }
        classes.into_iter().filter(|class| !class.is_empty()).collect()
    }
}

pub fn application_dirs() -> Vec<PathBuf> {
    let home = env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
    let data_home = env::var_os("XDG_DATA_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join(".local/share"));
    let mut dirs = vec![data_home.join("applications")];
    let data_dirs = env::var("XDG_DATA_DIRS").ok().filter(|value| !value.is_empty());
    for dir in data_dirs.as_deref().unwrap_or("/usr/local/share:/usr/share").split(':') {
        dirs.push(Path::new(dir).join("applications"));
    }
    dirs.push(home.join(".local/share/flatpak/exports/share/applications"));
    dirs.push(PathBuf::from("/var/lib/flatpak/exports/share/applications"));
    let mut seen = BTreeSet::new();
    dirs.retain(|dir| seen.insert(dir.clone()));
    dirs
}

/// Read every launchable application. Earlier directories shadow later ones,
/// matching the freedesktop lookup order.
pub fn discover(dirs: &[PathBuf]) -> Vec<DesktopApp> {
    let mut apps = Vec::new();
    let mut seen_ids = BTreeSet::new();
    for dir in dirs {
        let Ok(entries) = fs::read_dir(dir) else { continue };
        let mut paths: Vec<PathBuf> = entries
            .filter_map(|entry| entry.ok().map(|entry| entry.path()))
            .filter(|path| path.extension().is_some_and(|ext| ext == "desktop"))
            .collect();
        paths.sort();
        for path in paths {
            let Some(id) = path.file_stem().and_then(|stem| stem.to_str()).map(str::to_string) else { continue };
            if !seen_ids.insert(id.clone()) {
                continue;
            }
            let Ok(text) = fs::read_to_string(&path) else { continue };
            if let Some(app) = parse_entry(&id, &text, &path) {
                apps.push(app);
            }
        }
    }
    apps
}

pub fn parse_entry(id: &str, text: &str, path: &Path) -> Option<DesktopApp> {
    let mut in_main = false;
    let (mut name, mut exec, mut wm_class, mut kind) = (None, None, None, None);
    let mut hidden = false;
    for line in text.lines() {
        let line = line.trim();
        if line.starts_with('[') {
            in_main = line == "[Desktop Entry]";
            continue;
        }
        if !in_main {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else { continue };
        let value = value.trim().to_string();
        match key.trim() {
            "Name" => name = Some(value),
            "Exec" => exec = Some(value),
            "StartupWMClass" => wm_class = Some(value),
            "Type" => kind = Some(value),
            "NoDisplay" | "Hidden" => hidden |= value == "true",
            _ => {}
        }
    }
    if kind.as_deref().unwrap_or("Application") != "Application" || hidden {
        return None;
    }
    let exec = exec.unwrap_or_default();
    let command = launched_command(&exec);
    let binary = command.as_deref().and_then(resolve_binary);
    Some(DesktopApp {
        id: id.to_string(),
        name: name.unwrap_or_else(|| id.to_string()),
        exec,
        startup_wm_class: wm_class.filter(|class| !class.is_empty()),
        command: command.map(|command| file_name(&command)),
        binary,
        path: path.to_path_buf(),
    })
}

fn file_name(command: &str) -> String {
    Path::new(command).file_name().and_then(|name| name.to_str()).unwrap_or(command).to_string()
}

/// The program an Exec line runs, skipping `env`, assignments, and flags.
fn launched_command(exec: &str) -> Option<String> {
    split_exec(exec)
        .into_iter()
        .find(|word| word != "env" && !word.contains('=') && !word.starts_with('-') && !word.starts_with('%'))
}

fn split_exec(exec: &str) -> Vec<String> {
    let mut words = Vec::new();
    let mut current = String::new();
    let mut quote = None;
    let mut chars = exec.chars();
    while let Some(ch) = chars.next() {
        match (quote, ch) {
            (None, '"' | '\'') => quote = Some(ch),
            (Some(open), _) if ch == open => quote = None,
            (_, '\\') => {
                if let Some(next) = chars.next() {
                    current.push(next);
                }
            }
            (None, ch) if ch.is_whitespace() => {
                if !current.is_empty() {
                    words.push(std::mem::take(&mut current));
                }
            }
            _ => current.push(ch),
        }
    }
    if !current.is_empty() {
        words.push(current);
    }
    words
}

fn search_path(command: &str) -> Option<PathBuf> {
    if command.contains('/') {
        let path = PathBuf::from(command);
        return path.is_file().then_some(path);
    }
    let path = env::var("PATH").unwrap_or_else(|_| "/usr/local/bin:/usr/bin".into());
    path.split(':').map(|dir| Path::new(dir).join(command)).find(|candidate| candidate.is_file())
}

pub fn is_elf(path: &Path) -> bool {
    use std::io::Read;
    let mut magic = [0u8; 4];
    fs::File::open(path).and_then(|mut file| file.read_exact(&mut magic)).is_ok() && &magic == b"\x7fELF"
}

/// Follow a command through symlinks and small wrapper scripts to the ELF file
/// that implements it, so embedded resources can be read.
pub fn resolve_binary(command: &str) -> Option<PathBuf> {
    let mut path = fs::canonicalize(search_path(command)?).ok()?;
    for _ in 0..3 {
        if is_elf(&path) {
            return Some(path);
        }
        let script = fs::read_to_string(&path).ok().filter(|text| text.len() < 16_384)?;
        // Wrappers that pick a backend list the Wayland branch first
        // (`if [ -n "$WAYLAND_DISPLAY" ]; then exec imv-wayland ...`).
        let target = script.lines().find_map(|line| {
            let line = line.trim();
            let rest = line.strip_prefix("exec ")?;
            split_exec(rest).into_iter().find(|word| {
                !word.starts_with('-') && !word.contains('=') && !word.starts_with('$') && word != "env"
            })
        })?;
        path = fs::canonicalize(search_path(&target)?).ok()?;
    }
    is_elf(&path).then_some(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_launch_identity() {
        let entry = "[Desktop Entry]\nType=Application\nName=Files\nExec=env GDK_BACKEND=wayland nautilus --new-window %U\nStartupWMClass=org.gnome.Nautilus\n\n[Desktop Action new-window]\nName=New Window\nExec=nautilus --new-window\n";
        let app = parse_entry("org.gnome.Nautilus", entry, Path::new("x.desktop")).unwrap();
        assert_eq!(app.name, "Files");
        assert_eq!(app.command.as_deref(), Some("nautilus"));
        assert_eq!(app.classes(true), vec!["nautilus", "org.gnome.nautilus"]);
        assert_eq!(app.classes(false), vec!["org.gnome.nautilus"]);
    }

    #[test]
    fn hidden_and_non_application_entries_are_skipped() {
        assert!(parse_entry("a", "[Desktop Entry]\nType=Link\nName=A\n", Path::new("a")).is_none());
        assert!(parse_entry("b", "[Desktop Entry]\nName=B\nExec=b\nNoDisplay=true\n", Path::new("b")).is_none());
    }

    #[test]
    fn quoted_exec_words_are_kept_together() {
        assert_eq!(split_exec(r#""/opt/My App/app" --flag %U"#), vec!["/opt/My App/app", "--flag", "%U"]);
    }
}
