//! KDE applications do not ship a shortcut file. Their menus are built from
//! `KStandardAction`, whose keys come from `KStandardShortcut` — a documented,
//! stable set every KXmlGui application inherits — and the `.rc` files that
//! describe their menus carry no keys at all.
//!
//! So the index is the standard set, with the user's own reassignments layered
//! on top: KDE writes those into the `[Shortcuts]` group of the application's
//! own config file, and into `kdeglobals` for the ones shared by every app.
//! Actions an application adds itself are not covered here; the accessibility
//! capture reads those from its menus while it runs.

use std::collections::BTreeMap;

use crate::keys::{self, Case};
use crate::model::Shortcut;

/// One `KStandardAction`: its action name, the label its menu item carries,
/// other labels applications use for it, and its default keys.
struct Standard {
    action: &'static str,
    title: &'static str,
    aliases: &'static [&'static str],
    keys: &'static [&'static str],
}

/// `KStandardShortcut`'s defaults. Actions without a default key are listed so
/// a user's reassignment is still recognised.
const STANDARD: &[Standard] = &[
    Standard { action: "file_new", title: "New", aliases: &["New File", "New Window"], keys: &["Ctrl+N"] },
    Standard { action: "file_open", title: "Open", aliases: &["Open File", "Open..."], keys: &["Ctrl+O"] },
    Standard { action: "file_open_recent", title: "Open Recent", aliases: &[], keys: &[] },
    Standard { action: "file_save", title: "Save", aliases: &[], keys: &["Ctrl+S"] },
    Standard { action: "file_save_as", title: "Save As", aliases: &["Save As..."], keys: &["Ctrl+Shift+S"] },
    Standard { action: "file_revert", title: "Revert", aliases: &[], keys: &[] },
    Standard { action: "file_close", title: "Close", aliases: &["Close Window"], keys: &["Ctrl+W"] },
    Standard { action: "file_print", title: "Print", aliases: &["Print..."], keys: &["Ctrl+P"] },
    Standard { action: "file_print_preview", title: "Print Preview", aliases: &[], keys: &[] },
    Standard { action: "file_quit", title: "Quit", aliases: &["Exit"], keys: &["Ctrl+Q"] },
    Standard { action: "edit_undo", title: "Undo", aliases: &[], keys: &["Ctrl+Z"] },
    Standard { action: "edit_redo", title: "Redo", aliases: &[], keys: &["Ctrl+Shift+Z"] },
    Standard { action: "edit_cut", title: "Cut", aliases: &[], keys: &["Ctrl+X"] },
    Standard { action: "edit_copy", title: "Copy", aliases: &[], keys: &["Ctrl+C"] },
    Standard { action: "edit_paste", title: "Paste", aliases: &[], keys: &["Ctrl+V"] },
    Standard { action: "edit_select_all", title: "Select All", aliases: &[], keys: &["Ctrl+A"] },
    Standard { action: "edit_deselect", title: "Deselect", aliases: &[], keys: &["Ctrl+Shift+A"] },
    Standard { action: "edit_find", title: "Find", aliases: &["Find..."], keys: &["Ctrl+F"] },
    Standard { action: "edit_find_next", title: "Find Next", aliases: &[], keys: &["F3"] },
    Standard { action: "edit_find_last", title: "Find Previous", aliases: &[], keys: &["Shift+F3"] },
    Standard { action: "edit_replace", title: "Replace", aliases: &["Replace..."], keys: &["Ctrl+R"] },
    Standard { action: "view_zoom_in", title: "Zoom In", aliases: &[], keys: &["Ctrl++"] },
    Standard { action: "view_zoom_out", title: "Zoom Out", aliases: &[], keys: &["Ctrl+-"] },
    Standard { action: "view_actual_size", title: "Actual Size", aliases: &["Zoom to 100%"], keys: &["Ctrl+0"] },
    Standard { action: "view_fit_to_page", title: "Fit to Page", aliases: &[], keys: &[] },
    Standard { action: "view_redisplay", title: "Redisplay", aliases: &["Reload", "Refresh"], keys: &["F5"] },
    Standard { action: "fullscreen", title: "Full Screen Mode", aliases: &["Full Screen"], keys: &["Ctrl+Shift+F"] },
    Standard { action: "go_up", title: "Up", aliases: &[], keys: &["Alt+Up"] },
    Standard { action: "go_back", title: "Back", aliases: &[], keys: &["Alt+Left"] },
    Standard { action: "go_forward", title: "Forward", aliases: &[], keys: &["Alt+Right"] },
    Standard { action: "go_home", title: "Home", aliases: &[], keys: &["Alt+Home"] },
    Standard { action: "go_goto_line", title: "Go to Line", aliases: &["Go to Line..."], keys: &["Ctrl+G"] },
    Standard { action: "add_bookmark", title: "Add Bookmark", aliases: &[], keys: &["Ctrl+B"] },
    Standard { action: "options_show_menubar", title: "Show Menubar", aliases: &["Hide Menubar"], keys: &["Ctrl+M"] },
    Standard { action: "options_show_toolbar", title: "Show Toolbar", aliases: &[], keys: &[] },
    Standard { action: "options_configure", title: "Configure", aliases: &["Settings", "Preferences"], keys: &["Ctrl+Shift+,"] },
    Standard { action: "options_configure_keybinding", title: "Configure Keyboard Shortcuts", aliases: &[], keys: &[] },
    Standard { action: "help_contents", title: "Handbook", aliases: &["Help"], keys: &["F1"] },
    Standard { action: "help_whats_this", title: "What's This?", aliases: &[], keys: &["Shift+F1"] },
];

