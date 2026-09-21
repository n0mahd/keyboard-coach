//! Assemble the shortcut index for every installed application.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::desktop::{self, DesktopApp};
use crate::elf::{self, Elf};
use crate::gvdb;
use crate::harvest::{self, gtk, kde, libreoffice, t3code, terminal, vscode};
use crate::process;
use crate::model::{App, Index, Shortcut, SourceReport, SCHEMA_VERSION};

pub struct Options {
    pub application_dirs: Vec<PathBuf>,
    pub libreoffice_registry: PathBuf,
    pub config_home: PathBuf,
    pub home: PathBuf,
    /// Run helper programs (lazygit, Omarchy keybinding menus) to read bindings.
    pub run_tools: bool,
}

impl Default for Options {
    fn default() -> Self {
        let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
        Options {
            application_dirs: desktop::application_dirs(),
            libreoffice_registry: PathBuf::from("/usr/lib/libreoffice/share/registry"),
            config_home: std::env::var_os("XDG_CONFIG_HOME").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| home.join(".config")),
            home,
            run_tools: true,
        }
    }
}

/// Toolkit and embedded-resource shortcuts for one binary, shared by every
/// desktop entry that launches it.
struct BinaryHarvest {
    toolkit: Option<&'static str>,
    shortcuts: Vec<Shortcut>,
    reports: Vec<SourceReport>,
}

pub fn build(options: &Options) -> Index {
    let apps = desktop::discover(&options.application_dirs);
    let mut binaries: HashMap<PathBuf, BinaryHarvest> = HashMap::new();
    let mut libreoffice_registry: Option<Result<libreoffice::Registry, String>> = None;
    let mut index_apps = Vec::new();
    let mut command_counts: HashMap<&str, usize> = HashMap::new();
    for app in &apps {
        if let Some(command) = app.command.as_deref() {
            *command_counts.entry(command).or_default() += 1;
        }
    }

    for app in &apps {
        let mut shortcuts = Vec::new();
        let mut sources = Vec::new();
        let mut toolkit = None;

        if let Some(binary) = &app.binary {
            let harvest = binaries.entry(binary.clone()).or_insert_with(|| harvest_binary(app, binary));
            toolkit = harvest.toolkit;
            shortcuts.extend(harvest.shortcuts.iter().cloned());
            sources.extend(harvest.reports.iter().cloned());
        }

        if let Some(module) = libreoffice::MODULES.iter().find(|module| module.app_id == app.id) {
            toolkit = Some("libreoffice");
            let registry = libreoffice_registry.get_or_insert_with(|| load_libreoffice(&options.libreoffice_registry));
            match registry {
                Ok(registry) => {
                    let found = libreoffice::module_shortcuts(registry, module);
                    sources.push(report("libreoffice-registry", &options.libreoffice_registry, found.len(), None));
                    shortcuts.extend(found);
                }
                Err(error) => sources.push(report("libreoffice-registry", &options.libreoffice_registry, 0, Some(error.clone()))),
            }
        }

        let unique_command = app.command.as_deref().is_none_or(|command| command_counts.get(command) == Some(&1));
        // Process names identify terminal emulators, whose windows can carry
        // any app id (`org.omarchy.terminal`, `TUI.float`).
        let processes: Vec<String> = app.binary.iter().filter_map(|binary| binary.file_name()?.to_str().map(str::to_string)).collect();
        if let Some(command) = app.command.as_deref() {
            for (harvester, origin, found) in text_config_shortcuts(options, command) {
                sources.push(report(harvester, &origin, found.len(), None));
                shortcuts.extend(found);
            }
        }

        // KDE applications inherit KStandardShortcut, with the user's
        // reassignments in their own config file and in kdeglobals.
        if toolkit == Some("kde") {
            let component = app.command.clone().unwrap_or_else(|| app.id.clone());
            let path = options.config_home.join(format!("{component}rc"));
            let globals = options.config_home.join("kdeglobals");
            let found = kde::shortcuts(read_optional(&path).as_deref(), read_optional(&globals).as_deref());
            sources.push(report("kde-standard-actions", &path, found.len(), None));
            shortcuts.extend(found);
        }

        if let Some(directory) = vscode_config_dir(app.command.as_deref()) {
            toolkit = toolkit.or(Some("electron"));
            let path = options.config_home.join(directory).join("User/keybindings.json");
            let flatpak = options.home.join(".var/app").join(vscode_flatpak_id(directory)).join("config").join(directory).join("User/keybindings.json");
            let user = read_optional(&path).or_else(|| read_optional(&flatpak));
            match vscode::shortcuts(user.as_deref()) {
                Ok(found) => {
                    sources.push(report("vscode-keybindings", &path, found.len(), None));
                    shortcuts.extend(found);
                }
                Err(error) => {
                    sources.push(report("vscode-keybindings", &path, 0, Some(error)));
                    shortcuts.extend(vscode::shortcuts(None).unwrap_or_default());
                }
            }
        }

        if app.id == "t3code" {
            let path = options.home.join(".t3/userdata/keybindings.json");
            if let Some(text) = read_optional(&path) {
                match t3code::shortcuts(&text) {
                    Ok(found) => {
                        toolkit = toolkit.or(Some("electron"));
                        sources.push(report("t3code-keybindings", &path, found.len(), None));
                        shortcuts.extend(found);
                    }
                    Err(error) => sources.push(report("t3code-keybindings", &path, 0, Some(error))),
                }
            }
        }

        index_apps.push(App {
            id: app.id.clone(),
            name: app.name.clone(),
            classes: app.classes(unique_command),
            processes,
            executable: app.binary.as_ref().map(|path| path.display().to_string()),
            toolkit: toolkit.map(str::to_string),
            sources,
            shortcuts: harvest::dedupe(shortcuts),
        });
    }

    if options.run_tools {
        index_apps.extend(terminal_tools());
    }

    Index {
        schema_version: SCHEMA_VERSION,
        generated_at: SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |elapsed| elapsed.as_secs()),
        apps: index_apps,
    }
}

