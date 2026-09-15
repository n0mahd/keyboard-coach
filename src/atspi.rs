//! A small blocking AT-SPI client over D-Bus.
//!
//! Only the calls the coach needs are implemented: finding an application by
//! process id, reading its tree (through the bulk cache when the toolkit offers
//! one), and reading the text and key bindings of individual objects.

use std::collections::{HashMap, HashSet};
use std::time::Duration;

use anyhow::{Context, Result, bail};
use serde::de::DeserializeOwned;
use zbus::blocking::Connection;
use zbus::zvariant::{OwnedObjectPath, OwnedValue, Type};

const REGISTRY_BUS: &str = "org.a11y.atspi.Registry";
const ROOT_PATH: &str = "/org/a11y/atspi/accessible/root";
const ACCESSIBLE: &str = "org.a11y.atspi.Accessible";
const ACTION: &str = "org.a11y.atspi.Action";
const CACHE_PATH: &str = "/org/a11y/atspi/cache";
const CACHE: &str = "org.a11y.atspi.Cache";

/// A calling toolkit that stops answering must not stall the caller.
const METHOD_TIMEOUT: Duration = Duration::from_millis(1500);

/// AtspiRole values used by the coach.
pub mod role {
    pub const CHECK_MENU_ITEM: u32 = 8;
    pub const LIST: u32 = 31;
    pub const LIST_ITEM: u32 = 32;
    pub const MENU: u32 = 33;
    pub const MENU_BAR: u32 = 34;
    pub const MENU_ITEM: u32 = 35;
    pub const PAGE_TAB: u32 = 37;
    pub const POPUP_MENU: u32 = 41;
    pub const PUSH_BUTTON: u32 = 43;
    pub const RADIO_BUTTON: u32 = 44;
    pub const RADIO_MENU_ITEM: u32 = 45;
    pub const TABLE: u32 = 55;
    pub const TABLE_CELL: u32 = 56;
    pub const TERMINAL: u32 = 60;
    pub const TOGGLE_BUTTON: u32 = 62;
    pub const TOOL_BAR: u32 = 63;
    pub const TREE: u32 = 65;
    pub const TREE_TABLE: u32 = 66;
    pub const DOCUMENT_WEB: u32 = 95;
    pub const LINK: u32 = 88;
    pub const TABLE_ROW: u32 = 90;
    pub const TREE_ITEM: u32 = 91;
    pub const LIST_BOX: u32 = 98;
    pub const PUSH_BUTTON_MENU: u32 = 129;
}

/// AtspiStateType bit positions used by the coach.
pub mod state {
    pub const SHOWING: u32 = 25;
    pub const MANAGES_DESCENDANTS: u32 = 31;
}

/// A reference to one accessible object: the owning connection and its path.
#[derive(Debug, Clone, PartialEq, Eq, Hash, serde::Deserialize, serde::Serialize, Type)]
pub struct ObjectRef {
    pub bus: String,
    pub path: OwnedObjectPath,
}

impl ObjectRef {
    pub fn is_null(&self) -> bool {
        self.path.as_str() == "/org/a11y/atspi/null"
    }
}

/// An action an object can perform, with the key binding the toolkit reports.
#[derive(Debug, Clone, PartialEq, Eq, serde::Deserialize, Type)]
pub struct Action {
    pub name: String,
    pub description: String,
    pub key_binding: String,
}

/// The fields of an object that every tree walk reads.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Node {
    pub object: ObjectRef,
    pub parent: Option<ObjectRef>,
    pub role: u32,
    pub name: String,
    pub description: String,
    pub interfaces: Vec<String>,
    pub states: [u32; 2],
    /// Child references when the source provides them (the Qt cache and a
    /// recursive walk do); `None` when only the parent links are known.
    pub children: Option<Vec<ObjectRef>>,
    /// The number of children the object reports, when known without a call.
    pub child_count: Option<usize>,
}

impl Node {
    pub fn has_state(&self, bit: u32) -> bool {
        let word = (bit / 32) as usize;
        word < 2 && self.states[word] & (1 << (bit % 32)) != 0
    }

