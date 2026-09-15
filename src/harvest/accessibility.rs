//! Shortcuts read from a running application's accessibility tree.
//!
//! Toolkits without a static shortcut source still tell assistive technology
//! which keys trigger a control: Qt and GTK report the accelerator of every
//! menu item (including items of menus that are closed), web engines report
//! `aria-keyshortcuts`, and many applications put the shortcut in a tooltip,
//! such as `Play (k)` or `Toggle sidebar (Ctrl+B)`.

use std::collections::{BTreeMap, BTreeSet};

use crate::keys::{self, Case};
use crate::model::Shortcut;

/// The parts of one accessible object that can declare a shortcut.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Control {
    pub name: String,
    pub description: String,
    /// Key bindings the toolkit reports for the object's actions.
    pub key_bindings: Vec<String>,
    /// Host of the web page containing the control, `Some("")` for a document
    /// without one, and `None` for native interface.
    pub site: Option<String>,
    /// The control is an item inside a menu, where toolkits report the
    /// mnemonic (`Alt+N` for `Read-O&nly`) when no shortcut is assigned. A
    /// mnemonic only works while that menu is open, so it is not a shortcut.
    pub in_menu: bool,
}

pub fn shortcuts(controls: &[Control]) -> Vec<Shortcut> {
    let mut found = Vec::new();
    for control in controls {
        let name = control.name.trim();
        let mut bound: Vec<String> = control.key_bindings.iter().flat_map(|binding| key_binding(binding)).collect();
        if control.in_menu {
            bound.retain(|keys| !is_mnemonic(keys, name));
        }
        if !name.is_empty() && !bound.is_empty() {
            let (title, aliases) = match tooltip_hint(name) {
                Some((title, _)) => (title, vec![name.to_string()]),
                None => (name.to_string(), Vec::new()),
            };
            found.push(shortcut(title, bound, aliases, &control.site));
            continue;
        }
        for text in [name, control.description.trim()] {
            if let Some((title, keys)) = tooltip_hint(text) {
                let mut aliases = vec![text.to_string()];
                if !name.is_empty() && name != text && name != title {
                    aliases.push(name.to_string());
                }
                found.push(shortcut(title, vec![keys], aliases, &control.site));
                break;
            }
        }
    }
    // The same title may be captured on different sites; keep one per site.
    let mut seen = BTreeSet::new();
    found.retain(|shortcut| seen.insert((shortcut.title.to_lowercase(), shortcut.keys.clone(), shortcut.site.clone())));
    // A binding repeated on differently named controls (GitHub puts
    // `Alt+ArrowUp` on every link of a list) moves within a region; it does
    // not activate any one of them.
    let mut titles: BTreeMap<(&Option<String>, &Vec<String>), BTreeSet<String>> = BTreeMap::new();
    for shortcut in &found {
        titles.entry((&shortcut.site, &shortcut.keys)).or_default().insert(shortcut.title.to_lowercase());
    }
    let shared: BTreeSet<(Option<String>, Vec<String>)> =
        titles.into_iter().filter(|(_, names)| names.len() > 1).map(|((site, keys), _)| (site.clone(), keys.clone())).collect();
    found.retain(|shortcut| !shared.contains(&(shortcut.site.clone(), shortcut.keys.clone())));
    found
}

fn shortcut(title: String, keys: Vec<String>, aliases: Vec<String>, site: &Option<String>) -> Shortcut {
    Shortcut { title, keys, aliases, section: None, action: None, site: site.clone() }
}

