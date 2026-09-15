//! LibreOffice accelerators from its configuration registry.
//!
//! `share/registry/main.xcd` holds the default key table
//! (`org.openoffice.Office.Accelerators`) as key-name → UNO command nodes, global
//! and per document module, and the UI labels for each UNO command in the
//! `*Commands` components. The user's `registrymodifications.xcu` layers
//! customized keys on top.

use std::collections::{BTreeMap, HashMap};

use quick_xml::events::Event;
use quick_xml::Reader;

use crate::keys;
use crate::xml;
use crate::model::Shortcut;

/// A document module and the desktop entry / window class that shows it.
pub struct Module {
    pub service: &'static str,
    pub app_id: &'static str,
    /// Label components that apply to this module, beyond GenericCommands.
    pub label_components: &'static [&'static str],
}

pub const MODULES: &[Module] = &[
    Module { service: "com.sun.star.text.TextDocument", app_id: "libreoffice-writer", label_components: &["WriterCommands"] },
    Module { service: "com.sun.star.sheet.SpreadsheetDocument", app_id: "libreoffice-calc", label_components: &["CalcCommands"] },
    Module { service: "com.sun.star.presentation.PresentationDocument", app_id: "libreoffice-impress", label_components: &["DrawImpressCommands"] },
    Module { service: "com.sun.star.drawing.DrawingDocument", app_id: "libreoffice-draw", label_components: &["DrawImpressCommands"] },
    Module { service: "com.sun.star.formula.FormulaProperties", app_id: "libreoffice-math", label_components: &["MathCommands"] },
    Module { service: "com.sun.star.sdb.OfficeDatabaseDocument", app_id: "libreoffice-base", label_components: &["DbuCommands"] },
    Module { service: "com.sun.star.frame.StartModule", app_id: "libreoffice-startcenter", label_components: &["StartModuleCommands"] },
];

#[derive(Default, Debug)]
pub struct Registry {
    /// (module or "" for global) → key node name → UNO command.
    pub keys: BTreeMap<String, BTreeMap<String, String>>,
    /// label component → UNO command → labels, best first.
    pub labels: HashMap<String, HashMap<String, Vec<String>>>,
}

struct Prop {
    name: String,
    /// Language of the `<value>` being read.
    lang: Option<String>,
    text: String,
    /// The English or language-neutral value, when the prop has one.
    value: Option<String>,
}

/// Parse `main.xcd` (component-data blocks) into key tables and labels.
pub fn parse_xcd(xml: &[u8], registry: &mut Registry) {
    let mut reader = Reader::from_reader(xml);
    reader.config_mut().trim_text(true);
    let mut buffer = Vec::new();
    let mut component: Option<String> = None;
    let mut nodes: Vec<String> = Vec::new();
    let mut prop: Option<Prop> = None;
    let mut in_value = false;
    // Labels gathered for the node being read: (prop name, lang, text).
    let mut pending: Vec<(String, Option<String>, String)> = Vec::new();

    loop {
        let event = match reader.read_event_into(&mut buffer) {
            Ok(event) => event,
            Err(_) => break,
        };
        let empty = matches!(event, Event::Empty(_));
        match event {
            Event::Start(element) | Event::Empty(element) => {
                match element.name().as_ref() {
                    "oor:component-data" => {
                        component = xml::attr(&element, "oor:name");
                        nodes.clear();
                    }
                    "node" if component.is_some() && !empty => nodes.push(xml::attr(&element, "oor:name").unwrap_or_default()),
                    "prop" if component.is_some() => {
                        let name = xml::attr(&element, "oor:name").unwrap_or_default();
                        // A prop without an English value carries only translations
                        // (`<prop oor:name="Command"/>` or other-language values), so
                        // English falls back to the global key table.
                        if !empty {
                            prop = Some(Prop { name, lang: None, text: String::new(), value: None });
                        }
                    }
                    "value" if prop.is_some() => {
                        in_value = !empty;
                        if let Some(prop) = prop.as_mut() {
                            prop.lang = xml::attr(&element, "xml:lang");
                            prop.text.clear();
                        }
                    }
                    _ => {}
                }
            }
            Event::Text(content) if in_value => {
                if let Some(prop) = prop.as_mut() {
                    prop.text.push_str(content.as_ref());
                }
            }
            Event::GeneralRef(reference) if in_value => {
                if let Some(prop) = prop.as_mut() {
                    prop.text.push_str(&xml::reference(&reference));
                }
            }
            Event::End(element) => match element.name().as_ref() {
                "value" => {
                    in_value = false;
                    if let Some(prop) = prop.as_mut() {
                        if prop.lang.as_deref().is_none_or(|lang| lang == "en-US") {
                            prop.value = Some(std::mem::take(&mut prop.text));
                        }
                    }
                }
                "prop" => {
                    if let Some(Prop { name, value: Some(value), .. }) = prop.take() {
                        record(registry, component.as_deref(), &nodes, &name, value, &mut pending);
                    }
                }
                "node" if component.is_some() => {
                    flush_labels(registry, component.as_deref(), &nodes, &mut pending);
                    nodes.pop();
                }
                "oor:component-data" => component = None,
                _ => {}
            },
            Event::Eof => break,
            _ => {}
        }
        buffer.clear();
    }
}

