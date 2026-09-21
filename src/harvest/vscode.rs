//! Visual Studio Code and its forks (VSCodium, Code - OSS, Cursor, Windsurf)
//! compile their default keybindings into the application bundle, so only the
//! user's changes exist as a file: `keybindings.json`, a list of
//! `{ key, command, when }` entries where a `-` before the command removes a
//! default.
//!
//! The defaults are therefore carried here, for the commands whose controls a
//! user actually clicks — the activity bar, the panel, the editor toolbar —
//! together with the labels those controls publish through accessibility.

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

/// A shipped default: command id, the title the Keyboard Shortcuts editor
/// shows, the labels its clickable controls carry, and its keys on Linux.
struct Shipped {
    command: &'static str,
    title: &'static str,
    aliases: &'static [&'static str],
    keys: &'static [&'static str],
}

const DEFAULTS: &[Shipped] = &[
    Shipped { command: "workbench.action.showCommands", title: "Command Palette", aliases: &["Show All Commands", "Command Palette..."], keys: &["ctrl+shift+p", "f1"] },
    Shipped { command: "workbench.action.quickOpen", title: "Go to File", aliases: &["Go to File...", "Quick Open"], keys: &["ctrl+p"] },
    Shipped { command: "workbench.view.explorer", title: "Explorer", aliases: &["Explorer (Ctrl+Shift+E)"], keys: &["ctrl+shift+e"] },
    Shipped { command: "workbench.view.search", title: "Search", aliases: &["Search (Ctrl+Shift+F)"], keys: &["ctrl+shift+f"] },
    Shipped { command: "workbench.view.scm", title: "Source Control", aliases: &["Source Control (Ctrl+Shift+G)"], keys: &["ctrl+shift+g"] },
    Shipped { command: "workbench.view.debug", title: "Run and Debug", aliases: &["Run and Debug (Ctrl+Shift+D)"], keys: &["ctrl+shift+d"] },
    Shipped { command: "workbench.view.extensions", title: "Extensions", aliases: &["Extensions (Ctrl+Shift+X)"], keys: &["ctrl+shift+x"] },
    Shipped { command: "workbench.action.toggleSidebarVisibility", title: "Toggle Primary Side Bar", aliases: &["Toggle Primary Side Bar (Ctrl+B)"], keys: &["ctrl+b"] },
    Shipped { command: "workbench.action.togglePanel", title: "Toggle Panel", aliases: &[], keys: &["ctrl+j"] },
    Shipped { command: "workbench.action.terminal.toggleTerminal", title: "Toggle Terminal", aliases: &["Terminal"], keys: &["ctrl+`"] },
    Shipped { command: "workbench.action.terminal.new", title: "New Terminal", aliases: &[], keys: &["ctrl+shift+`"] },
    Shipped { command: "workbench.action.terminal.split", title: "Split Terminal", aliases: &[], keys: &["ctrl+shift+5"] },
    Shipped { command: "workbench.action.terminal.kill", title: "Kill Terminal", aliases: &["Kill the Active Terminal Instance"], keys: &[] },
    Shipped { command: "workbench.actions.view.problems", title: "Problems", aliases: &[], keys: &["ctrl+shift+m"] },
    Shipped { command: "workbench.action.output.toggleOutput", title: "Output", aliases: &[], keys: &["ctrl+shift+u"] },
    Shipped { command: "workbench.debug.action.toggleRepl", title: "Debug Console", aliases: &[], keys: &["ctrl+shift+y"] },
    Shipped { command: "workbench.action.files.newUntitledFile", title: "New File", aliases: &["New Untitled File"], keys: &["ctrl+n"] },
    Shipped { command: "workbench.action.files.openFile", title: "Open File", aliases: &["Open File..."], keys: &["ctrl+o"] },
    Shipped { command: "workbench.action.files.save", title: "Save", aliases: &[], keys: &["ctrl+s"] },
    Shipped { command: "workbench.action.files.saveAs", title: "Save As", aliases: &["Save As..."], keys: &["ctrl+shift+s"] },
    Shipped { command: "workbench.action.files.saveAll", title: "Save All", aliases: &[], keys: &["ctrl+k s"] },
    Shipped { command: "workbench.action.closeActiveEditor", title: "Close Editor", aliases: &["Close"], keys: &["ctrl+w"] },
    Shipped { command: "workbench.action.splitEditor", title: "Split Editor", aliases: &["Split Editor Right"], keys: &["ctrl+\\"] },
    Shipped { command: "workbench.action.nextEditor", title: "Next Editor", aliases: &[], keys: &["ctrl+pagedown"] },
    Shipped { command: "workbench.action.previousEditor", title: "Previous Editor", aliases: &[], keys: &["ctrl+pageup"] },
    Shipped { command: "workbench.action.navigateBack", title: "Go Back", aliases: &["Back"], keys: &["ctrl+alt+-"] },
    Shipped { command: "workbench.action.navigateForward", title: "Go Forward", aliases: &["Forward"], keys: &["ctrl+shift+-"] },
    Shipped { command: "workbench.action.gotoLine", title: "Go to Line/Column", aliases: &["Go to Line/Column..."], keys: &["ctrl+g"] },
    Shipped { command: "workbench.action.gotoSymbol", title: "Go to Symbol in Editor", aliases: &["Go to Symbol in Editor..."], keys: &["ctrl+shift+o"] },
    Shipped { command: "workbench.action.showAllSymbols", title: "Go to Symbol in Workspace", aliases: &["Go to Symbol in Workspace..."], keys: &["ctrl+t"] },
    Shipped { command: "workbench.action.findInFiles", title: "Search in Files", aliases: &["Find in Files"], keys: &["ctrl+shift+f"] },
    Shipped { command: "actions.find", title: "Find", aliases: &[], keys: &["ctrl+f"] },
    Shipped { command: "editor.action.startFindReplaceAction", title: "Replace", aliases: &[], keys: &["ctrl+h"] },
    Shipped { command: "editor.action.commentLine", title: "Toggle Line Comment", aliases: &[], keys: &["ctrl+/"] },
    Shipped { command: "editor.action.formatDocument", title: "Format Document", aliases: &[], keys: &["ctrl+shift+i"] },
    Shipped { command: "editor.action.rename", title: "Rename Symbol", aliases: &[], keys: &["f2"] },
    Shipped { command: "editor.action.revealDefinition", title: "Go to Definition", aliases: &[], keys: &["f12"] },
    Shipped { command: "workbench.action.openSettings", title: "Settings", aliases: &["Open Settings", "Manage"], keys: &["ctrl+,"] },
    Shipped { command: "workbench.action.openGlobalKeybindings", title: "Keyboard Shortcuts", aliases: &[], keys: &["ctrl+k ctrl+s"] },
    Shipped { command: "workbench.action.openRecent", title: "Open Recent", aliases: &[], keys: &["ctrl+r"] },
    Shipped { command: "workbench.action.toggleZenMode", title: "Zen Mode", aliases: &["Toggle Zen Mode"], keys: &["ctrl+k z"] },
    Shipped { command: "workbench.action.toggleFullScreen", title: "Full Screen", aliases: &["Toggle Full Screen"], keys: &["f11"] },
    Shipped { command: "workbench.action.zoomIn", title: "Zoom In", aliases: &[], keys: &["ctrl+="] },
    Shipped { command: "workbench.action.zoomOut", title: "Zoom Out", aliases: &[], keys: &["ctrl+-"] },
    Shipped { command: "workbench.action.zoomReset", title: "Reset Zoom", aliases: &[], keys: &["ctrl+numpad0"] },
];