fn report(harvester: &str, origin: &Path, shortcuts: usize, error: Option<String>) -> SourceReport {
    SourceReport { harvester: harvester.to_string(), origin: origin.display().to_string(), shortcuts, error }
}

/// Parse every registry layer: `main.xcd` holds the key table and generic
/// labels, and each module's file (`writer.xcd`, `calc.xcd`) its own labels.
fn load_libreoffice(dir: &Path) -> Result<libreoffice::Registry, String> {
    let mut files: Vec<PathBuf> = fs::read_dir(dir)
        .map_err(|error| error.to_string())?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.extension().is_some_and(|ext| ext == "xcd"))
        .collect();
    files.sort();
    let mut registry = libreoffice::Registry::default();
    for file in files {
        let xml = fs::read(&file).map_err(|error| format!("{}: {error}", file.display()))?;
        libreoffice::parse_xcd(&xml, &mut registry);
    }
    if registry.keys.is_empty() {
        return Err("no accelerator table found".into());
    }
    Ok(registry)
}

fn is_gtk(toolkit: Option<&str>) -> bool {
    matches!(toolkit, Some("gtk3" | "gtk4" | "libadwaita"))
}

fn harvest_binary(app: &DesktopApp, binary: &Path) -> BinaryHarvest {
    let Some(mut elf) = Elf::open(binary) else {
        return BinaryHarvest { toolkit: None, shortcuts: Vec::new(), reports: Vec::new() };
    };
    let needed = elf.needed();
    let toolkit = elf::toolkit(binary, &needed);
    let mut shortcuts = Vec::new();
    let mut reports = Vec::new();
    if is_gtk(toolkit) {
        let mut resources = Vec::new();
        // glib-compile-resources places bundles in dedicated sections when it
        // can; older builds leave them in read-only data.
        let sections = elf.read_named(|name| name.starts_with(".gresource."));
        let sections = if sections.is_empty() { elf.read_named(|name| name == ".rodata" || name == ".data.rel.ro") } else { sections };
        for (_, bytes) in &sections {
            resources.extend(gvdb::scan(bytes));
        }
        let found = shortcuts_from_resources(&resources);
        reports.push(report("gtk-resources", binary, found.len(), None));
        shortcuts.extend(found);

        for bundle in resource_files(app) {
            let Ok(bytes) = fs::read(&bundle) else { continue };
            let found = shortcuts_from_resources(&gvdb::scan(&bytes));
            if !found.is_empty() {
                reports.push(report("gtk-resources", &bundle, found.len(), None));
                shortcuts.extend(found);
            }
        }
    }
    BinaryHarvest { toolkit, shortcuts, reports }
}

fn shortcuts_from_resources(resources: &[gvdb::Resource]) -> Vec<Shortcut> {
    resources
        .iter()
        .filter(|resource| resource.path.ends_with(".ui") || resource.path.ends_with(".xml"))
        .flat_map(|resource| gtk::extract(&resource.data))
        .collect()
}

/// `.gresource` files an app ships outside its binary.
fn resource_files(app: &DesktopApp) -> Vec<PathBuf> {
    let mut names = vec![app.id.clone()];
    if let Some(command) = &app.command {
        names.push(command.clone());
    }
    let mut files = Vec::new();
    for name in names {
        for root in [Path::new("/usr/share").join(&name), Path::new("/usr/lib").join(&name)] {
            collect_gresource(&root, 2, &mut files);
        }
    }
    files.sort();
    files.dedup();
    files
}