/// A `[Shortcuts]` value: `Ctrl+S`, several alternatives separated by `;`, or
/// `none` for an action the user unbound. KDE appends the shipped default
/// after a tab when the entry differs from it; only the live keys are kept.
fn parse_value(value: &str) -> Vec<String> {
    let live = value.split('\t').next().unwrap_or_default();
    live.split(';')
        .map(str::trim)
        .filter(|part| !part.is_empty() && !part.eq_ignore_ascii_case("none"))
        .filter_map(|part| keys::plus_separated(part, Case::Insensitive))
        .collect()
}

/// Read the `[Shortcuts]` group of a KDE config file.
pub fn overrides(text: &str) -> BTreeMap<String, Vec<String>> {
    let mut found = BTreeMap::new();
    let mut in_group = false;
    for line in text.lines() {
        let line = line.trim();
        if line.starts_with('[') {
            in_group = line == "[Shortcuts]";
            continue;
        }
        if !in_group || line.starts_with('#') {
            continue;
        }
        let Some((name, value)) = line.split_once('=') else { continue };
        // `name[$e]` marks an expandable value; the action is the part before.
        let name = name.trim().split('[').next().unwrap_or_default().trim();
        if name.is_empty() {
            continue;
        }
        found.insert(name.to_string(), parse_value(value));
    }
    found
}

/// The standard actions of one KDE application, after its own overrides and
/// the ones shared by every KDE application.
pub fn shortcuts(application_rc: Option<&str>, kdeglobals: Option<&str>) -> Vec<Shortcut> {
    let mut reassigned = kdeglobals.map(overrides).unwrap_or_default();
    reassigned.extend(application_rc.map(overrides).unwrap_or_default());
    STANDARD
        .iter()
        .map(|standard| {
            let keys = match reassigned.get(standard.action) {
                Some(keys) => keys.clone(),
                None => standard.keys.iter().filter_map(|key| keys::plus_separated(key, Case::Insensitive)).collect(),
            };
            Shortcut {
                title: standard.title.to_string(),
                keys,
                aliases: standard.aliases.iter().map(|alias| alias.to_string()).collect(),
                section: None,
                action: Some(standard.action.to_string()),
                site: None,
            }
        })
        .filter(|shortcut| !shortcut.keys.is_empty())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn standard_actions_carry_their_documented_keys() {
        let found = shortcuts(None, None);
        let save = found.iter().find(|shortcut| shortcut.action.as_deref() == Some("file_save")).unwrap();
        assert_eq!(save.keys, vec!["Ctrl+S"]);
        assert_eq!(save.title, "Save");
        // An action with no default key is not offered as a suggestion.
        assert!(found.iter().all(|shortcut| shortcut.action.as_deref() != Some("file_revert")));
    }

    #[test]
    fn user_reassignment_replaces_the_default() {
        let found = shortcuts(Some("[Shortcuts]\nfile_save=Ctrl+Alt+S; Meta+S\nedit_find=none\n"), None);
        let save = found.iter().find(|shortcut| shortcut.action.as_deref() == Some("file_save")).unwrap();
        assert_eq!(save.keys, vec!["Ctrl+Alt+S", "Super+S"]);
        assert!(found.iter().all(|shortcut| shortcut.action.as_deref() != Some("edit_find")));
    }

    #[test]
    fn the_application_outranks_the_shared_defaults() {
        let found = shortcuts(Some("[Shortcuts]\nedit_copy=Ctrl+Insert\n"), Some("[Shortcuts]\nedit_copy=Ctrl+Shift+C\nedit_paste=Ctrl+Shift+V\n"));
        let by_action = |action: &str| {
            found.iter().find(|shortcut| shortcut.action.as_deref() == Some(action)).unwrap().keys.clone()
        };
        assert_eq!(by_action("edit_copy"), vec!["Ctrl+Insert"]);
        assert_eq!(by_action("edit_paste"), vec!["Ctrl+Shift+V"]);
    }

    #[test]
    fn other_groups_are_ignored() {
        let found = overrides("[General]\nfile_save=Ctrl+X\n[Shortcuts]\nfile_open[$e]=Ctrl+Y\n");
        assert_eq!(found.get("file_open"), Some(&vec!["Ctrl+Y".to_string()]));
        assert!(!found.contains_key("file_save"));
    }

    #[test]
    fn a_reassignment_keeps_only_the_live_keys() {
        assert_eq!(parse_value("Ctrl+Alt+S\tCtrl+S"), vec!["Ctrl+Alt+S"]);
    }
}
