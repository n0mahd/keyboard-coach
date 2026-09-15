//! Shortcuts for terminal programs and tools configured by text files.
//!
//! Each parser reads the tool's shipped defaults, then applies the user's
//! configuration on top, so the index reflects the bindings actually in effect.

use std::collections::BTreeMap;

use crate::keys::{self, Case};
use crate::model::Shortcut;

/// `clipboard-copy` → `Clipboard copy`; `quitWithoutChangingDirectory` →
/// `Quit without changing directory`.
pub fn humanize(identifier: &str) -> String {
    let mut words = String::new();
    let mut previous_lower = false;
    for ch in identifier.chars() {
        if ch == '-' || ch == '_' {
            words.push(' ');
            previous_lower = false;
        } else if ch.is_uppercase() && previous_lower {
            words.push(' ');
            words.extend(ch.to_lowercase());
            previous_lower = false;
        } else {
            words.push(ch);
            previous_lower = ch.is_lowercase() || ch.is_ascii_digit();
        }
    }
    let words = words.split_whitespace().collect::<Vec<_>>().join(" ");
    let mut chars = words.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}

/// foot's `[key-bindings]`, `[search-bindings]` and `[url-bindings]`. The shipped
/// `foot.ini` lists every default as a commented `# action=Keys Keys` line.
pub fn foot(defaults: &str, user: Option<&str>) -> Vec<Shortcut> {
    let mut table: BTreeMap<(String, String), Vec<String>> = BTreeMap::new();
    let mut apply = |text: &str, commented_defaults: bool| {
        let mut section = String::new();
        for raw in text.lines() {
            let line = raw.trim();
            if let Some(name) = line.strip_prefix('[').and_then(|rest| rest.strip_suffix(']')) {
                section = name.to_string();
                continue;
            }
            if !matches!(section.as_str(), "key-bindings" | "search-bindings" | "url-bindings") {
                continue;
            }
            let line = if commented_defaults { line.trim_start_matches('#').trim() } else { line };
            if line.starts_with('#') || line.starts_with('\\') {
                continue;
            }
            let Some((action, value)) = line.split_once('=') else { continue };
            let action = action.trim();
            if action.is_empty() || action.contains(' ') {
                continue;
            }
            // Bindings may carry a leading `[command args]`; keep only the keys.
            let value = value.trim().rsplit(']').next().unwrap_or("").trim();
            let rendered: Vec<String> = if value == "none" {
                Vec::new()
            } else {
                value.split_whitespace().filter_map(|combo| keys::plus_separated(combo, Case::Insensitive)).collect()
            };
            table.insert((section.clone(), action.to_string()), rendered);
        }
    };
    apply(defaults, true);
    if let Some(user) = user {
        apply(user, false);
    }
    table
        .into_iter()
        .filter(|(_, rendered)| !rendered.is_empty())
        .map(|((section, action), rendered)| Shortcut {
            title: humanize(&action),
            keys: rendered,
            aliases: Vec::new(),
            section: Some(humanize(&section)),
            action: Some(action),
            site: None,
        })
        .collect()
}

/// mpv `input.conf`: `KEY command  # description`. The shipped copy comments out
/// every default; a user file replaces individual keys.
pub fn mpv(defaults: &str, user: Option<&str>) -> Vec<Shortcut> {
    let mut table: BTreeMap<String, (String, String)> = BTreeMap::new();
    let mut apply = |text: &str, commented_defaults: bool| {
        for raw in text.lines() {
            let mut line = raw.trim();
            if commented_defaults {
                // `#KEY command`: one leading hash is the commented default;
                // `##` and `# ` start prose.
                let Some(rest) = line.strip_prefix('#') else { continue };
                if rest.starts_with('#') || rest.starts_with(' ') || rest.is_empty() {
                    continue;
                }
                line = rest;
            } else if line.starts_with('#') || line.is_empty() {
                continue;
            }
            let (binding, description) = match line.split_once(" #") {
                Some((binding, description)) => (binding.trim(), description.trim().to_string()),
                None => (line, String::new()),
            };
            let mut parts = binding.splitn(2, char::is_whitespace);
            let (Some(key), Some(command)) = (parts.next(), parts.next()) else { continue };
            let command = command.trim().to_string();
            let key = if key == "SHARP" { "#" } else { key };
            let Some(rendered) = keys::plus_separated(key, Case::Sensitive) else { continue };
            if command == "ignore" {
                table.remove(&rendered);
            } else {
                table.insert(rendered, (command, description));
            }
        }
    };
    apply(defaults, true);
    if let Some(user) = user {
        apply(user, false);
    }
    group_by_title(table.into_iter().map(|(key, (command, description))| {
        let title = upper_first(if description.is_empty() { &command } else { &description });
        (title, key, command)
    }))
}