/// Parse a key binding in any of the forms toolkits report:
///
/// * ATK's `mnemonic;mnemonic-path;accelerator` triple (`s;<Alt>f:s;<Control>s`),
///   of which only the accelerator works from anywhere in the window;
/// * GTK accelerator syntax (`<Primary>s`);
/// * Qt's portable text, with `, ` between the chords of a sequence (`Ctrl+K, Ctrl+D`);
/// * `aria-keyshortcuts`, with alternatives separated by spaces (`Control+S Meta+S`).
pub fn key_binding(binding: &str) -> Vec<String> {
    let binding = binding.trim();
    if binding.is_empty() {
        return Vec::new();
    }
    if binding.contains(';') {
        return binding.split(';').nth(2).map(key_binding).unwrap_or_default();
    }
    if binding.starts_with('<') {
        return keys::gtk_accelerators(binding);
    }
    if binding.contains(", ") {
        let chords: Option<Vec<String>> = binding.split(", ").map(|chord| keys::plus_separated(chord, Case::Insensitive)).collect();
        return chords.map(|chords| vec![chords.join(", ")]).unwrap_or_default();
    }
    binding.split_whitespace().filter_map(|chord| keys::plus_separated(chord, Case::Insensitive)).collect()
}

/// `Alt` plus a letter or digit that appears in the label.
fn is_mnemonic(keys: &str, label: &str) -> bool {
    let Some(key) = keys.strip_prefix("Alt+") else { return false };
    let mut chars = key.chars();
    match (chars.next(), chars.next()) {
        (Some(letter), None) if letter.is_alphanumeric() => label.to_uppercase().contains(letter.to_ascii_uppercase()),
        _ => false,
    }
}

const HINT_KEYS: &[&str] = &[
    "esc", "escape", "enter", "return", "tab", "space", "backspace", "delete", "del", "insert", "home", "end",
    "pageup", "pagedown", "page up", "page down", "up", "down", "left", "right",
];

/// Split a label that ends with its shortcut in parentheses: `Play (k)`,
/// `Next (SHIFT+n)`, `Toggle sidebar (Ctrl+B)`. Counts, versions and other
/// parenthesized words are rejected: the text must be a single character,
/// a function key, a named navigation key, or a chord with a modifier.
pub fn tooltip_hint(text: &str) -> Option<(String, String)> {
    let text = text.trim();
    let inner_end = text.strip_suffix(')')?;
    let open = inner_end.rfind('(')?;
    let title = inner_end[..open].trim_end().trim_end_matches([':', '-', '–', '—']).trim_end();
    let inner = inner_end[open + 1..].trim();
    if title.is_empty() || inner.is_empty() {
        return None;
    }
    let keys = hint_keys(inner)?;
    Some((title.to_string(), keys))
}

fn hint_keys(inner: &str) -> Option<String> {
    let mut parts: Vec<&str> = inner.split('+').map(str::trim).collect();
    // `Ctrl++` splits into a trailing pair of empty parts.
    let key = if inner.ends_with("++") {
        parts.truncate(parts.len() - 2);
        "+"
    } else {
        parts.pop()?
    };
    let mut modifiers = Vec::new();
    for part in &parts {
        modifiers.push(keys::modifier(part)?);
    }
    let single = key.chars().count() == 1;
    if modifiers.is_empty() {
        let lower = key.to_ascii_lowercase();
        let function_key = lower.len() <= 3 && lower.starts_with('f') && lower.len() > 1 && lower[1..].chars().all(|c| c.is_ascii_digit());
        let plain = (single && !key.chars().all(|c| c.is_ascii_digit() || c.is_whitespace())) || function_key || HINT_KEYS.contains(&lower.as_str());
        if !plain {
            return None;
        }
    }
    let key = key.replace(' ', "_");
    if single && modifiers.is_empty() {
        return keys::known_key_name(&key, Case::Sensitive);
    }
    keys::chord(&modifiers, &known(&key)?, Case::Insensitive)
}

