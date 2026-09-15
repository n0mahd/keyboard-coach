//! Shortcuts declared in GTK builder files.
//!
//! GTK applications describe shortcuts in three places, all compiled into
//! their GResource bundles:
//! - shortcuts windows and dialogs (`GtkShortcutsShortcut`, `AdwShortcutsItem`)
//!   with a title and accelerator, grouped into titled sections;
//! - menu models, where an `<item>` carries `label` and `accel` attributes;
//! - shortcut controllers (`GtkShortcut`) with a trigger and an action.

use std::collections::HashMap;

use quick_xml::events::Event;
use quick_xml::Reader;

use crate::keys;
use crate::xml;
use crate::model::Shortcut;

const SHORTCUT_CLASSES: &[&str] = &["GtkShortcutsShortcut", "AdwShortcutsItem"];
const SECTION_CLASSES: &[&str] = &["GtkShortcutsGroup", "GtkShortcutsSection", "AdwShortcutsSection"];

enum Frame {
    Object { class: String, props: HashMap<String, String> },
    Item { attrs: HashMap<String, String> },
    Other,
}

/// Strip GTK mnemonic underscores (`_New Window`) while keeping literal `__`.
pub fn strip_mnemonic(label: &str) -> String {
    let mut out = String::with_capacity(label.len());
    let mut chars = label.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch == '_' {
            if chars.peek() == Some(&'_') {
                out.push('_');
                chars.next();
            }
            continue;
        }
        out.push(ch);
    }
    out.trim().to_string()
}

/// `win.close-current-view` → `Close current view`.
fn title_from_action(action: &str) -> String {
    let name = action.rsplit('.').next().unwrap_or(action).replace(['-', '_'], " ");
    let mut chars = name.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}

pub fn extract(xml: &[u8]) -> Vec<Shortcut> {
    let mut reader = Reader::from_reader(xml);
    reader.config_mut().trim_text(true);
    let mut stack: Vec<Frame> = Vec::new();
    // The name of the <property>/<attribute> whose text is being read.
    let mut reading: Option<String> = None;
    let mut text = String::new();
    let mut shortcuts = Vec::new();
    let mut buffer = Vec::new();

    loop {
        let event = match reader.read_event_into(&mut buffer) {
            Ok(event) => event,
            Err(_) => break,
        };
        match event {
            Event::Start(element) => match element.name().as_ref() {
                "object" => stack.push(Frame::Object {
                    class: xml::attr(&element, "class").unwrap_or_default(),
                    props: HashMap::new(),
                }),
                "item" => stack.push(Frame::Item { attrs: HashMap::new() }),
                "property" | "attribute" => {
                    reading = xml::attr(&element, "name");
                    text.clear();
                }
                _ => stack.push(Frame::Other),
            },
            Event::Text(content) if reading.is_some() => text.push_str(content.as_ref()),
            Event::GeneralRef(reference) if reading.is_some() => text.push_str(&xml::reference(&reference)),
            Event::End(element) => match element.name().as_ref() {
                "property" | "attribute" => {
                    if let Some(key) = reading.take() {
                        match stack.last_mut() {
                            Some(Frame::Object { props, .. }) => {
                                props.insert(key, text.trim().to_string());
                            }
                            Some(Frame::Item { attrs }) => {
                                attrs.insert(key, text.trim().to_string());
                            }
                            _ => {}
                        }
                    }
                }
                "object" | "item" => {
                    let Some(frame) = stack.pop() else { continue };
                    let section = nearest_section(&stack);
                    if let Some(shortcut) = shortcut_from(frame, section) {
                        shortcuts.push(shortcut);
                    }
                }
                _ => {
                    stack.pop();
                }
            },
            Event::Eof => break,
            _ => {}
        }
        buffer.clear();
    }
    shortcuts
}

fn nearest_section(stack: &[Frame]) -> Option<String> {
    stack.iter().rev().find_map(|frame| match frame {
        Frame::Object { class, props } if SECTION_CLASSES.contains(&class.as_str()) => {
            props.get("title").filter(|title| !title.is_empty()).cloned()
        }
        _ => None,
    })
}