/// imv `[binds]`: `<Ctrl+r> = rotate by 90`, `gg = goto 1`. Built-in defaults
/// apply unless the user config sets `suppress_default_binds = true`.
pub fn imv(defaults: &str, user: Option<&str>) -> Vec<Shortcut> {
    let suppress = user.is_some_and(|text| {
        text.lines().any(|line| {
            let line = line.trim().replace(' ', "");
            line == "suppress_default_binds=true"
        })
    });
    let mut table: BTreeMap<String, String> = BTreeMap::new();
    let mut apply = |text: &str| {
        let mut in_binds = false;
        for raw in text.lines() {
            let line = raw.trim();
            if line.starts_with('[') {
                in_binds = line == "[binds]";
                continue;
            }
            if !in_binds || line.starts_with('#') {
                continue;
            }
            let Some((binding, command)) = line.split_once(" = ").or_else(|| line.split_once('=')) else { continue };
            let Some(rendered) = imv_keys(binding.trim()) else { continue };
            table.insert(rendered, command.trim().to_string());
        }
    };
    // The shipped imv_config mirrors the built-in binds, plus `exec` examples
    // (`y = exec echo working!`) that imv does not actually bind.
    if !suppress {
        let builtin: String = defaults.lines().filter(|line| !line.contains("= exec ")).collect::<Vec<_>>().join("\n");
        apply(&builtin);
    }
    if let Some(user) = user {
        apply(user);
    }
    // Built-in commands use underscores (`next_frame`); `exec` lines are shell.
    group_by_title(table.into_iter().map(|(key, command)| {
        let title = if command.starts_with("exec ") { command.clone() } else { command.replace('_', " ") };
        (upper_first(&title), key, command)
    }))
}

/// `gg` is two presses of g; `<Shift+G>` one chord; `<Ctrl+x>` one chord.
fn imv_keys(binding: &str) -> Option<String> {
    let mut chords = Vec::new();
    let mut rest = binding;
    while !rest.is_empty() {
        if let Some(stripped) = rest.strip_prefix('<') {
            let end = stripped.find('>')?;
            chords.push(keys::plus_separated(&stripped[..end], Case::Sensitive)?);
            rest = &stripped[end + 1..];
        } else {
            let ch = rest.chars().next()?;
            chords.push(ch.to_string());
            rest = &rest[ch.len_utf8()..];
        }
    }
    (!chords.is_empty()).then(|| chords.join(" "))
}

/// lazygit `--config`: the `keybinding:` block. Contexts sit at indent 4,
/// actions at indent 8, and multi-key actions list their keys beneath.
pub fn lazygit(config: &str) -> Vec<Shortcut> {
    let mut shortcuts = Vec::new();
    let mut in_keybinding = false;
    let mut context = String::new();
    let mut list: Option<(String, Vec<String>)> = None;
    for raw in config.lines() {
        let indent = raw.len() - raw.trim_start().len();
        let line = raw.trim();
        if line.is_empty() {
            continue;
        }
        if let Some(item) = line.strip_prefix("- ") {
            if let Some((_, rendered)) = list.as_mut() {
                rendered.extend(lazygit_key(item));
            }
            continue;
        }
        if let Some((action, rendered)) = list.take() {
            if !rendered.is_empty() {
                shortcuts.push(lazygit_shortcut(&context, &action, rendered));
            }
        }
        if indent == 0 {
            in_keybinding = line == "keybinding:";
            continue;
        }
        let Some((name, value)) = line.split_once(':') else { continue };
        if !in_keybinding {
            continue;
        }
        let value = value.trim();
        match (indent, value.is_empty()) {
            (4, true) => context = name.to_string(),
            (_, true) => list = Some((name.to_string(), Vec::new())),
            (_, false) => {
                if let Some(rendered) = lazygit_key(value) {
                    shortcuts.push(lazygit_shortcut(&context, name, vec![rendered]));
                }
            }
        }
    }
    if let Some((action, rendered)) = list {
        if !rendered.is_empty() {
            shortcuts.push(lazygit_shortcut(&context, &action, rendered));
        }
    }
    shortcuts
}

fn lazygit_shortcut(context: &str, action: &str, rendered: Vec<String>) -> Shortcut {
    // `quit-alt1` and `nextItem-alt` are alternative keys for `quit`, `nextItem`.
    let base = action.split("-alt").next().unwrap_or(action);
    Shortcut {
        title: humanize(base),
        keys: rendered,
        aliases: Vec::new(),
        section: (!context.is_empty()).then(|| humanize(context)),
        action: Some(format!("{context}.{action}")),
        site: None,
    }
}