fn known(key: &str) -> Option<String> {
    keys::known_key_name(key, Case::Insensitive).map(|_| key.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn control(name: &str, description: &str, bindings: &[&str]) -> Control {
        Control {
            name: name.into(),
            description: description.into(),
            key_bindings: bindings.iter().map(|b| b.to_string()).collect(),
            site: None,
            in_menu: false,
        }
    }

    #[test]
    fn toolkit_key_binding_forms() {
        assert_eq!(key_binding("Ctrl+O"), vec!["Ctrl+O"]);
        assert_eq!(key_binding("Ctrl+K, Ctrl+D"), vec!["Ctrl+K, Ctrl+D"]);
        assert_eq!(key_binding("s;<Alt>f:s;<Control>s"), vec!["Ctrl+S"]);
        assert_eq!(key_binding("s;<Alt>f:s;"), Vec::<String>::new());
        assert_eq!(key_binding("<Primary><Shift>z"), vec!["Ctrl+Shift+Z"]);
        assert_eq!(key_binding("Control+S Meta+S"), vec!["Ctrl+S", "Super+S"]);
        assert_eq!(key_binding(""), Vec::<String>::new());
    }

    #[test]
    fn tooltip_hints_accept_shortcuts_and_reject_other_parentheses() {
        assert_eq!(tooltip_hint("Play (k)"), Some(("Play".into(), "k".into())));
        assert_eq!(tooltip_hint("Next (SHIFT+n)"), Some(("Next".into(), "Shift+N".into())));
        assert_eq!(tooltip_hint("Toggle sidebar (Ctrl+B)"), Some(("Toggle sidebar".into(), "Ctrl+B".into())));
        assert_eq!(tooltip_hint("Zoom in (Ctrl++)"), Some(("Zoom in".into(), "Ctrl++".into())));
        assert_eq!(tooltip_hint("Exit full screen (Esc)"), Some(("Exit full screen".into(), "Esc".into())));
        assert_eq!(tooltip_hint("Search (/)"), Some(("Search".into(), "/".into())));
        assert_eq!(tooltip_hint("Help (F1)"), Some(("Help".into(), "F1".into())));
        assert_eq!(tooltip_hint("Comments (3)"), None);
        assert_eq!(tooltip_hint("Settings (Beta)"), None);
        assert_eq!(tooltip_hint("Inbox (1 of 5)"), None);
        assert_eq!(tooltip_hint("Merge (Shift+Something)"), None);
        assert_eq!(tooltip_hint("(k)"), None);
        assert_eq!(tooltip_hint("Next page"), None);
    }

    #[test]
    fn menu_mnemonics_are_not_shortcuts() {
        let menu_item = |name: &str, binding: &str| Control { in_menu: true, ..control(name, "", &[binding]) };
        let found = shortcuts(&[
            menu_item("Open Read-Only...", "Alt+N"),
            menu_item("Find Next", "F3"),
            menu_item("Reset Zoom", "Alt+0"),
            control("File", "", &["Alt+F"]),
        ]);
        let rendered: Vec<(String, Vec<String>)> = found.into_iter().map(|s| (s.title, s.keys)).collect();
        assert_eq!(
            rendered,
            vec![
                ("Find Next".into(), vec!["F3".into()]),
                ("Reset Zoom".into(), vec!["Alt+0".into()]),
                ("File".into(), vec!["Alt+F".into()]),
            ]
        );
    }

    #[test]
    fn collects_bindings_and_hints() {
        let found = shortcuts(&[
            control("Open...", "", &["Ctrl+O"]),
            control("Recently Opened Files", "", &[""]),
            control("Play (k)", "", &[]),
            control("Sidebar", "Toggle sidebar (Ctrl+B)", &[]),
            control("Save", "", &["Ctrl+S"]),
            control("Save", "", &["Ctrl+S"]),
            control("commits by alice", "", &["Alt+ArrowUp"]),
            control("commits by bob", "", &["Alt+ArrowUp"]),
        ]);
        let rendered: Vec<(String, Vec<String>, Vec<String>)> = found.into_iter().map(|s| (s.title, s.keys, s.aliases)).collect();
        assert_eq!(
            rendered,
            vec![
                ("Open...".into(), vec!["Ctrl+O".into()], vec![]),
                ("Play".into(), vec!["k".into()], vec!["Play (k)".into()]),
                ("Toggle sidebar".into(), vec!["Ctrl+B".into()], vec!["Toggle sidebar (Ctrl+B)".into(), "Sidebar".into()]),
                ("Save".into(), vec!["Ctrl+S".into()], vec![]),
            ]
        );
    }
}