    pub fn has_interface(&self, name: &str) -> bool {
        self.interfaces.iter().any(|interface| interface == name)
    }
}

pub struct Bus {
    connection: Connection,
}

/// at-spi2-core's cache item: object, application, parent, index in parent,
/// child count, interfaces, name, role, description, states.
type CoreCacheItem = (ObjectRef, ObjectRef, ObjectRef, i32, i32, Vec<String>, String, u32, String, Vec<u32>);
/// Qt's cache item lists the children instead of the index and count.
type QtCacheItem = (ObjectRef, ObjectRef, ObjectRef, Vec<ObjectRef>, Vec<String>, String, u32, String, Vec<u32>);

fn state_words(states: &[u32]) -> [u32; 2] {
    [states.first().copied().unwrap_or(0), states.get(1).copied().unwrap_or(0)]
}

impl Bus {
    /// Connect to the accessibility bus announced on the session bus.
    pub fn connect() -> Result<Self> {
        let session = Connection::session().context("no session bus")?;
        let reply = session
            .call_method(Some("org.a11y.Bus"), "/org/a11y/bus", Some("org.a11y.Bus"), "GetAddress", &())
            .context("the accessibility bus is not running")?;
        let address: String = reply.body().deserialize()?;
        let connection = zbus::blocking::connection::Builder::address(address.as_str())?
            .method_timeout(METHOD_TIMEOUT)
            .build()
            .context("cannot connect to the accessibility bus")?;
        Ok(Self { connection })
    }

    fn call<B, R>(&self, object: &ObjectRef, interface: &str, method: &str, body: &B) -> Result<R>
    where
        B: serde::Serialize + Type,
        R: DeserializeOwned + Type,
    {
        let reply = self.connection.call_method(Some(object.bus.as_str()), object.path.as_str(), Some(interface), method, body)?;
        Ok(reply.body().deserialize()?)
    }

    fn property<R>(&self, object: &ObjectRef, interface: &str, name: &str) -> Result<R>
    where
        R: TryFrom<OwnedValue>,
        R::Error: Into<zbus::zvariant::Error>,
    {
        let value: OwnedValue = self.call(object, "org.freedesktop.DBus.Properties", "Get", &(interface, name))?;
        R::try_from(value).map_err(|error| anyhow::Error::from(error.into()))
    }

    /// Top-level application objects registered on the bus.
    pub fn applications(&self) -> Result<Vec<ObjectRef>> {
        let root = ObjectRef { bus: REGISTRY_BUS.into(), path: OwnedObjectPath::try_from(ROOT_PATH)? };
        self.children(&root)
    }

    /// The process that owns a bus connection.
    pub fn pid(&self, bus: &str) -> Result<u32> {
        let reply = self.connection.call_method(
            Some("org.freedesktop.DBus"),
            "/org/freedesktop/DBus",
            Some("org.freedesktop.DBus"),
            "GetConnectionUnixProcessID",
            &(bus,),
        )?;
        Ok(reply.body().deserialize()?)
    }

    /// The application object whose connection belongs to `pid`.
    pub fn application_for_pid(&self, pid: u32) -> Result<ObjectRef> {
        for application in self.applications()? {
            if self.pid(&application.bus).ok() == Some(pid) {
                return Ok(application);
            }
        }
        bail!("process {pid} has no accessible application")
    }

    pub fn children(&self, object: &ObjectRef) -> Result<Vec<ObjectRef>> {
        self.call(object, ACCESSIBLE, "GetChildren", &())
    }

    pub fn role(&self, object: &ObjectRef) -> Result<u32> {
        self.call(object, ACCESSIBLE, "GetRole", &())
    }

    pub fn states(&self, object: &ObjectRef) -> Result<[u32; 2]> {
        let states: Vec<u32> = self.call(object, ACCESSIBLE, "GetState", &())?;
        Ok(state_words(&states))
    }

    pub fn interfaces(&self, object: &ObjectRef) -> Result<Vec<String>> {
        self.call(object, ACCESSIBLE, "GetInterfaces", &())
    }

