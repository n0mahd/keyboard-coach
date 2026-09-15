use serde::{Deserialize, Serialize};

/// One action and the key combinations that trigger it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Shortcut {
    /// Human-readable action name as the application presents it.
    pub title: String,
    /// Alternative key combinations, each rendered as `Ctrl+Shift+N`. A
    /// sequence of chords is joined with `, ` (`Ctrl+Space, C`).
    pub keys: Vec<String>,
    /// Other names the application uses for the same action, such as a
    /// tooltip or context-menu label. Used for matching clicked controls.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub aliases: Vec<String>,
    /// Grouping the application shows the shortcut under.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub section: Option<String>,
    /// Internal action identifier, when the source provides one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub action: Option<String>,
    /// Host of the web page the shortcut was observed on (empty for a page
    /// without one). Absent for shortcuts of the application's own interface.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub site: Option<String>,
}

/// Where a set of shortcuts came from and whether the harvest succeeded.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceReport {
    pub harvester: String,
    pub origin: String,
    pub shortcuts: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// An application and every shortcut discovered for it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct App {
    /// Desktop entry id, or a synthetic id for tools without one.
    pub id: String,
    pub name: String,
    /// Lower-cased window classes the application is known to use.
    pub classes: Vec<String>,
    /// Process names, for tools that run inside a terminal.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub processes: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub executable: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub toolkit: Option<String>,
    pub sources: Vec<SourceReport>,
    pub shortcuts: Vec<Shortcut>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Index {
    pub schema_version: u32,
    pub generated_at: u64,
    pub apps: Vec<App>,
}

pub const SCHEMA_VERSION: u32 = 1;