fn lazygit_key(value: &str) -> Option<String> {
    let value = value.trim().trim_matches(|c| c == '\'' || c == '"');
    if value.is_empty() || value == "<disabled>" {
        return None;
    }
    match value.strip_prefix('<').and_then(|rest| rest.strip_suffix('>')) {
        Some(inner) => keys::plus_separated(inner, Case::Sensitive),
        None => keys::key_name(value, Case::Sensitive),
    }
}

/// Output of Omarchy's `omarchy-menu-{tmux,herdr}-keybindings --print`:
/// `PREFIX + c   → Create window`, with the first line defining PREFIX.
/// Alternatives are separated by ` / ` (`PREFIX + P / ALT + LEFT`), and a
/// digit range keeps its `1..9` form (`ALT + 1..9`).
pub fn omarchy_menu(text: &str) -> Vec<Shortcut> {
    let mut prefix: Option<String> = None;
    let mut shortcuts = Vec::new();
    for line in text.lines() {
        let Some((binding, title)) = line.split_once('→') else { continue };
        let (binding, title) = (binding.trim(), title.trim());
        if binding == "PREFIX" {
            prefix = title.split(" / ").next().and_then(omarchy_chord);
            continue;
        }
        let mut keys = Vec::new();
        let mut section = None;
        for alternative in binding.split(" / ") {
            let Some((key, mode)) = omarchy_binding(alternative.trim(), prefix.as_deref()) else { continue };
            if keys.is_empty() {
                section = mode;
            }
            if !keys.contains(&key) {
                keys.push(key);
            }
        }
        if keys.is_empty() {
            continue;
        }
        shortcuts.push(Shortcut { title: title.to_string(), keys, aliases: Vec::new(), section, action: None, site: None });
    }
    shortcuts
}

/// One alternative of a menu binding, rendered, with the mode it needs.
fn omarchy_binding(binding: &str, prefix: Option<&str>) -> Option<(String, Option<String>)> {
    let (mode, chord_text) = match binding.split_once(" + ") {
        Some((mode, rest)) if mode.chars().all(|c| c.is_ascii_uppercase() || c == ' ') && modifier_word(mode).is_none() => {
            (Some(mode), rest)
        }
        _ => (None, binding),
    };
    let chord = omarchy_chord(chord_text)?;
    Some(match mode {
        Some("PREFIX") => (format!("{}, {chord}", prefix?), None),
        Some(mode) => {
            let name = upper_first(&mode.to_lowercase());
            let section = if name.ends_with(" mode") { name } else { format!("{name} mode") };
            (chord, Some(section))
        }
        None => (chord, None),
    })
}

fn modifier_word(word: &str) -> Option<keys::Modifier> {
    keys::modifier(word)
}

/// `CTRL + ALT + SHIFT + LEFT`, `c`, `?` — letters keep their case.
fn omarchy_chord(text: &str) -> Option<String> {
    let parts: Vec<&str> = text.split(" + ").map(str::trim).collect();
    let (key, modifier_parts) = parts.split_last()?;
    let mut modifiers = Vec::new();
    for part in modifier_parts {
        modifiers.push(modifier_word(part)?);
    }
    keys::chord(&modifiers, key, Case::Sensitive)
}

fn upper_first(text: &str) -> String {
    let mut chars = text.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}