fn collect_gresource(dir: &Path, depth: usize, files: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() && depth > 0 {
            collect_gresource(&path, depth - 1, files);
        } else if path.extension().is_some_and(|ext| ext == "gresource") {
            files.push(path);
        }
    }
}

/// The user configuration directory of a Visual Studio Code fork, keyed by the
/// command its desktop entry launches.
fn vscode_config_dir(command: Option<&str>) -> Option<&'static str> {
    Some(match command? {
        "code" => "Code",
        "code-insiders" => "Code - Insiders",
        "code-oss" => "Code - OSS",
        "codium" | "vscodium" => "VSCodium",
        "cursor" => "Cursor",
        "windsurf" => "Windsurf",
        _ => return None,
    })
}

fn vscode_flatpak_id(directory: &str) -> &'static str {
    match directory {
        "Code" => "com.visualstudio.code",
        "Code - Insiders" => "com.visualstudio.code.insiders",
        "VSCodium" => "com.vscodium.codium",
        _ => "com.visualstudio.code.oss",
    }
}

fn read_optional(path: &Path) -> Option<String> {
    fs::read_to_string(path).ok()
}

/// Shortcuts for apps configured by text files, keyed by the launched command.
fn text_config_shortcuts(options: &Options, command: &str) -> Vec<(&'static str, PathBuf, Vec<Shortcut>)> {
    let config = &options.config_home;
    match command {
        "foot" | "footclient" => {
            let defaults = Path::new("/etc/xdg/foot/foot.ini");
            let Some(default_text) = read_optional(defaults) else { return Vec::new() };
            let user = config.join("foot/foot.ini");
            let found = terminal::foot(&default_text, read_optional(&user).as_deref());
            vec![("foot-config", user, found)]
        }
        "mpv" => {
            let defaults = Path::new("/usr/share/doc/mpv/input.conf");
            let Some(default_text) = read_optional(defaults) else { return Vec::new() };
            // A system input.conf and the user's both layer over the defaults.
            let mut overrides = read_optional(Path::new("/etc/mpv/input.conf")).unwrap_or_default();
            let user = config.join("mpv/input.conf");
            overrides.push('\n');
            overrides.push_str(&read_optional(&user).unwrap_or_default());
            vec![("mpv-input", user, terminal::mpv(&default_text, Some(&overrides)))]
        }
        "imv" | "imv-wayland" | "imv-dir" => {
            let defaults = Path::new("/etc/imv_config");
            let default_text = read_optional(defaults).unwrap_or_default();
            let user = config.join("imv/config");
            vec![("imv-config", user.clone(), terminal::imv(&default_text, read_optional(&user).as_deref()))]
        }
        _ => Vec::new(),
    }
}

fn on_path(program: &str) -> bool {
    std::env::var("PATH").unwrap_or_default().split(':').any(|dir| Path::new(dir).join(program).is_file())
}

/// Tools that run inside a terminal and have no desktop entry.
fn terminal_tools() -> Vec<App> {
    let timeout = std::time::Duration::from_secs(10);
    let mut apps = Vec::new();
    let mut tool = |id: &str, name: &str, harvester: &str, origin: &str, shortcuts: Option<Vec<Shortcut>>| {
        let (found, error) = match shortcuts {
            Some(found) => (found, None),
            None => (Vec::new(), Some(format!("{origin} produced no output"))),
        };
        apps.push(App {
            id: id.to_string(),
            name: name.to_string(),
            classes: Vec::new(),
            processes: vec![id.to_string()],
            executable: None,
            toolkit: Some("terminal".to_string()),
            sources: vec![SourceReport { harvester: harvester.to_string(), origin: origin.to_string(), shortcuts: found.len(), error }],
            shortcuts: harvest::dedupe(found),
        });
    };
    if on_path("tmux") && on_path("omarchy-menu-tmux-keybindings") {
        let found = process::output("omarchy-menu-tmux-keybindings", &["--print"], timeout).map(|text| terminal::omarchy_menu(&text));
        tool("tmux", "tmux", "omarchy-keybindings-menu", "omarchy-menu-tmux-keybindings --print", found);
    }
    if on_path("herdr") && on_path("omarchy-menu-herdr-keybindings") {
        let found = process::output("omarchy-menu-herdr-keybindings", &["--print"], timeout).map(|text| terminal::omarchy_menu(&text));
        tool("herdr", "Herdr", "omarchy-keybindings-menu", "omarchy-menu-herdr-keybindings --print", found);
    }
    if on_path("lazygit") {
        let found = process::output("lazygit", &["--config"], timeout).map(|text| terminal::lazygit(&text));
        tool("lazygit", "lazygit", "lazygit-config", "lazygit --config", found);
    }
    apps
}
