//! Shortcut harvesters, one per kind of source.

pub mod accessibility;
pub mod gtk;
pub mod libreoffice;
pub mod t3code;
pub mod terminal;

use std::collections::BTreeSet;

use crate::model::Shortcut;

/// Drop repeated shortcuts (the same title and keys declared in several
/// builder files) while keeping the first occurrence's order and section.
pub fn dedupe(shortcuts: Vec<Shortcut>) -> Vec<Shortcut> {
    let mut seen = BTreeSet::new();
    shortcuts
        .into_iter()
        .filter(|shortcut| seen.insert((shortcut.title.to_lowercase(), shortcut.keys.clone())))
        .collect()
}
