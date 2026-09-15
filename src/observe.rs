//! Capture shortcuts from a running application and keep them in the
//! observed index, which accumulates across captures: a web app's tooltips or
//! a menu that appears only in some views are remembered after they are gone.

use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::Path;

use anyhow::{Context, Result, bail};

use crate::atspi::{Bus, Limits, Node, ObjectRef, role};
use crate::harvest::accessibility::{self, Control};
use crate::model::{App, Index, SCHEMA_VERSION, Shortcut, SourceReport};
use crate::store;

const ACTION_INTERFACE: &str = "org.a11y.atspi.Action";
/// Entries kept per application; the newest captures win.
const MAX_SHORTCUTS: usize = 4000;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Observation {
    pub toolkit: String,
    pub nodes: usize,
    pub shortcuts: Vec<Shortcut>,
}

/// Read every accessible application owned by `pid`. A process can register
/// more than one (Quickshell registers a GTK and a Qt application).
pub fn observe_pid(bus: &Bus, pid: u32, limits: Limits) -> Result<Observation> {
    let applications: Vec<ObjectRef> =
        bus.applications()?.into_iter().filter(|application| bus.pid(&application.bus).ok() == Some(pid)).collect();
    if applications.is_empty() {
        bail!("process {pid} has no accessible application; accessibility may be disabled for it");
    }
    let mut toolkits = BTreeSet::new();
    let mut nodes = 0;
    let mut controls = Vec::new();
    for application in &applications {
        if let Ok(toolkit) = bus.toolkit(application) {
            toolkits.insert(toolkit);
        }
        let tree = bus.tree(application, limits);
        nodes += tree.len();
        controls.extend(controls_of(bus, &tree));
    }
    Ok(Observation { toolkit: toolkits.into_iter().collect::<Vec<_>>().join(","), nodes, shortcuts: accessibility::shortcuts(&controls) })
}

fn controls_of(bus: &Bus, nodes: &[Node]) -> Vec<Control> {
    let by_object: HashMap<&ObjectRef, &Node> = nodes.iter().map(|node| (&node.object, node)).collect();
    let mut parents: HashMap<&ObjectRef, &ObjectRef> = HashMap::new();
    for node in nodes {
        if let Some(parent) = &node.parent {
            parents.insert(&node.object, parent);
        }
        for child in node.children.iter().flatten() {
            parents.entry(child).or_insert(&node.object);
        }
    }
    let documents: HashMap<&ObjectRef, String> = nodes
        .iter()
        .filter(|node| node.role == role::DOCUMENT_WEB)
        .map(|node| {
            let uri = bus.document_attributes(&node.object).ok().and_then(|attributes| attributes.get("URI").cloned()).unwrap_or_default();
            (&node.object, host(&uri))
        })
        .collect();

    let candidates: Vec<(&Node, Option<String>)> = nodes
        .iter()
        .filter(|node| !node.name.trim().is_empty() || !node.description.trim().is_empty())
        .map(|node| (node, site(&node.object, &parents, &documents)))
        .collect();
    let bindings: HashMap<ObjectRef, Vec<String>> = bus
        .map_parallel(&candidates, |(node, site)| {
            if !node.has_interface(ACTION_INTERFACE) || node.name.trim().is_empty() {
                return None;
            }
            let mut bindings: Vec<String> =
                bus.actions(&node.object).ok()?.into_iter().map(|action| action.key_binding).filter(|binding| !binding.is_empty()).collect();
            // Web engines publish aria-keyshortcuts as an object attribute.
            if site.is_some() {
                if let Some(declared) = bus.attributes(&node.object).ok().and_then(|attributes| attributes.get("keyshortcuts").cloned()) {
                    bindings.push(declared);
                }
            }
            (!bindings.is_empty()).then(|| (node.object.clone(), bindings))
        })
        .into_iter()
        .collect();

    candidates
        .into_iter()
        .map(|(node, site)| {
            let parent_role = parents.get(&node.object).and_then(|parent| by_object.get(parent)).map(|parent| parent.role);
            Control {
                name: node.name.clone(),
                description: node.description.clone(),
                key_bindings: bindings.get(&node.object).cloned().unwrap_or_default(),
                site,
                in_menu: matches!(parent_role, Some(role::MENU | role::POPUP_MENU | role::MENU_ITEM)),
            }
        })
        .collect()
}

fn site(object: &ObjectRef, parents: &HashMap<&ObjectRef, &ObjectRef>, documents: &HashMap<&ObjectRef, String>) -> Option<String> {
    let mut current = object;
    for _ in 0..256 {
        if let Some(site) = documents.get(current) {
            return Some(site.clone());
        }
        current = parents.get(current)?;
    }
    None
}