fn record(
    registry: &mut Registry,
    component: Option<&str>,
    nodes: &[String],
    prop: &str,
    value: String,
    pending: &mut Vec<(String, Option<String>, String)>,
) {
    match component {
        // Only the preferred key table; SecondaryKeys duplicates it.
        Some("Accelerators") if prop == "Command" => {
            if value.trim().is_empty() {
                return;
            }
            let (module, key) = match nodes {
                [primary, global, key] if primary == "PrimaryKeys" && global == "Global" => (String::new(), key.clone()),
                [primary, modules, module, key] if primary == "PrimaryKeys" && modules == "Modules" => (module.clone(), key.clone()),
                _ => return,
            };
            registry.keys.entry(module).or_default().insert(key, value);
        }
        Some(name) if name.ends_with("Commands") && ["Label", "ContextLabel", "PopupLabel", "TooltipLabel"].contains(&prop) => {
            pending.push((prop.to_string(), None, value));
        }
        _ => {}
    }
}

fn flush_labels(registry: &mut Registry, component: Option<&str>, nodes: &[String], pending: &mut Vec<(String, Option<String>, String)>) {
    let (Some(component), Some(command)) = (component, nodes.last()) else {
        pending.clear();
        return;
    };
    if pending.is_empty() || !command.starts_with(".uno:") {
        pending.clear();
        return;
    }
    let rank = |name: &str| ["Label", "ContextLabel", "PopupLabel", "TooltipLabel"].iter().position(|item| *item == name);
    pending.sort_by_key(|(name, _, _)| rank(name));
    let mut labels: Vec<String> = Vec::new();
    for (_, _, value) in pending.drain(..) {
        let clean = value.replace('~', "").trim().to_string();
        if !clean.is_empty() && !labels.contains(&clean) {
            labels.push(clean);
        }
    }
    registry.labels.entry(component.to_string()).or_default().insert(command.clone(), labels);
}

/// Shortcuts for one module: the global table overlaid with module keys.
pub fn module_shortcuts(registry: &Registry, module: &Module) -> Vec<Shortcut> {
    let mut effective: BTreeMap<String, String> = registry.keys.get("").cloned().unwrap_or_default();
    if let Some(keys) = registry.keys.get(module.service) {
        for (key, command) in keys {
            effective.insert(key.clone(), command.clone());
        }
    }
    let mut by_command: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (key, command) in effective {
        if command.is_empty() {
            continue;
        }
        if let Some(rendered) = keys::libreoffice(&key) {
            by_command.entry(command).or_default().push(rendered);
        }
    }
    let mut shortcuts = Vec::new();
    for (command, mut rendered) in by_command {
        let labels = module
            .label_components
            .iter()
            .chain(std::iter::once(&"GenericCommands"))
            .find_map(|component| registry.labels.get(*component).and_then(|labels| labels.get(&command)))
            .cloned()
            .unwrap_or_default();
        let Some((title, aliases)) = labels.split_first() else { continue };
        rendered.sort_by_key(|key| (key.matches('+').count(), key.len()));
        shortcuts.push(Shortcut {
            title: title.clone(),
            keys: rendered,
            aliases: aliases.to_vec(),
            section: None,
            action: Some(command),
            site: None,
        });
    }
    shortcuts
}