/// `ctrl+k ctrl+s` is a sequence of chords, rendered the way the index states
/// them: `Ctrl+K, Ctrl+S`.
fn chords(key: &str) -> Option<String> {
    let key = key.trim();
    if key.is_empty() || key.to_ascii_lowercase().contains("cmd+") || key.to_ascii_lowercase().contains("meta+") {
        return None;
    }
    let mut rendered = Vec::new();
    for part in key.split_whitespace() {
        rendered.push(keys::plus_separated(part, Case::Insensitive)?);
    }
    (!rendered.is_empty()).then(|| rendered.join(", "))
}

/// `workbench.action.terminal.new` → `Workbench: Action: Terminal: New`, the
/// only name a command the defaults do not cover can be given.
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

fn shipped() -> Vec<Shortcut> {
    DEFAULTS
        .iter()
        .map(|default| Shortcut {
            title: default.title.to_string(),
            keys: default.keys.iter().filter_map(|key| chords(key)).collect(),
            aliases: default.aliases.iter().map(|alias| alias.to_string()).collect(),
            section: None,
            action: Some(default.command.to_string()),
            site: None,
        })
        .filter(|shortcut| !shortcut.keys.is_empty())
        .collect()
}

/// The shipped defaults with the user's `keybindings.json` layered on top.
/// A malformed user file is an error: the defaults alone are still returned by
/// passing `None`.
pub fn shortcuts(user_keybindings: Option<&str>) -> Result<Vec<Shortcut>, String> {
    let mut found = shipped();
    let Some(text) = user_keybindings else { return Ok(found) };
    let bindings: Vec<Binding> = json5_relaxed(text)?;
    for binding in bindings {
        if binding.command.is_empty() {
            continue;
        }
        // A leading `-` removes a default binding.
        if let Some(removed) = binding.command.strip_prefix('-') {
            let key = chords(&binding.key);
            for shortcut in found.iter_mut().filter(|shortcut| shortcut.action.as_deref() == Some(removed)) {
                shortcut.keys.retain(|existing| key.as_ref().is_none_or(|key| key != existing));
            }
            continue;
        }
        let Some(key) = chords(&binding.key) else { continue };
        if let Some(existing) = found.iter_mut().find(|shortcut| shortcut.action.as_deref() == Some(binding.command.as_str())) {
            // The user's binding is the one they will press; it goes first.
            existing.keys.retain(|candidate| candidate != &key);
            existing.keys.insert(0, key);
            continue;
        }
        found.push(Shortcut {
            title: settings_name(&binding.command),
            keys: vec![key],
            aliases: Vec::new(),
            section: binding.when.filter(|when| !when.is_empty()),
            action: Some(binding.command),
            site: None,
        });
    }
    found.retain(|shortcut| !shortcut.keys.is_empty());
    Ok(found)
}