/// Collapse key → command rows into one shortcut per title with every key.
fn group_by_title(rows: impl Iterator<Item = (String, String, String)>) -> Vec<Shortcut> {
    let mut grouped: Vec<Shortcut> = Vec::new();
    for (title, key, command) in rows {
        match grouped.iter_mut().find(|shortcut| shortcut.title == title) {
            Some(existing) => existing.keys.push(key),
            None => grouped.push(Shortcut { title, keys: vec![key], aliases: Vec::new(), section: None, action: Some(command), site: None }),
        }
    }
    grouped
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn humanizes_identifiers() {
        assert_eq!(humanize("clipboard-copy"), "Clipboard copy");
        assert_eq!(humanize("quitWithoutChangingDirectory"), "Quit without changing directory");
    }

    #[test]
    fn foot_user_config_overrides_and_unbinds_defaults() {
        let defaults = "[key-bindings]\n# clipboard-copy=Control+Shift+c XF86Copy\n# primary-paste=Shift+Insert\n# minimize=none\n[search-bindings]\n# cancel=Control+g Control+c Escape\n";
        let user = "[key-bindings]\nclipboard-copy=Control+Insert Control+Shift+c\nprimary-paste=none\n";

        let shortcuts = foot(defaults, Some(user));

        let rows: Vec<_> = shortcuts.iter().map(|s| (s.title.as_str(), s.keys.join(" / "))).collect();
        assert_eq!(rows, vec![("Clipboard copy", "Ctrl+Insert / Ctrl+Shift+C".to_string()), ("Cancel", "Ctrl+G / Ctrl+C / Esc".to_string())]);
    }

    #[test]
    fn mpv_defaults_group_keys_and_respect_ignore() {
        let defaults = "# mpv keybindings\n#\n## Seek\n#RIGHT seek  5    # seek 5 seconds forward\n#q quit\n#Q quit-watch-later  # exit and remember the playback position\n#MBTN_LEFT ignore\n#SPACE cycle pause # toggle pause/playback mode\n#p cycle pause # toggle pause/playback mode\n";
        let user = "q ignore\n";

        let shortcuts = mpv(defaults, Some(user));

        let rows: Vec<_> = shortcuts.iter().map(|s| (s.title.as_str(), s.keys.join(" / "))).collect();
        assert!(rows.contains(&("Seek 5 seconds forward", "Right".to_string())));
        assert!(rows.contains(&("Toggle pause/playback mode", "Space / p".to_string())));
        assert!(rows.contains(&("Exit and remember the playback position", "Q".to_string())));
        assert!(!rows.iter().any(|(title, _)| *title == "quit"));
    }

    #[test]
    fn imv_sequences_chords_and_suppression() {
        let defaults = "[binds]\nq = quit\ny = exec echo working!\ngg = goto 1\n<Shift+G> = goto -1\n";
        let user = "[binds]\n<Ctrl+r> = rotate by 90\n";

        let merged = imv(defaults, Some(user));
        let suppressed = imv(defaults, Some("[options]\nsuppress_default_binds = true\n[binds]\nx = close\n"));

        let rows: Vec<_> = merged.iter().map(|s| (s.title.as_str(), s.keys.join(" / "))).collect();
        assert!(rows.contains(&("Goto 1", "g g".to_string())));
        assert!(rows.contains(&("Goto -1", "Shift+G".to_string())));
        assert!(rows.contains(&("Rotate by 90", "Ctrl+R".to_string())));
        assert!(!rows.iter().any(|(title, _)| title.starts_with("Exec echo")));
        assert_eq!(suppressed.len(), 1);
    }

    #[test]
    fn lazygit_contexts_lists_and_alternatives() {
        let config = "gui:\n    scrollHeight: 2\nkeybinding:\n    universal:\n        quit: q\n        quit-alt1: <ctrl+c>\n        prevPage: ','\n        jumpToBlock:\n            - \"1\"\n            - \"2\"\n        optionMenu: <disabled>\n    files:\n        commitChanges: c\nos:\n    edit: vim\n";

        let shortcuts = lazygit(config);

        let rows: Vec<_> = shortcuts.iter().map(|s| (s.section.clone().unwrap_or_default(), s.title.clone(), s.keys.join(" / "))).collect();
        assert_eq!(rows, vec![
            ("Universal".into(), "Quit".into(), "q".into()),
            ("Universal".into(), "Quit".into(), "Ctrl+C".into()),
            ("Universal".into(), "Prev page".into(), ",".into()),
            ("Universal".into(), "Jump to block".into(), "1 / 2".into()),
            ("Files".into(), "Commit changes".into(), "c".into()),
        ]);
    }

    #[test]
    fn omarchy_menu_prefix_modes_and_case() {
        let text = "PREFIX                           → CTRL + SPACE / CTRL + B\nPREFIX + C                       → Create session\nPREFIX + c                       → Create window\nCTRL + ALT + SHIFT + LEFT        → Resize pane left\nCOPY MODE + v                    → Begin selection\n";

        let shortcuts = omarchy_menu(text);

        let rows: Vec<_> = shortcuts.iter().map(|s| (s.title.as_str(), s.keys[0].as_str(), s.section.clone())).collect();
        assert_eq!(rows, vec![
            ("Create session", "Ctrl+Space, C", None),
            ("Create window", "Ctrl+Space, c", None),
            ("Resize pane left", "Ctrl+Alt+Shift+Left", None),
            ("Begin selection", "v", Some("Copy mode".to_string())),
        ]);
    }

    #[test]
    fn omarchy_menu_alternatives_and_ranges() {
        let text = "PREFIX                           → CTRL + SPACE\nPREFIX + P / ALT + LEFT          → Previous tab\nPREFIX + 1..9 / ALT + 1..9       → Switch tab\nPREFIX + X / ALT + ESC           → Close pane\n";

        let shortcuts = omarchy_menu(text);

        let rows: Vec<_> = shortcuts.iter().map(|s| (s.title.as_str(), s.keys.join(" / "))).collect();
        assert_eq!(rows, vec![
            ("Previous tab", "Ctrl+Space, P / Alt+Left".to_string()),
            ("Switch tab", "Ctrl+Space, 1..9 / Alt+1..9".to_string()),
            ("Close pane", "Ctrl+Space, X / Alt+Esc".to_string()),
        ]);
    }
}