#[cfg(test)]
mod tests {
    use super::*;

    const XCD: &[u8] = br#"<oor:data>
      <oor:component-data oor:name="Accelerators" oor:package="org.openoffice.Office">
        <node oor:name="PrimaryKeys"><node oor:name="Global">
          <node oor:name="S_MOD1" oor:op="replace"><prop oor:name="Command"><value xml:lang="en-US">.uno:Save</value></prop></node>
          <node oor:name="B_MOD1" oor:op="replace"><prop oor:name="Command"><value xml:lang="en-US">.uno:Bold</value></prop></node>
        </node><node oor:name="Modules"><node oor:name="com.sun.star.sheet.SpreadsheetDocument">
          <node oor:name="B_MOD1" oor:op="replace"><prop oor:name="Command"><value xml:lang="de">.uno:Shadowed</value></prop></node>
          <node oor:name="S_MOD1" oor:op="replace"><prop oor:name="Command"/></node>
          <node oor:name="F2" oor:op="replace"><prop oor:name="Command"><value xml:lang="en-US">.uno:SetInputMode</value></prop></node>
        </node></node></node>
      </oor:component-data>
      <oor:component-data oor:name="GenericCommands" oor:package="org.openoffice.Office.UI">
        <node oor:name="UserInterface"><node oor:name="Commands">
          <node oor:name=".uno:Save" oor:op="replace"><prop oor:name="Label"><value xml:lang="en-US">~Save</value></prop>
            <prop oor:name="TooltipLabel"><value xml:lang="en-US">Save Document</value></prop></node>
          <node oor:name=".uno:Bold" oor:op="replace"><prop oor:name="Label"><value xml:lang="en-US">Bold</value></prop></node>
        </node></node>
      </oor:component-data>
      <oor:component-data oor:name="CalcCommands" oor:package="org.openoffice.Office.UI">
        <node oor:name="UserInterface"><node oor:name="Commands">
          <node oor:name=".uno:SetInputMode" oor:op="replace"><prop oor:name="Label"><value xml:lang="en-US">Edit Mode</value></prop></node>
        </node></node>
      </oor:component-data>
    </oor:data>"#;

    #[test]
    fn module_keys_overlay_global_keys_and_resolve_labels() {
        let mut registry = Registry::default();
        parse_xcd(XCD, &mut registry);
        let calc = MODULES.iter().find(|module| module.app_id == "libreoffice-calc").unwrap();
        let writer = MODULES.iter().find(|module| module.app_id == "libreoffice-writer").unwrap();

        let calc_shortcuts = module_shortcuts(&registry, calc);
        let writer_shortcuts = module_shortcuts(&registry, writer);

        let titles = |shortcuts: &[Shortcut]| shortcuts.iter().map(|s| (s.title.clone(), s.keys.join(" / "))).collect::<Vec<_>>();
        // A module entry with only translated values keeps the global English binding.
        assert_eq!(titles(&calc_shortcuts), vec![("Bold".into(), "Ctrl+B".into()), ("Save".into(), "Ctrl+S".into()), ("Edit Mode".into(), "F2".into())]);
        assert_eq!(titles(&writer_shortcuts), vec![("Bold".into(), "Ctrl+B".into()), ("Save".into(), "Ctrl+S".into())]);
        assert_eq!(calc_shortcuts[1].aliases, vec!["Save Document"]);
    }
}