/// `keybindings.json` is JSON with comments, which the editor writes into it
/// by default. Line and block comments are removed before parsing; trailing
/// commas are left to the parser, which rejects the file if they remain.
fn json5_relaxed(text: &str) -> Result<Vec<Binding>, String> {
    let mut cleaned = String::with_capacity(text.len());
    let mut characters = text.chars().peekable();
    let (mut in_string, mut escaped) = (false, false);
    while let Some(character) = characters.next() {
        if in_string {
            cleaned.push(character);
            escaped = !escaped && character == '\\';
            in_string = !(character == '"' && !escaped);
            continue;
        }
        match character {
            '"' => {
                in_string = true;
                cleaned.push(character);
            }
            '/' if characters.peek() == Some(&'/') => {
                for next in characters.by_ref() {
                    if next == '\n' {
                        cleaned.push('\n');
                        break;
                    }
                }
            }
            '/' if characters.peek() == Some(&'*') => {
                let mut previous = ' ';
                for next in characters.by_ref() {
                    if previous == '*' && next == '/' {
                        break;
                    }
                    previous = next;
                }
            }
            _ => cleaned.push(character),
        }
    }
    // A trailing comma before the closing bracket is legal in the editor.
    let trimmed = cleaned.trim_end();
    let cleaned = match trimmed.strip_suffix(']').map(str::trim_end).and_then(|body| body.strip_suffix(',')) {
        Some(body) => format!("{body}]"),
        None => cleaned,
    };
    serde_json::from_str(&cleaned).map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keys_of(found: &[Shortcut], command: &str) -> Vec<String> {
        found.iter().find(|shortcut| shortcut.action.as_deref() == Some(command)).map(|shortcut| shortcut.keys.clone()).unwrap_or_default()
    }

    #[test]
    fn defaults_alone_cover_the_clickable_views() {
        let found = shortcuts(None).unwrap();
        assert_eq!(keys_of(&found, "workbench.view.explorer"), vec!["Ctrl+Shift+E"]);
        assert_eq!(keys_of(&found, "workbench.action.showCommands"), vec!["Ctrl+Shift+P", "F1"]);
        // A sequence of chords keeps both, in the order they are pressed.
        assert_eq!(keys_of(&found, "workbench.action.openGlobalKeybindings"), vec!["Ctrl+K, Ctrl+S"]);
    }

    #[test]
    fn user_bindings_are_added_ahead_of_the_default() {
        let found = shortcuts(Some(
            r#"// Place your key bindings in this file
            [
                {"key": "ctrl+alt+b", "command": "workbench.action.toggleSidebarVisibility"},
                {"key": "ctrl+shift+j", "command": "team.review", "when": "editorFocus"},
            ]"#,
        ))
        .unwrap();
        assert_eq!(keys_of(&found, "workbench.action.toggleSidebarVisibility"), vec!["Ctrl+Alt+B", "Ctrl+B"]);
        let added = found.iter().find(|shortcut| shortcut.action.as_deref() == Some("team.review")).unwrap();
        assert_eq!(added.title, "Team: Review");
        assert_eq!(added.section.as_deref(), Some("editorFocus"));
    }

    #[test]
    fn a_removed_default_is_not_suggested() {
        let found = shortcuts(Some(r#"[{"key": "ctrl+b", "command": "-workbench.action.toggleSidebarVisibility"}]"#)).unwrap();
        assert!(keys_of(&found, "workbench.action.toggleSidebarVisibility").is_empty());
    }

    #[test]
    fn block_comments_do_not_break_parsing() {
        let found = shortcuts(Some("/* header */\n[{\"key\": \"ctrl+alt+p\", \"command\": \"workbench.action.showCommands\"}]")).unwrap();
        assert_eq!(keys_of(&found, "workbench.action.showCommands"), vec!["Ctrl+Alt+P", "Ctrl+Shift+P", "F1"]);
    }

    #[test]
    fn a_broken_file_is_reported() {
        assert!(shortcuts(Some("[{")).is_err());
    }
}