fn shortcut_from(frame: Frame, section: Option<String>) -> Option<Shortcut> {
    match frame {
        Frame::Object { class, props } if SHORTCUT_CLASSES.contains(&class.as_str()) => {
            // direction 2 is the right-to-left variant of a shortcut listed twice.
            if props.get("direction").map(String::as_str) == Some("2") {
                return None;
            }
            let keys = keys::gtk_accelerators(props.get("accelerator")?);
            let title = props.get("title").map(|title| strip_mnemonic(title))?;
            (!keys.is_empty() && !title.is_empty()).then(|| Shortcut {
                title,
                keys,
                aliases: props.get("subtitle").map(|subtitle| vec![strip_mnemonic(subtitle)]).unwrap_or_default(),
                section,
                action: props.get("action-name").cloned(),
                site: None,
            })
        }
        Frame::Object { class, props } if class == "GtkShortcut" => {
            let keys = keys::gtk_accelerators(props.get("trigger")?);
            let action = props.get("action")?.trim();
            let action = action.strip_prefix("action(").and_then(|rest| rest.strip_suffix(')')).unwrap_or(action);
            (!keys.is_empty() && !action.is_empty()).then(|| Shortcut {
                title: title_from_action(action),
                keys,
                aliases: Vec::new(),
                section: None,
                action: Some(action.to_string()),
                site: None,
            })
        }
        Frame::Item { attrs } => {
            let keys = keys::gtk_accelerators(attrs.get("accel")?);
            let title = strip_mnemonic(attrs.get("label")?);
            (!keys.is_empty() && !title.is_empty()).then(|| Shortcut {
                title,
                keys,
                aliases: Vec::new(),
                section: None,
                action: attrs.get("action").cloned(),
                site: None,
            })
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn libadwaita_shortcuts_dialog() {
        let xml = br#"<interface><object class="AdwShortcutsDialog"><child>
            <object class="AdwShortcutsSection"><property name="title" translatable="yes">Navigation</property>
              <child><object class="AdwShortcutsItem">
                <property name="title" translatable="yes">Go Back</property>
                <property name="accelerator">&lt;alt&gt;Left</property><property name="direction">1</property>
              </object></child>
              <child><object class="AdwShortcutsItem">
                <property name="title">Go Back</property>
                <property name="accelerator">&lt;alt&gt;Right</property><property name="direction">2</property>
              </object></child>
              <child><object class="AdwShortcutsItem">
                <property name="title">Show Sidebar</property><property name="action-name">win.toggle-sidebar</property>
              </object></child>
            </object></child></object></interface>"#;

        let shortcuts = extract(xml);

        assert_eq!(shortcuts.len(), 1);
        assert_eq!(shortcuts[0].title, "Go Back");
        assert_eq!(shortcuts[0].keys, vec!["Alt+Left"]);
        assert_eq!(shortcuts[0].section.as_deref(), Some("Navigation"));
    }

    #[test]
    fn gtk3_shortcuts_window_with_alternatives() {
        let xml = br#"<interface><object class="GtkShortcutsWindow"><child><object class="GtkShortcutsSection">
            <child><object class="GtkShortcutsGroup"><property name="title">Document</property>
              <child><object class="GtkShortcutsShortcut">
                <property name="title">Open a document</property>
                <property name="accelerator">&lt;Primary&gt;O Return</property>
              </object></child></object></child></object></child></object></interface>"#;

        let shortcuts = extract(xml);

        assert_eq!(shortcuts[0].keys, vec!["Ctrl+O", "Enter"]);
        assert_eq!(shortcuts[0].section.as_deref(), Some("Document"));
    }

    #[test]
    fn menu_items_and_shortcut_controllers() {
        let xml = br#"<interface>
            <menu id="app-menu"><section><item>
              <attribute name="label" translatable="yes">_New Window</attribute>
              <attribute name="action">app.new-window</attribute>
              <attribute name="accel">&lt;Primary&gt;n</attribute>
            </item><item><attribute name="label">_About</attribute><attribute name="action">app.about</attribute></item></section></menu>
            <object class="GtkShortcutController"><child><object class="GtkShortcut">
              <property name="trigger">&lt;Control&gt;w</property><property name="action">action(win.close-current-view)</property>
            </object></child></object>
          </interface>"#;

        let shortcuts = extract(xml);

        assert_eq!(shortcuts.len(), 2);
        assert_eq!((shortcuts[0].title.as_str(), shortcuts[0].keys[0].as_str()), ("New Window", "Ctrl+N"));
        assert_eq!((shortcuts[1].title.as_str(), shortcuts[1].keys[0].as_str()), ("Close current view", "Ctrl+W"));
    }

    #[test]
    fn mnemonics_are_stripped() {
        assert_eq!(strip_mnemonic("_Save As…"), "Save As…");
        assert_eq!(strip_mnemonic("snake__case"), "snake_case");
    }
}
