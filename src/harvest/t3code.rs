//! T3 Code keeps its keybindings in `~/.t3/userdata/keybindings.json`, a list
//! of `{ key, command, when }` entries it writes with every default and the
//! user's changes. The file names commands, not the labels on screen, so each
//! known command carries the labels its controls use.

use serde::Deserialize;

use crate::keys::{self, Case};
use crate::model::Shortcut;

#[derive(Deserialize)]
struct Binding {
    #[serde(default)]
    key: String,
    #[serde(default)]
    command: String,
    #[serde(default)]
    when: Option<String>,
}

/// Title and on-screen labels for each command. Labels come from the app's
/// accessible names (`Toggle main sidebar`) and command palette entries
/// (`Go to file`); the title follows the verb-first wording of its buttons.
const COMMANDS: &[(&str, &str, &[&str])] = &[
    ("sidebar.toggle", "Toggle sidebar", &["Toggle main sidebar"]),
    ("terminal.toggle", "Toggle terminal", &[]),
    ("terminal.split", "Split terminal", &[]),
    ("terminal.splitVertical", "Split terminal vertically", &[]),
    ("terminal.new", "New terminal", &[]),
    ("terminal.close", "Close terminal", &[]),
    ("rightPanel.toggle", "Toggle right panel", &[]),
    ("rightPanel.toggleMaximized", "Maximize right panel", &[]),
    ("rightPanel.close", "Close right panel", &[]),
    ("diff.toggle", "Toggle diff", &[]),
    ("preview.toggle", "Toggle preview", &[]),
    ("preview.refresh", "Refresh preview", &[]),
    ("preview.focusUrl", "Focus preview address", &[]),
    ("preview.zoomIn", "Zoom in preview", &[]),
    ("preview.zoomOut", "Zoom out preview", &[]),
    ("preview.resetZoom", "Reset preview zoom", &[]),
    ("commandPalette.toggle", "Command palette", &[]),
    ("filePicker.toggle", "Go to file", &["File picker"]),
    ("projectSearch.toggle", "Search project contents", &[]),
    ("themeEditor.toggle", "Toggle theme editor", &[]),
    ("composer.stash", "Stash draft", &[]),
    ("chat.new", "New thread", &["New chat"]),
    ("chat.newLocal", "New local thread", &[]),
    ("modelPicker.toggle", "Choose model", &["Model picker"]),
    ("editor.openFavorite", "Open in editor", &[]),
    ("thread.stop", "Stop", &[]),
    ("thread.previous", "Previous thread", &[]),
    ("thread.next", "Next thread", &[]),
    ("thread.copyReference", "Copy thread reference", &[]),
    ("thread.settle", "Settle thread", &["Un-settle thread"]),
    ("thread.pin", "Pin thread", &["Unpin thread"]),
];

/// `sidebar.toggle` → `Sidebar: Toggle`, the name T3 Code's keybinding
/// settings show.
fn settings_name(command: &str) -> String {
    command
        .split('.')
        .map(|part| {
            let mut words = String::new();
            for (index, character) in part.chars().enumerate() {
                if index == 0 {
                    words.extend(character.to_uppercase());
                } else if character.is_uppercase() {
                    words.push(' ');
                    words.push(character);
                } else {
                    words.push(character);
                }
            }
            words
        })
        .collect::<Vec<_>>()
        .join(": ")
}

fn chord(key: &str) -> Option<String> {
    keys::plus_separated(key, Case::Insensitive)
}

pub fn shortcuts(text: &str) -> Result<Vec<Shortcut>, String> {
    let bindings: Vec<Binding> = serde_json::from_str(text).map_err(|error| error.to_string())?;
    let mut found: Vec<Shortcut> = Vec::new();
    for binding in bindings {
        // A leading `-` removes a binding, as in VS Code.
        if let Some(removed) = binding.command.strip_prefix('-') {
            let key = chord(&binding.key);
            for shortcut in found.iter_mut().filter(|shortcut| shortcut.action.as_deref() == Some(removed)) {
                shortcut.keys.retain(|existing| key.as_ref().is_some_and(|key| key != existing));
            }
            continue;
        }
        let Some(key) = chord(&binding.key) else { continue };
        if binding.command.is_empty() {
            continue;
        }
        if let Some(existing) = found.iter_mut().find(|shortcut| shortcut.action.as_deref() == Some(binding.command.as_str())) {
            if !existing.keys.contains(&key) {
                existing.keys.push(key);
            }
            continue;
        }
        let settings = settings_name(&binding.command);
        let (title, mut aliases) = match COMMANDS.iter().find(|(command, _, _)| *command == binding.command) {
            Some((_, title, labels)) => (title.to_string(), labels.iter().map(|label| label.to_string()).collect()),
            None => (settings.clone(), Vec::new()),
        };
        if title != settings {
            aliases.push(settings);
        }
        found.push(Shortcut {
            title,
            keys: vec![key],
            aliases,
            section: binding.when.filter(|when| !when.is_empty()),
            action: Some(binding.command),
            site: None,
        });
    }
    found.retain(|shortcut| !shortcut.keys.is_empty());
    Ok(found)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_bindings_with_labels_and_alternatives() {
        let found = shortcuts(
            r#"[
                {"key": "mod+b", "command": "sidebar.toggle"},
                {"key": "mod+n", "command": "chat.new", "when": "!terminalFocus"},
                {"key": "mod+shift+o", "command": "chat.new", "when": "!terminalFocus"},
                {"key": "mod++", "command": "preview.zoomIn"},
                {"key": "mod+shift+[", "command": "thread.previous"},
                {"key": "mod+alt+x", "command": "future.newThing"},
                {"key": "mod+j", "command": "terminal.toggle"},
                {"key": "mod+j", "command": "-terminal.toggle"}
            ]"#,
        )
        .unwrap();
        let rendered: Vec<(&str, Vec<&str>, Vec<&str>)> = found
            .iter()
            .map(|s| (s.title.as_str(), s.keys.iter().map(String::as_str).collect(), s.aliases.iter().map(String::as_str).collect()))
            .collect();
        assert_eq!(
            rendered,
            vec![
                ("Toggle sidebar", vec!["Ctrl+B"], vec!["Toggle main sidebar", "Sidebar: Toggle"]),
                ("New thread", vec!["Ctrl+N", "Ctrl+Shift+O"], vec!["New chat", "Chat: New"]),
                ("Zoom in preview", vec!["Ctrl++"], vec!["Preview: Zoom In"]),
                ("Previous thread", vec!["Ctrl+Shift+["], vec!["Thread: Previous"]),
                ("Future: New Thing", vec!["Ctrl+Alt+X"], vec![]),
            ]
        );
        assert_eq!(found[1].section.as_deref(), Some("!terminalFocus"));
    }

    #[test]
    fn rejects_malformed_files() {
        assert!(shortcuts("{").is_err());
    }
}