/// The host of an http(s) URI, or an empty string for any other document.
pub fn host(uri: &str) -> String {
    let Some(rest) = uri.strip_prefix("https://").or_else(|| uri.strip_prefix("http://")) else { return String::new() };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or_default();
    let authority = authority.rsplit('@').next().unwrap_or_default();
    let host = if authority.starts_with('[') {
        authority.split(']').next().map(|value| format!("{value}]")).unwrap_or_default()
    } else {
        authority.split(':').next().unwrap_or_default().to_string()
    };
    host.to_ascii_lowercase()
}

/// Fold a capture into the observed index. Shortcuts seen now replace earlier
/// entries with the same title on the same site, so a remapped key updates;
/// entries not visible this time are kept.
pub fn merge(index: &mut Index, class: &str, observation: &Observation, now: u64) {
    let class = class.to_lowercase();
    index.schema_version = SCHEMA_VERSION;
    index.generated_at = now;
    let position = index.apps.iter().position(|app| app.id == class).unwrap_or_else(|| {
        index.apps.push(App {
            id: class.clone(),
            name: class.clone(),
            classes: vec![class.clone()],
            processes: Vec::new(),
            executable: None,
            toolkit: None,
            sources: Vec::new(),
            shortcuts: Vec::new(),
        });
        index.apps.len() - 1
    });
    let app = &mut index.apps[position];
    let replaced: BTreeSet<(String, Option<String>)> =
        observation.shortcuts.iter().map(|shortcut| (shortcut.title.to_lowercase(), shortcut.site.clone())).collect();
    let mut shortcuts = observation.shortcuts.clone();
    shortcuts.extend(app.shortcuts.drain(..).filter(|shortcut| !replaced.contains(&(shortcut.title.to_lowercase(), shortcut.site.clone()))));
    shortcuts.truncate(MAX_SHORTCUTS);
    app.shortcuts = shortcuts;
    if !observation.toolkit.is_empty() {
        app.toolkit = Some(observation.toolkit.clone());
    }
    app.sources = vec![SourceReport {
        harvester: "accessibility".into(),
        origin: observation.toolkit.clone(),
        shortcuts: app.shortcuts.len(),
        error: None,
    }];
}

/// Load, update and atomically rewrite the observed index while holding an
/// exclusive lock, so captures of different applications cannot lose writes.
pub fn record(path: &Path, class: &str, observation: &Observation, now: u64) -> Result<()> {
    let parent = path.parent().context("observed index path has no directory")?;
    fs::create_dir_all(parent)?;
    let lock = fs::File::create(parent.join(".observed.lock"))?;
    lock.lock()?;
    let mut index = fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Index>(&bytes).ok())
        .unwrap_or(Index { schema_version: SCHEMA_VERSION, generated_at: now, apps: Vec::new() });
    merge(&mut index, class, observation, now);
    store::write_json(path, &index)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shortcut(title: &str, keys: &str, site: Option<&str>) -> Shortcut {
        Shortcut {
            title: title.into(),
            keys: vec![keys.into()],
            aliases: Vec::new(),
            section: None,
            action: None,
            site: site.map(str::to_string),
        }
    }

    #[test]
    fn hosts_of_web_documents() {
        assert_eq!(host("https://www.YouTube.com/watch?v=1"), "www.youtube.com");
        assert_eq!(host("http://user@localhost:8080/app"), "localhost");
        assert_eq!(host("https://[::1]:3000/"), "[::1]");
        assert_eq!(host("file:///home/me/index.html"), "");
        assert_eq!(host(""), "");
    }

    #[test]
    fn merging_replaces_retitled_keys_and_keeps_unseen_entries() {
        let mut index = Index { schema_version: SCHEMA_VERSION, generated_at: 0, apps: Vec::new() };
        let first = Observation {
            toolkit: "Qt".into(),
            nodes: 3,
            shortcuts: vec![shortcut("Save", "Ctrl+S", None), shortcut("Play", "k", Some("www.youtube.com"))],
        };
        merge(&mut index, "Linguist", &first, 1);
        let second = Observation { toolkit: "Qt".into(), nodes: 2, shortcuts: vec![shortcut("Save", "Ctrl+Shift+S", None), shortcut("Play", "p", Some("example.com"))] };
        merge(&mut index, "linguist", &second, 2);

        assert_eq!(index.apps.len(), 1);
        let app = &index.apps[0];
        assert_eq!(app.classes, vec!["linguist"]);
        let rendered: Vec<(&str, &str, Option<&str>)> =
            app.shortcuts.iter().map(|s| (s.title.as_str(), s.keys[0].as_str(), s.site.as_deref())).collect();
        assert_eq!(rendered, vec![("Save", "Ctrl+Shift+S", None), ("Play", "p", Some("example.com")), ("Play", "k", Some("www.youtube.com"))]);
        assert_eq!(app.sources[0].shortcuts, 3);
    }
}