    pub fn name(&self, object: &ObjectRef) -> Result<String> {
        self.property(object, ACCESSIBLE, "Name")
    }

    pub fn description(&self, object: &ObjectRef) -> Result<String> {
        self.property(object, ACCESSIBLE, "Description")
    }

    pub fn attributes(&self, object: &ObjectRef) -> Result<HashMap<String, String>> {
        self.call(object, ACCESSIBLE, "GetAttributes", &())
    }

    /// The object's actions. Qt leaves the key binding out of the bulk reply
    /// and only answers the per-action query, so empty bindings are asked for
    /// individually.
    pub fn actions(&self, object: &ObjectRef) -> Result<Vec<Action>> {
        let mut actions: Vec<Action> = self.call(object, ACTION, "GetActions", &())?;
        for (index, action) in actions.iter_mut().enumerate() {
            if action.key_binding.is_empty() {
                action.key_binding = self.call(object, ACTION, "GetKeyBinding", &(index as i32,)).unwrap_or_default();
            }
        }
        Ok(actions)
    }

    /// Attributes of a document, including its `URI` in web engines.
    pub fn document_attributes(&self, object: &ObjectRef) -> Result<HashMap<String, String>> {
        self.call(object, "org.a11y.atspi.Document", "GetAttributes", &())
    }

    pub fn toolkit(&self, application: &ObjectRef) -> Result<String> {
        self.property(application, "org.a11y.atspi.Application", "ToolkitName")
    }

    /// Every object the application has published, in one call, when its
    /// toolkit implements the cache interface. `None` means the caller must
    /// walk the tree instead.
    pub fn cached_nodes(&self, application: &ObjectRef) -> Option<Vec<Node>> {
        let cache = ObjectRef { bus: application.bus.clone(), path: OwnedObjectPath::try_from(CACHE_PATH).ok()? };
        let reply = self.connection.call_method(Some(cache.bus.as_str()), cache.path.as_str(), Some(CACHE), "GetItems", &()).ok()?;
        let body = reply.body();
        // Qt answers with an empty list: it implements the method but keeps no
        // cache, so an empty reply means the tree must be walked.
        if let Ok(items) = body.deserialize::<Vec<CoreCacheItem>>() {
            if items.is_empty() {
                return None;
            }
            return Some(
                items
                    .into_iter()
                    .map(|(object, _, parent, _, child_count, interfaces, name, role, description, states)| Node {
                        object,
                        parent: (!parent.is_null()).then_some(parent),
                        role,
                        name,
                        description,
                        interfaces,
                        states: state_words(&states),
                        children: None,
                        child_count: usize::try_from(child_count).ok(),
                    })
                    .collect(),
            );
        }
        if let Ok(items) = body.deserialize::<Vec<QtCacheItem>>() {
            if items.is_empty() {
                return None;
            }
            return Some(
                items
                    .into_iter()
                    .map(|(object, _, parent, children, interfaces, name, role, description, states)| Node {
                        object,
                        parent: (!parent.is_null()).then_some(parent),
                        role,
                        name,
                        description,
                        interfaces,
                        states: state_words(&states),
                        child_count: Some(children.len()),
                        children: Some(children),
                    })
                    .collect(),
            );
        }
        None
    }

    /// Read one object's walk fields.
    pub fn node(&self, object: &ObjectRef, parent: Option<&ObjectRef>) -> Result<Node> {
        Ok(Node {
            object: object.clone(),
            parent: parent.cloned(),
            role: self.role(object)?,
            name: self.name(object).unwrap_or_default(),
            description: self.description(object).unwrap_or_default(),
            interfaces: self.interfaces(object).unwrap_or_default(),
            states: self.states(object).unwrap_or_default(),
            children: None,
            child_count: None,
        })
    }
}

/// Bounds on a tree walk, so a huge document cannot make a capture expensive.
#[derive(Debug, Clone, Copy)]
pub struct Limits {
    pub max_nodes: usize,
    pub max_children: usize,
    pub deadline: Duration,
}

impl Default for Limits {
    fn default() -> Self {
        Self { max_nodes: 6000, max_children: 400, deadline: Duration::from_secs(4) }
    }
}

impl Bus {
    /// Every object below `root`, from the cache when the toolkit keeps one and
    /// by a breadth-first walk otherwise. Children of objects that manage their
    /// descendants (large lists and tables) are not visited: their rows are
    /// created on demand and carry data, not commands.
    pub fn tree(&self, root: &ObjectRef, limits: Limits) -> Vec<Node> {
        let Some(mut nodes) = self.cached_nodes(root) else {
            return self.walk_from(vec![(root.clone(), None)], HashSet::new(), limits);
        };
        // Chromium's cache holds the browser interface but not web documents,
        // which appear only as uncached children; walk those from the cache.
        let mut cached_children: HashMap<&ObjectRef, usize> = HashMap::new();
        for node in &nodes {
            if let Some(parent) = &node.parent {
                *cached_children.entry(parent).or_default() += 1;
            }
        }
        let incomplete: Vec<ObjectRef> = nodes
            .iter()
            .filter(|node| !node.has_state(state::MANAGES_DESCENDANTS))
            .filter(|node| node.child_count.unwrap_or(0) > cached_children.get(&node.object).copied().unwrap_or(0))
            .map(|node| node.object.clone())
            .collect();
        let known: HashSet<ObjectRef> = nodes.iter().map(|node| node.object.clone()).collect();
        let start: Vec<(ObjectRef, Option<ObjectRef>)> = self
            .map_parallel(&incomplete, |parent| {
                let children = self.children(parent).ok()?;
                Some(children.into_iter().filter(|child| !child.is_null() && !known.contains(child)).map(|child| (child, Some(parent.clone()))).collect::<Vec<_>>())
            })
            .into_iter()
            .flatten()
            .collect();
        let remaining = Limits { max_nodes: limits.max_nodes.saturating_sub(nodes.len()), ..limits };
        nodes.extend(self.walk_from(start, known, remaining));
        nodes
    }

    /// Breadth-first walk from `start`, skipping objects already in `seen`.
    pub fn walk_from(&self, start: Vec<(ObjectRef, Option<ObjectRef>)>, mut seen: HashSet<ObjectRef>, limits: Limits) -> Vec<Node> {
        let started = std::time::Instant::now();
        let mut nodes = Vec::new();
        let mut level = start;
        while !level.is_empty() && nodes.len() < limits.max_nodes && started.elapsed() < limits.deadline {
            level.retain(|(object, _)| seen.insert(object.clone()));
            level.truncate(limits.max_nodes - nodes.len());
            let read: Vec<Node> = self.map_parallel(&level, |(object, parent)| {
                let mut node = self.node(object, parent.as_ref()).ok()?;
                if !node.has_state(state::MANAGES_DESCENDANTS) && node.role != role::TERMINAL {
                    let mut children = self.children(object).unwrap_or_default();
                    children.retain(|child| !child.is_null());
                    children.truncate(limits.max_children);
                    node.child_count = Some(children.len());
                    node.children = Some(children);
                }
                Some(node)
            });
            level = read.iter().flat_map(|node| node.children.iter().flatten().map(|child| (child.clone(), Some(node.object.clone())))).collect();
            nodes.extend(read);
        }
        nodes
    }

    /// Run one query per item on several threads. Replies are multiplexed on
    /// the single connection, so this removes round-trip waiting, not load.
    pub fn map_parallel<T, R, F>(&self, items: &[T], query: F) -> Vec<R>
    where
        T: Sync,
        R: Send,
        F: Fn(&T) -> Option<R> + Sync,
    {
        const WORKERS: usize = 6;
        if items.len() < 2 * WORKERS {
            return items.iter().filter_map(&query).collect();
        }
        let chunk = items.len().div_ceil(WORKERS);
        std::thread::scope(|scope| {
            let handles: Vec<_> = items.chunks(chunk).map(|part| scope.spawn(|| part.iter().filter_map(&query).collect::<Vec<R>>())).collect();
            handles.into_iter().flat_map(|handle| handle.join().unwrap_or_default()).collect()
        })
    }
}
