#!/usr/bin/env python3
"""Suggest a keyboard equivalent for a completed mouse action."""

from __future__ import annotations

import collections
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from typing import Any
from urllib.parse import urlsplit
import warnings

DEFAULT_BANNER_DURATION_MS = 2000
BUTTONS = {"left", "right", "middle"}

# Omarchy's app launcher. Resolved against the indexed bindings, never assumed.
APP_LAUNCHER_COMMAND = re.compile(r"^omarchy-menu toggle apps$")

ACTIONABLE_ROLES = {
    "button", "check box", "check menu item", "combo box", "entry", "link", "list item",
    "menu item", "page tab", "push button", "radio button", "radio menu item", "slider",
    "spin button", "switch", "table row", "toggle button", "tree item",
}
DOCUMENT_ROLES = {"document web", "document frame"}
FRAME_ROLES = {"frame", "window", "dialog", "application"}
# Chromium opens menus as separate accessible windows that Hyprland does not list.
POPUP_ROLES = {"menu", "popup menu", "window", "tool tip"}

warnings.filterwarnings("ignore", category=DeprecationWarning)


class HyprlandUnavailable(RuntimeError):
    pass


def state_dir() -> Path:
    path = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "keyboard-coach"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def runtime_dir() -> Path:
    path = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "keyboard-coach"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def paused() -> bool:
    return (state_dir() / "paused").exists()


def log(message: str) -> None:
    print(f"keyboard-coach: {message}", file=sys.stderr, flush=True)


def run(argv: list[str], *, timeout: float = 10, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=check)


_banner_visible_until = 0.0


def show_banner(message: str, duration: int = DEFAULT_BANNER_DURATION_MS) -> None:
    """Show a suggestion, replacing any banner on screen and restarting its timer."""
    global _banner_visible_until
    payload = json.dumps({"message": message[:240], "duration": duration}, separators=(",", ":"))
    _banner_visible_until = time.monotonic() + duration / 1000
    try:
        run(["omarchy-shell", "-q", "keyboard-coach", "show", payload], timeout=3, check=True)
        return
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        subprocess.run(
            ["omarchy", "notification", "send", "Keyboard coach", message[:240], "-t", str(duration)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        log(f"could not display: {message}")


def close_banner() -> None:
    global _banner_visible_until
    if time.monotonic() >= _banner_visible_until:
        return
    _banner_visible_until = 0.0
    try:
        run(["omarchy-shell", "-q", "keyboard-coach", "close"], timeout=3)
    except (OSError, subprocess.SubprocessError):
        pass


# --- Hyprland ---------------------------------------------------------------

def hypr_socket_candidates(name: str = ".socket.sock") -> list[Path]:
    root = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "hypr"
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    candidates = [root / signature / name] if signature else []
    # A service started before the compositor never receives the signature, and
    # a compositor restart changes it; the newest live instance is the right one.
    discovered = []
    for path in root.glob(f"*/{name}"):
        try:
            discovered.append((path.stat().st_mtime, path))
        except OSError:
            continue
    candidates += [path for _, path in sorted(discovered, reverse=True) if path not in candidates]
    return candidates


def hypr_request(command: str) -> Any:
    for path in hypr_socket_candidates():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(1)
                client.connect(str(path))
                client.sendall(f"j/{command}".encode())
                chunks = []
                while chunk := client.recv(65536):
                    chunks.append(chunk)
        except OSError:
            continue
        try:
            return json.loads(b"".join(chunks))
        except json.JSONDecodeError as error:
            raise HyprlandUnavailable(f"unexpected reply to {command}: {error}") from error
    raise HyprlandUnavailable("no running Hyprland instance")


def cursor_position() -> tuple[int, int]:
    position = hypr_request("cursorpos")
    return int(position["x"]), int(position["y"])


def _contains(x: int, y: int, left: int, top: int, width: int, height: int) -> bool:
    return left <= x < left + width and top <= y < top + height


def window_at(x: int, y: int, clients: list[dict[str, Any]], monitors: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the Hyprland client drawn under a global layout point."""
    visible = set()
    for monitor in monitors:
        visible.add((monitor.get("activeWorkspace") or {}).get("id"))
        special = (monitor.get("specialWorkspace") or {}).get("id")
        if special:
            visible.add(special)
    candidates = []
    for client in clients:
        if not client.get("mapped", True) or client.get("hidden"):
            continue
        if (client.get("workspace") or {}).get("id") not in visible:
            continue
        (left, top), (width, height) = client.get("at", (0, 0)), client.get("size", (0, 0))
        if _contains(x, y, left, top, width, height):
            candidates.append(client)
    if not candidates:
        return {}
    # With focus-follows-mouse the pointer's window is normally focused; if the
    # focused window contains the point, nothing can be drawn over it.
    for client in candidates:
        if client.get("focusHistoryID") == 0:
            return client

    def stacking(client: dict[str, Any]) -> tuple:
        fullscreen = int(client.get("fullscreen") or 0)
        return (
            int((client.get("workspace") or {}).get("id") or 0) < 0,
            bool(client.get("pinned")), fullscreen == 2, bool(client.get("floating")), fullscreen == 1,
            -int(client.get("focusHistoryID") if client.get("focusHistoryID") is not None else 1 << 30),
        )

    return max(candidates, key=stacking)


def monitor_scale(client: dict[str, Any], monitors: list[dict[str, Any]]) -> float:
    for monitor in monitors:
        if monitor.get("id") == client.get("monitor"):
            return float(monitor.get("scale") or 1)
    return 1.0


def layer_at_point(x: int, y: int, layers: dict[str, Any]) -> str:
    matches = []
    for monitor in layers.values() if isinstance(layers, dict) else []:
        for level_text, surfaces in (monitor.get("levels") or {}).items():
            level = int(level_text)
            if level < 2:
                continue
            for surface in surfaces:
                namespace = str(surface.get("namespace") or "")
                if namespace == "keyboard-coach":
                    continue
                sx, sy = int(surface.get("x", 0)), int(surface.get("y", 0))
                sw, sh = int(surface.get("w", 0)), int(surface.get("h", 0))
                if _contains(x, y, sx, sy, sw, sh):
                    matches.append((level, -(sw * sh), namespace))
    return max(matches)[2] if matches else ""


# --- Omarchy shell ----------------------------------------------------------

def bar_widget_context(
    widgets: list[dict[str, Any]], x: int, y: int, panel_widgets: dict[str, str],
    layout_ids: list[str] | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {"namespace": "omarchy-bar", "surface": "bar"}
    target_widget = None
    for widget in widgets if isinstance(widgets, list) else []:
        if not isinstance(widget, dict) or not widget.get("visible") or widget.get("itemVisible") is False:
            continue
        wx, wy = int(widget.get("x", 0)), int(widget.get("y", 0))
        width, height = int(widget.get("width", 0)), int(widget.get("height", 0))
        if _contains(x, y, wx, wy, width, height):
            context.update({"widget": str(widget.get("id") or ""), "section": str(widget.get("section") or "")})
            target_widget = widget
            break
    if target_widget and context.get("widget") in panel_widgets:
        context["widget_name"] = panel_widgets[context["widget"]]
    # Omarchy's numbered panel bindings address only the right section and skip
    # every visible widget that does not implement the open/close/opened contract.
    if target_widget and context.get("section") == "right" and context.get("widget") in panel_widgets:
        visible_ids = {
            str(item.get("id") or "") for item in widgets
            if item.get("visible") and item.get("itemVisible") is not False and item.get("section") == "right"
        }
        source_ids = layout_ids or [str(item.get("id") or "") for item in widgets if item.get("section") == "right"]
        panel_ids = []
        for widget_id in source_ids:
            if widget_id in visible_ids and widget_id in panel_widgets and widget_id not in panel_ids:
                panel_ids.append(widget_id)
        if context["widget"] in panel_ids:
            context["panel_index"] = panel_ids.index(context["widget"]) + 1
    return context


def omarchy_context(x: int, y: int, catalog: dict[str, Any]) -> dict[str, Any]:
    namespace = layer_at_point(x, y, hypr_request("layers"))
    if not namespace:
        return {}
    if namespace != "omarchy-bar":
        return {"namespace": namespace, "surface": "layer"}
    try:
        widgets = json.loads(run(["omarchy-shell", "shell", "debugBarGeometry"], timeout=4).stdout)
        shell_config = json.loads(run(["omarchy-shell", "shell", "listShellConfig"], timeout=4).stdout)
    except (json.JSONDecodeError, OSError, subprocess.SubprocessError):
        widgets, shell_config = [], {}
    right_layout = (((shell_config or {}).get("bar") or {}).get("layout") or {}).get("right") or []
    layout_ids = [str(item.get("id") if isinstance(item, dict) else item) for item in right_layout]
    return bar_widget_context(widgets, x, y, panel_widget_registry(catalog), layout_ids)


def panel_widget_registry(catalog: dict[str, Any]) -> dict[str, str]:
    return {
        str(plugin["id"]): str(plugin.get("display_name") or plugin.get("name") or plugin["id"])
        for plugin in (catalog.get("system_index") or {}).get("plugins", [])
        if plugin.get("bar_panel") and plugin.get("id")
    }


# --- AT-SPI -----------------------------------------------------------------

def _atspi():
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError):
        return None
    return Atspi


def _safe(call, default=None):
    try:
        return call()
    except Exception:
        return default


def _role(node) -> str:
    return (_safe(node.get_role_name, "") or "").lower()


def _has_state(node, state) -> bool:
    states = _safe(node.get_state_set)
    return bool(states is not None and _safe(lambda: states.contains(state), False))


def _children(node, limit: int = 500):
    for index in range(min(_safe(node.get_child_count, 0) or 0, limit)):
        child = _safe(lambda index=index: node.get_child_at_index(index))
        if child is not None:
            yield child


def accessible_frame(app, client: dict[str, Any], Atspi):
    """Pick the accessible top-level window that corresponds to a Hyprland client.

    Wayland gives accessible windows no screen position, so identity comes from
    the title, the focused state, and the size. An ambiguous match returns None:
    a wrong window produces a confident wrong suggestion.
    """
    title = str(client.get("title") or "")
    focused = client.get("focusHistoryID") == 0
    width, height = (client.get("size") or (0, 0))
    scored = []
    for frame in _children(app, 64):
        if _role(frame) not in FRAME_ROLES or not _has_state(frame, Atspi.StateType.SHOWING):
            continue
        score = 0
        if title and (_safe(frame.get_name, "") or "") == title:
            score += 4
        if _has_state(frame, Atspi.StateType.ACTIVE):
            score += 2 if focused else -2
        extents = _safe(lambda frame=frame: frame.get_extents(Atspi.CoordType.WINDOW))
        # Chromium keeps a minimum width wider than a narrow tile, so only the
        # height is compared exactly.
        if extents and abs(extents.height - height) <= 8 and extents.width >= width - 8:
            score += 1
        scored.append((score, frame))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    if best_score < 2 or (len(scored) > 1 and scored[1][0] == best_score):
        return None
    return best


def _bounds(node, Atspi):
    extents = _safe(lambda: node.get_extents(Atspi.CoordType.WINDOW))
    if extents is None or extents.width <= 0 or extents.height <= 0:
        return None
    return extents.x, extents.y, extents.width, extents.height


def _containing_children(node, x: float, y: float, scale: float, in_document: bool, Atspi) -> list:
    """Return showing children whose bounds contain the window-local point, topmost first.

    Children without bounds are treated as transparent groupings and searched a
    few levels down. Later siblings are painted over earlier ones. A document
    publishes its own bounds in physical pixels, so it is tested with the scaled
    point even from the logical-pixel layer above it.
    """
    found = []
    frontier = [(child, 0) for child in _children(node)]
    while frontier:
        child, depth = frontier.pop(0)
        # Chromium keeps hidden views that still report full-window bounds.
        if not _has_state(child, Atspi.StateType.SHOWING):
            continue
        bounds = _bounds(child, Atspi)
        if bounds is None:
            if depth < 3:
                frontier.extend((grandchild, depth + 1) for grandchild in _children(child))
            continue
        factor = scale if in_document or _role(child) in DOCUMENT_ROLES else 1.0
        if _contains(int(x * factor), int(y * factor), *bounds):
            found.append(child)
    return list(reversed(found))


def hit_test(frame, local_x: float, local_y: float, scale: float, Atspi, budget: int = 1500):
    """Find the accessible object under a window-local point.

    Chromium's toolkit layer answers get_accessible_at_point with objects that do
    not contain the point, and overlapping views can lead a greedy descent into an
    empty container, so the toolkit layer is searched by published bounds with
    backtracking, preferring the deepest actionable control. Web documents are
    hit-tested by the renderer, which handles overlays and z-order, and its answer
    is kept only when its bounds contain the point. Chromium and Electron publish
    browser UI in logical pixels but document content in physical pixels, so the
    point is scaled inside a document.
    """
    visited = 0

    def rank(node, depth):
        return (_role(node) in ACTIONABLE_ROLES, depth)

    def descend(node, in_document: bool, depth: int):
        nonlocal visited
        visited += 1
        in_document = in_document or _role(node) in DOCUMENT_ROLES
        best_rank, best = rank(node, depth), node
        if depth >= 64 or visited > budget:
            return best_rank, best
        if in_document:
            px, py = int(local_x * scale), int(local_y * scale)
            candidate = _safe(lambda: node.get_accessible_at_point(px, py, Atspi.CoordType.WINDOW))
            bounds = _bounds(candidate, Atspi) if candidate is not None and candidate != node else None
            if bounds and _contains(px, py, *bounds):
                child_rank, child = descend(candidate, True, depth + 1)
                return (child_rank, child) if child_rank > best_rank else (best_rank, best)
        for child in _containing_children(node, local_x, local_y, scale, in_document, Atspi):
            child_rank, found = descend(child, in_document, depth + 1)
            if child_rank > best_rank:
                best_rank, best = child_rank, found
            # A control in a higher sibling is drawn over anything below it.
            if child_rank[0]:
                break
        return best_rank, best

    return descend(frame, False, 0)[1]


def actionable_target(node):
    """Climb from the deepest hit to the control the click activated."""
    target = None
    document = None
    current = node
    for _ in range(48):
        if current is None:
            break
        role = _role(current)
        if target is None and document is None and role in ACTIONABLE_ROLES:
            target = current
        if document is None and role in DOCUMENT_ROLES:
            document = current
        if role in FRAME_ROLES:
            break
        current = _safe(current.get_parent)
    return target or node, document


def popup_menu_item(app, Atspi):
    """Return the hovered item of an open popup menu, if one is showing."""
    for window in _children(app, 64):
        if _role(window) not in POPUP_ROLES or not _has_state(window, Atspi.StateType.SHOWING):
            continue
        stack, visited = [window], 0
        while stack and visited < 600:
            node = stack.pop()
            visited += 1
            if _role(node) in {"menu item", "check menu item", "radio menu item"} and (
                _has_state(node, Atspi.StateType.SELECTED) or _has_state(node, Atspi.StateType.FOCUSED)
            ):
                return node
            stack.extend(_children(node, 200))
    return None


def _target_position(node, Atspi) -> dict[str, Any]:
    """Return a one-based position among siblings with the same role."""
    parent = _safe(node.get_parent)
    role = _role(node)
    if parent is None or not role:
        return {}
    siblings = []
    selected_position = None
    for child in _children(parent):
        if _role(child) != role:
            continue
        siblings.append(child)
        if _has_state(child, Atspi.StateType.SELECTED):
            selected_position = len(siblings)
    try:
        position = siblings.index(node) + 1
    except ValueError:
        return {}
    return {
        "position": position, "set_size": len(siblings),
        "is_last": position == len(siblings), "selected_position": selected_position,
    }


DIALOG_ROLES = {"dialog", "alert", "file chooser", "color chooser", "font chooser"}


def _in_dialog(node, Atspi) -> bool:
    """Whether a control sits inside a dialog, where Esc dismisses it. A
    "Close" button elsewhere (a browser tab, a side panel) is not dismissed by Esc."""
    for _ in range(64):
        if node is None:
            return False
        role = _role(node)
        if role in DIALOG_ROLES or (role in {"frame", "window"} and _has_state(node, Atspi.StateType.MODAL)):
            return True
        if role in {"frame", "window", "application"}:
            return False
        node = _safe(node.get_parent)
    return False


def describe_target(node, document, app, Atspi) -> dict[str, Any]:
    actions = []
    for index in range(_safe(node.get_n_actions, 0) or 0):
        actions.append({
            "name": _safe(lambda index=index: node.get_action_name(index), "") or "",
            "description": _safe(lambda index=index: node.get_action_description(index), "") or "",
            "key_binding": _safe(lambda index=index: node.get_key_binding(index), "") or "",
        })
    attributes = _safe(node.get_attributes, {}) or {}
    parent = _safe(node.get_parent)
    target = {
        "name": _safe(node.get_name, "") or "",
        "role": _role(node),
        "description": _safe(node.get_description, "") or "",
        "actions": actions,
        "key_shortcuts": str(attributes.get("keyshortcuts") or ""),
        "provider": _safe(app.get_name, "") or "",
        "in_document": document is not None,
        "parent_role": _role(parent) if parent is not None else "",
        # A tab's close button closes that tab; only the selected tab is the
        # one Ctrl+W closes.
        "parent_selected": parent is not None and _has_state(parent, Atspi.StateType.SELECTED),
        "in_dialog": _in_dialog(parent, Atspi),
    }
    if document is not None:
        uri = str((_safe(lambda: Atspi.Document.get_document_attributes(document), {}) or {}).get("URI") or "")
        # Only the host is kept; paths and queries can carry private content.
        target["site"] = (urlsplit(uri).hostname or "") if uri.startswith(("http://", "https://")) else ""
    target.update(_target_position(node, Atspi))
    return target


def semantic_target(client: dict[str, Any], x: int, y: int, scale: float) -> dict[str, Any]:
    Atspi = _atspi()
    if Atspi is None or not client.get("pid"):
        return {}
    desktop = _safe(lambda: Atspi.get_desktop(0))
    if desktop is None:
        return {}
    for app in _children(desktop, 256):
        if _safe(app.get_process_id, 0) != client["pid"]:
            continue
        popup = popup_menu_item(app, Atspi)
        if popup is not None:
            return describe_target(popup, None, app, Atspi)
        frame = accessible_frame(app, client, Atspi)
        if frame is None:
            continue
        left, top = client.get("at") or (0, 0)
        node = hit_test(frame, x - left, y - top, scale, Atspi)
        target, document = actionable_target(node)
        return describe_target(target, document, app, Atspi)
    return {}


# --- Catalog ----------------------------------------------------------------

def catalog_path() -> Path:
    configured = os.environ.get("KEYBOARD_COACH_CATALOG")
    if configured:
        return Path(configured).expanduser()
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "keyboard-coach/catalog.json"


def load_catalog() -> dict[str, Any]:
    try:
        catalog = json.loads(catalog_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        catalog = {"version": 0, "entries": [], "command_sets": []}
    # Shortcuts harvested from installed applications by keyboard-coach-harvest,
    # and those it captured from running applications' accessibility trees.
    try:
        catalog["harvested"] = json.loads(catalog_path().with_name("shortcuts.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        catalog["harvested"] = {"apps": []}
    try:
        observed = json.loads(observed_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        observed = {"apps": []}
    merge_observed(catalog["harvested"], observed)
    return catalog


def observed_path() -> Path:
    return catalog_path().with_name("observed.json")


def observed_stamp() -> tuple[int, int]:
    try:
        stat = observed_path().stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def merge_observed(harvested: dict[str, Any], observed: dict[str, Any]) -> None:
    """Attach captured shortcuts to the installed app with the same window class.

    Captured entries come first: they are what the running app reports, including
    shortcuts the user reassigned inside it.
    """
    apps = harvested.setdefault("apps", [])
    for entry in observed.get("apps", []):
        classes = set(entry.get("classes", []))
        installed = next((app for app in apps if classes & set(app.get("classes", []))), None)
        if installed is None:
            apps.append(entry)
            continue
        installed["shortcuts"] = [*entry.get("shortcuts", []), *installed.get("shortcuts", [])]
        installed["sources"] = [*installed.get("sources", []), *entry.get("sources", [])]
        installed["toolkit"] = installed.get("toolkit") or entry.get("toolkit")


def _matches(patterns: list[str], value: Any) -> bool:
    if not patterns:
        return True
    values = value if isinstance(value, (list, set, tuple)) else [value]
    return any(re.search(pattern, str(item), re.IGNORECASE) for pattern in patterns for item in values)


def _words(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


class _FormatValues(dict):
    def __missing__(self, key):
        return ""


def normalize_key_binding(binding: str) -> str:
    binding = binding.split(";", 1)[0].strip()
    for source, target in {
        "<Primary>": "Ctrl+", "<Control>": "Ctrl+", "<Ctrl>": "Ctrl+",
        "<Shift>": "Shift+", "<Alt>": "Alt+", "<Super>": "Super+",
        "Control+": "Ctrl+", "Meta+": "Super+",
    }.items():
        binding = binding.replace(source, target)
    binding = re.sub(r"\+{2,}", "+", binding).strip("+")
    if not binding:
        return ""
    *modifiers, key = binding.split("+")
    key = {"Escape": "Esc", "Return": "Enter"}.get(key, key.upper() if len(key) == 1 else key)
    return "+".join([*modifiers, key])


HINT_MODIFIERS = {
    "super": "Super", "win": "Super", "logo": "Super", "meta": "Super", "mod4": "Super", "hyper": "Super",
    "primary": "Ctrl", "control": "Ctrl", "ctrl": "Ctrl", "ctl": "Ctrl",
    "alt": "Alt", "mod1": "Alt", "option": "Alt", "shift": "Shift",
}
HINT_KEYS = {
    "esc": "Esc", "escape": "Esc", "enter": "Enter", "return": "Enter", "tab": "Tab", "space": "Space",
    "backspace": "Backspace", "delete": "Delete", "del": "Delete", "insert": "Insert", "home": "Home",
    "end": "End", "pageup": "PageUp", "pagedown": "PageDown", "page up": "PageUp", "page down": "PageDown",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
}


def tooltip_hint(text: str) -> tuple[str, str] | None:
    """Split a label ending in its shortcut, such as `Play (k)` or `Sidebar (Ctrl+B)`.

    Mirrors keyboard_coach::harvest::accessibility::tooltip_hint: the parenthesized
    text must be one character, a function key, a named navigation key, or a chord
    with a modifier, so counts and words in parentheses are not mistaken for keys.
    """
    text = text.strip()
    if not text.endswith(")"):
        return None
    inner_end = text[:-1]
    open_at = inner_end.rfind("(")
    if open_at < 0:
        return None
    title = inner_end[:open_at].rstrip().rstrip(":-–—").rstrip()
    inner = inner_end[open_at + 1:].strip()
    if not title or not inner:
        return None
    parts = [part.strip() for part in inner.split("+")]
    if inner.endswith("++"):
        parts, key = parts[:-2], "+"
    else:
        key = parts.pop()
    modifiers = []
    for part in parts:
        if part.lower() not in HINT_MODIFIERS:
            return None
        modifiers.append(HINT_MODIFIERS[part.lower()])
    lower = key.lower()
    function_key = re.fullmatch(r"f\d{1,2}", lower) is not None
    if len(key) == 1:
        if key.isspace() or (not modifiers and key.isdigit()):
            return None
        rendered = key.upper() if modifiers else key
    elif function_key:
        rendered = key.upper()
    elif lower in HINT_KEYS:
        rendered = HINT_KEYS[lower]
    else:
        return None
    order = ["Super", "Ctrl", "Alt", "Shift"]
    chord = "+".join([*sorted(set(modifiers), key=order.index), rendered])
    return title, chord


MENU_PARENT_ROLES = {"menu", "popup menu", "menu item"}


def is_mnemonic(shortcut: str, label: str) -> bool:
    """`Alt` plus a letter or digit that appears in the label."""
    key = shortcut[len("Alt+"):] if shortcut.startswith("Alt+") else ""
    return len(key) == 1 and key.isalnum() and key.upper() in label.upper()


def exposed_shortcut(target: dict[str, Any]) -> str | None:
    label = target.get("name") or "activate this control"
    declared = normalize_key_binding(str(target.get("key_shortcuts") or "").split(" ", 1)[0])
    if declared:
        return f"{declared} — {label}."
    for action in target.get("actions", []):
        shortcut = normalize_key_binding(action.get("key_binding", ""))
        intent = " ".join((
            str(target.get("name", "")), str(target.get("description", "")),
            str(action.get("name", "")), str(action.get("description", "")),
        ))
        # Chromium can publish Escape on a menu-opening control after the menu
        # appears. That describes dismissing the popup, not activating the control.
        if shortcut == "Esc" and not re.search(r"\b(close|dismiss|cancel|stop)\b", intent, re.IGNORECASE):
            continue
        # Inside a menu, toolkits report the item's mnemonic when it has no
        # shortcut; that key only works while the menu is open.
        if str(target.get("parent_role") or "").lower() in MENU_PARENT_ROLES and is_mnemonic(shortcut, str(target.get("name") or "")):
            continue
        if shortcut:
            return f"{shortcut} — {target.get('name') or action.get('name') or 'activate this control'}."
    for text in (str(target.get("name") or ""), str(target.get("description") or "")):
        hint = tooltip_hint(text)
        if hint:
            return f"{hint[1]} — {hint[0]}."
    return None


MENU_BUTTON_ROLES = {"button", "push button", "toggle button"}
# A hamburger button names itself in one of two ways, and which of the name and
# the description carries it differs per toolkit: Brave names the button "Brave"
# and describes it "Customize and control Brave", Chrome reverses them, and GTK
# apps use "Main Menu" with no description. Each field is therefore tested on
# its own; concatenating them hides the label behind the product name.
MENU_BUTTON_LABELS = {
    "menu", "main menu", "app menu", "application menu", "browser menu", "open menu",
    "open application menu", "more options", "more actions", "overflow", "overflow menu",
}
MENU_BUTTON_PREFIXES = ("customize and control ", "settings and more")
CHROMIUM_APP = re.compile(r"brave|chrom(?:e|ium)|vivaldi|opera|edge|thorium", re.IGNORECASE)


def semantic_intents(context: dict[str, Any]) -> set[str]:
    """Translate unstable accessibility labels into stable UI action concepts."""
    target = context.get("target", {})
    role = str(target.get("role", "")).lower()
    if role not in MENU_BUTTON_ROLES or target.get("in_document"):
        return set()
    labels = [_words(str(target.get(field) or "")) for field in ("name", "description")]
    intents: set[str] = set()
    if any(label in MENU_BUTTON_LABELS or label.startswith(MENU_BUTTON_PREFIXES) for label in labels if label):
        intents.add("open-menu")
        if CHROMIUM_APP.search(str(context.get("app", ""))):
            intents.add("open-application-menu")
    return intents


def match_values(context: dict[str, Any]) -> dict[str, Any]:
    target = context.get("target", {})
    omarchy = context.get("omarchy", {})
    return {
        "apps": str(context.get("app", "")),
        "titles": str(context.get("title", "")),
        "intents": semantic_intents(context),
        "roles": str(target.get("role", "")),
        "names": f"{target.get('name', '')} {target.get('description', '')}".strip(),
        "actions": " ".join(
            f"{action.get('name', '')} {action.get('description', '')}" for action in target.get("actions", [])
        ).strip(),
        "sites": str(target.get("site", "")),
        "target_in_document": str(target["in_document"]).lower() if "in_document" in target else "",
        "widgets": str(omarchy.get("widget", "")),
        "namespaces": str(omarchy.get("namespace", "")),
        "surfaces": str(omarchy.get("surface", "")),
        "panel_indexes": str(omarchy.get("panel_index", "")),
        "target_positions": str(target.get("position", "")),
        "target_set_sizes": str(target.get("set_size", "")),
        "target_is_last": str(target.get("is_last", "")).lower(),
        "parent_roles": str(target.get("parent_role", "")),
        "target_parent_selected": str(target["parent_selected"]).lower() if "parent_selected" in target else "",
        "target_in_dialog": str(target["in_dialog"]).lower() if "in_dialog" in target else "",
    }


def catalog_suggestion(
    context: dict[str, Any], button: str, catalog: dict[str, Any], *, app_commands_only: bool | None = None,
) -> str | None:
    target = context.get("target", {})
    omarchy = context.get("omarchy", {})
    values = match_values(context)
    for entry in catalog.get("entries", []):
        is_app_command = bool(entry.get("command_id"))
        if app_commands_only is not None and is_app_command != app_commands_only:
            continue
        if entry.get("buttons") and button not in entry["buttons"]:
            continue
        if all(_matches(entry.get(field, []), value) for field, value in values.items()):
            substitutions = _FormatValues({
                "panel_index": omarchy.get("panel_index", ""),
                "widget_name": omarchy.get("widget_name", "Omarchy"),
                "target_position": target.get("position", ""),
                "target_set_size": target.get("set_size", ""),
            })
            shortcut = str(entry["shortcut"]).format_map(substitutions)
            description = str(entry["description"]).format_map(substitutions)
            return f"{shortcut} — {description}"
    return None


def _sentence(description: str, fallback: str) -> str:
    text = description.strip().rstrip(".") or fallback
    return text[0].lower() + text[1:] + "."


def _best_binding(bindings: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not bindings:
        return None
    return min(bindings, key=lambda binding: (binding["shortcut"].count("+"), len(binding["shortcut"]), binding["shortcut"]))


def indexed_system_suggestion(context: dict[str, Any], catalog: dict[str, Any], button: str) -> str | None:
    """Resolve a clicked Omarchy bar widget against the indexed binding set."""
    omarchy = context.get("omarchy", {})
    # Clicks inside an open layer are navigation within it; the binding that
    # opens the layer would not reproduce the action.
    if button != "left" or omarchy.get("surface") != "bar":
        return None
    bindings = [
        binding for binding in (catalog.get("system_index") or {}).get("hypr_bindings", [])
        if binding.get("shortcut") and binding.get("command")
    ]
    widget = str(omarchy.get("widget") or "")
    if len(widget) >= 3:
        pattern = re.compile(rf"(?<![\w.-]){re.escape(widget)}(?![\w.-])")
        binding = _best_binding([item for item in bindings if pattern.search(item["command"])])
        if binding:
            return f"{binding['shortcut']} — {_sentence(binding.get('description', ''), 'open this Omarchy surface')}"
    index = omarchy.get("panel_index")
    if index:
        pattern = re.compile(rf"\btogglePanelAt\s+right\s+{int(index)}$")
        binding = _best_binding([item for item in bindings if pattern.search(item["command"])])
        if binding:
            name = omarchy.get("widget_name") or "this"
            return f"{binding['shortcut']} — open the {name} panel."
    return None


HARVEST_MATCH_ROLES = {
    "button", "push button", "toggle button", "menu item", "check menu item", "radio menu item",
    "link", "page tab",
}


def normalize_label(label: str) -> str:
    """Reduce a UI label to a comparable form: mnemonics, ellipses, and case removed."""
    label = re.sub(r"_(?=\S)", "", label.replace("~", ""))
    label = re.sub(r"(\.\.\.|…)\s*$", "", label.strip())
    return " ".join(label.lower().split()).rstrip(" :")


def harvested_app(context: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any] | None:
    app = str(context.get("app") or "").lower()
    if not app:
        return None
    for entry in (catalog.get("harvested") or {}).get("apps", []):
        if app in entry.get("classes", []):
            return entry
    return None


def harvested_suggestion(context: dict[str, Any], button: str, catalog: dict[str, Any]) -> str | None:
    """Match a clicked control's label against the shortcuts harvested for its app."""
    target = context.get("target", {})
    if button != "left" or str(target.get("role", "")).lower() not in HARVEST_MATCH_ROLES:
        return None
    wanted = normalize_label(str(target.get("name") or ""))
    if not wanted:
        return None
    in_document = bool(target.get("in_document"))
    site = str(target.get("site") or "")
    entry = harvested_app(context, catalog)
    candidates = list(entry.get("shortcuts", [])) if entry else []
    if in_document and site:
        # A site's shortcuts hold in any browser window showing it, web apps included.
        candidates += [
            shortcut for app in (catalog.get("harvested") or {}).get("apps", []) if app is not entry
            for shortcut in app.get("shortcuts", []) if shortcut.get("site") == site
        ]
    for shortcut in candidates:
        # Page shortcuts apply only on their site. Application shortcuts apply to the
        # app's own interface, which in Electron apps is a document with no web host,
        # but never to a website.
        if "site" in shortcut:
            if not in_document or shortcut["site"] != site:
                continue
        elif in_document and site:
            continue
        labels = [shortcut.get("title", ""), *shortcut.get("aliases", [])]
        if wanted in {normalize_label(label) for label in labels} and shortcut.get("keys"):
            keys = " / ".join(shortcut["keys"][:2])
            title = re.sub(r"(\.\.\.|…|\.)\s*$", "", shortcut["title"].strip())
            return f"{keys} — {title}."
    return None


def installed_widget_launcher_suggestion(context: dict[str, Any], button: str, catalog: dict[str, Any]) -> str | None:
    """Route a bar widget named after an installed app through the app launcher."""
    omarchy = context.get("omarchy", {})
    name = str(omarchy.get("widget_name") or "")
    if button != "left" or not name or omarchy.get("panel_index"):
        return None
    index = catalog.get("system_index") or {}
    launcher = _best_binding([
        binding for binding in index.get("hypr_bindings", [])
        if binding.get("shortcut") and APP_LAUNCHER_COMMAND.search(str(binding.get("command") or ""))
    ])
    if launcher is None:
        return None
    wanted = _words(name)
    for app in index.get("desktop_apps", []):
        aliases = {_words(str(app.get(key) or "")) for key in ("name", "id", "startup_wm_class", "executable")}
        if wanted in aliases:
            return f"{launcher['shortcut']}, type {name}, Enter — open {name}."
    return None


# --- herdr --------------------------------------------------------------------
#
# herdr draws its sidebar, tab bar and panes as terminal text, so the terminal
# window has no accessible controls. herdr's API reports its state and every
# focus and layout change instead: a click is resolved by what it changed, such
# as the focused tab going from 1 to 3, which Alt+3 also does.

HERDR_EVENTS = (
    "workspace.created", "workspace.closed", "workspace.focused", "tab.created", "tab.closed", "tab.focused",
    "pane.created", "pane.closed", "pane.focused", "layout.updated",
)
# How long before the daemon hears about a press herdr may already have acted
# on it, and how long after the release its events may still arrive.
HERDR_PRESS_LEAD_SECONDS = 0.3
HERDR_RELEASE_SETTLE_SECONDS = 0.12


def herdr_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The parts of a herdr session snapshot a click can change."""
    return {
        "workspace": snapshot.get("focused_workspace_id"),
        "tab": snapshot.get("focused_tab_id"),
        "pane": snapshot.get("focused_pane_id"),
        "workspaces": [
            {"id": item.get("workspace_id"), "number": item.get("number"), "label": item.get("label", "")}
            for item in snapshot.get("workspaces", [])
        ],
        "tabs": [
            {"id": item.get("tab_id"), "workspace": item.get("workspace_id"), "number": item.get("number"),
             "label": item.get("label", "")}
            for item in snapshot.get("tabs", [])
        ],
        "layouts": {
            str(layout.get("tab_id")): {
                "zoomed": bool(layout.get("zoomed")),
                "panes": {str(pane.get("pane_id")): pane.get("rect", {}) for pane in layout.get("panes", [])},
            }
            for layout in snapshot.get("layouts", [])
        },
    }


def _herdr_request(socket_path: str, method: str, params: dict[str, Any], timeout: float = 0.5) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(socket_path)
        stream = client.makefile("rwb")
        stream.write(json.dumps({"id": "keyboard-coach", "method": method, "params": params}).encode() + b"\n")
        stream.flush()
        reply = json.loads(stream.readline() or b"{}")
    if "result" not in reply:
        raise OSError(f"herdr {method} failed: {reply.get('error')}")
    return reply["result"]


class HerdrWatcher:
    """Follow one herdr server, keeping each state change with the state before it."""

    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self.lock = threading.Lock()
        self.state: dict[str, Any] | None = None
        self.changes: collections.deque[tuple[float, dict[str, Any], dict[str, Any]]] = collections.deque(maxlen=64)
        threading.Thread(target=self._run, name=f"herdr {socket_path}", daemon=True).start()

    def _snapshot(self) -> dict[str, Any]:
        return herdr_state(_herdr_request(self.socket_path, "session.snapshot", {}).get("snapshot", {}))

    def _run(self) -> None:
        while True:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as events:
                    events.connect(self.socket_path)
                    stream = events.makefile("rwb")
                    request = {"subscriptions": [{"type": event} for event in HERDR_EVENTS]}
                    stream.write(json.dumps({"id": "keyboard-coach", "method": "events.subscribe", "params": request}).encode() + b"\n")
                    stream.flush()
                    stream.readline()
                    self.state = self._snapshot()
                    while stream.readline():
                        # One action emits several events; each re-read only records a real change.
                        state = self._snapshot()
                        with self.lock:
                            if self.state is not None and state != self.state:
                                self.changes.append((time.monotonic(), self.state, state))
                            self.state = state
            except (OSError, ValueError):
                pass
            with self.lock:
                self.state = None
                self.changes.clear()
            time.sleep(2)

    def effect(self, since: float) -> dict[str, Any] | None:
        """The state before the first change after `since`, and the state now."""
        with self.lock:
            recent = [change for change in self.changes if change[0] >= since]
            if not recent or self.state is None:
                return None
            return {"before": recent[0][1], "after": self.state}


_herdr_watchers: dict[str, HerdrWatcher] = {}


def herdr_sessions() -> dict[str, str]:
    """Running herdr session names and their API sockets."""
    if not shutil.which("herdr"):
        return {}
    try:
        output = subprocess.run(["herdr", "session", "list"], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    sessions = {}
    for line in output.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 4 and fields[1] == "running" and fields[-1].endswith(".sock"):
            sessions[fields[0]] = fields[-1]
    return sessions


def herdr_client_session(pid: int) -> str | None:
    """The session a herdr client running under a terminal's process is attached to."""
    pending, seen = [pid], set()
    while pending and len(seen) < 256:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            argv = Path(f"/proc/{current}/cmdline").read_bytes().split(b"\0")
            children = [
                int(child)
                for task in Path(f"/proc/{current}/task").iterdir()
                for child in (task / "children").read_text().split()
            ]
        except (OSError, ValueError):
            continue
        args = [arg.decode(errors="replace") for arg in argv if arg]
        if args and os.path.basename(args[0]) == "herdr" and current != pid:
            options = args[1:]
            # Remote clients show another machine's server; other subcommands are not clients.
            if "--remote" in options or "--no-session" in options:
                return None
            if "--session" in options and options.index("--session") + 1 < len(options):
                return options[options.index("--session") + 1]
            if options[:2] == ["session", "attach"] and len(options) >= 3:
                return options[2]
            return "default" if not options else None
        pending.extend(children)
    return None


_herdr_session_sockets: dict[str, str] = {}


def herdr_watcher(session: str) -> HerdrWatcher | None:
    if session not in _herdr_session_sockets:
        _herdr_session_sockets.update(herdr_sessions())
    socket_path = _herdr_session_sockets.get(session)
    if socket_path is None:
        return None
    if socket_path not in _herdr_watchers:
        _herdr_watchers[socket_path] = HerdrWatcher(socket_path)
    return _herdr_watchers[socket_path]


def attach_herdr(context: dict[str, Any], pressed_at: float) -> None:
    """Mark a click in a terminal running a herdr client, starting to follow its server."""
    pid = int(context.get("pid") or 0)
    if not pid or context.get("omarchy"):
        return
    session = herdr_client_session(pid)
    if session is not None and herdr_watcher(session) is not None:
        context["herdr"] = {"session": session, "pressed_at": pressed_at}


def resolve_herdr_effect(context: dict[str, Any], released_at: float) -> None:
    """Add the herdr state before and after the click, once its events have arrived."""
    herdr = context.get("herdr")
    if not herdr:
        return
    watcher = herdr_watcher(str(herdr.get("session")))
    pressed_at = float(herdr.pop("pressed_at", released_at))
    if watcher is None:
        return
    time.sleep(max(0.0, released_at + HERDR_RELEASE_SETTLE_SECONDS - time.monotonic()))
    herdr.update(watcher.effect(pressed_at - HERDR_PRESS_LEAD_SECONDS) or {})


def _herdr_neighbor(source: dict[str, Any], target: dict[str, Any], rects: list[dict[str, Any]]) -> str | None:
    """The direction from `source` in which `target` is the only adjacent pane."""
    def span(rect, axis):
        start = rect.get("x" if axis == "x" else "y", 0)
        return start, start + rect.get("width" if axis == "x" else "height", 0)

    def adjacent(direction, rect):
        across = "y" if direction in {"left", "right"} else "x"
        along = "x" if across == "y" else "y"
        (a0, a1), (b0, b1) = span(source, across), span(rect, across)
        if min(a1, b1) <= max(a0, b0):
            return None
        (s0, s1), (r0, r1) = span(source, along), span(rect, along)
        gap = r0 - s1 if direction in {"right", "down"} else s0 - r1
        return gap if gap >= 0 else None

    for direction in ("left", "right", "up", "down"):
        gaps = [(adjacent(direction, rect), rect) for rect in rects if rect is not source]
        gaps = [(gap, rect) for gap, rect in gaps if gap is not None]
        if not gaps:
            continue
        nearest = min(gap for gap, _ in gaps)
        closest = [rect for gap, rect in gaps if gap == nearest]
        if closest == [target]:
            return direction
    return None


def herdr_effect(before: dict[str, Any], after: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Name the herdr command that has the same effect as a click."""
    workspaces = {item["id"]: item for item in after.get("workspaces", [])}
    old_workspaces = {item["id"] for item in before.get("workspaces", [])}
    tabs = {item["id"]: item for item in after.get("tabs", [])}
    old_tabs = {item["id"] for item in before.get("tabs", [])}
    if after.get("workspace") != before.get("workspace"):
        if after.get("workspace") not in old_workspaces:
            return "New workspace", {}
        if before.get("workspace") not in workspaces:
            return "Close workspace", {}
        target = workspaces[after["workspace"]]
        offset = int(target.get("number") or 0) - int(workspaces[before["workspace"]].get("number") or 0)
        if offset == -1:
            return "Previous workspace", {}
        if offset == 1:
            return "Next workspace", {}
        return "Workspace picker", {"label": target.get("label") or str(target.get("number"))}
    if after.get("tab") != before.get("tab"):
        if after.get("tab") not in old_tabs:
            return "New tab", {}
        if before.get("tab") not in tabs:
            return "Close tab", {}
        number = int(tabs[after["tab"]].get("number") or 0)
        return ("Switch tab", {"number": number}) if 1 <= number <= 9 else None
    layout, old_layout = after.get("layouts", {}).get(str(after.get("tab"))), before.get("layouts", {}).get(str(before.get("tab")))
    if not layout or not old_layout:
        return None
    if after.get("pane") != before.get("pane"):
        if before.get("pane") not in layout["panes"]:
            return "Close pane", {}
        if after.get("pane") not in old_layout["panes"]:
            return None
        rects = list(layout["panes"].values())
        direction = _herdr_neighbor(layout["panes"][before["pane"]], layout["panes"][after["pane"]], rects)
        return (f"Focus pane {direction}", {}) if direction else None
    if layout["zoomed"] != old_layout["zoomed"]:
        return "Zoom", {}
    return None


def herdr_suggestion(context: dict[str, Any], catalog: dict[str, Any]) -> str | None:
    herdr = context.get("herdr") or {}
    if not herdr.get("before") or not herdr.get("after"):
        return None
    effect = herdr_effect(herdr["before"], herdr["after"])
    if effect is None:
        return None
    title, details = effect
    entry = next((app for app in (catalog.get("harvested") or {}).get("apps", []) if app.get("id") == "herdr"), None)
    shortcut = next(
        (item for item in (entry or {}).get("shortcuts", []) if item.get("title") == title and not item.get("section")), None,
    )
    if shortcut is None or not shortcut.get("keys"):
        return None
    keys = list(shortcut["keys"])
    if "number" in details:
        keys = [key.replace("1..9", str(details["number"])) for key in keys]
    # Direct chords first: they are one keystroke instead of prefix and key.
    keys.sort(key=lambda key: ", " in key)
    description = {
        "Switch tab": f"switch to tab {details.get('number')}",
        "Workspace picker": f"open the workspace picker, then choose {details.get('label')}",
    }.get(title, title[0].lower() + title[1:])
    return f"{' / '.join(keys[:2])} — {description}."


def deterministic_suggestion(
    context: dict[str, Any], button: str, catalog: dict[str, Any] | None = None,
) -> tuple[str | None, str]:
    catalog = catalog if catalog is not None else load_catalog()
    for source, resolve in (
        ("herdr", lambda: herdr_suggestion(context, catalog) if button == "left" else None),
        ("catalog", lambda: catalog_suggestion(context, button, catalog, app_commands_only=True)),
        ("system", lambda: indexed_system_suggestion(context, catalog, button)),
        ("app", lambda: exposed_shortcut(context.get("target", {})) if button == "left" else None),
        ("harvested", lambda: harvested_suggestion(context, button, catalog)),
        ("system", lambda: installed_widget_launcher_suggestion(context, button, catalog)),
        ("catalog", lambda: catalog_suggestion(context, button, catalog, app_commands_only=False)),
    ):
        suggestion = resolve()
        if suggestion:
            return suggestion, source
    return None, "none"


# --- Coaching policy --------------------------------------------------------
#
# A banner after every single click is what makes a coach into a heckler, and
# what people uninstall. Everything here is local, optional, and off by
# default: the shipped behaviour is unchanged until a config file says
# otherwise.

CONFIG_DEFAULTS: dict[str, Any] = {
    # How long a suggestion stays on screen.
    "banner_duration_ms": DEFAULT_BANNER_DURATION_MS,
    # Show a suggestion only once the same action has been clicked this many
    # times. 1 shows it the first time.
    "repeats_before_suggesting": 1,
    # How far back those repeats are counted.
    "repeat_window_hours": 168,
    # Stop showing a suggestion that has already been shown this often. 0 keeps
    # showing it.
    "stop_after_suggestions": 0,
    # Window classes to stay silent in, as regular expressions. The coach
    # still reads them; it just says nothing.
    "mute_apps": [],
    # Window classes the coach never looks at, as regular expressions: no
    # accessibility read, no capture, nothing written down.
    "ignore_apps": [],
    # Whether to keep the local log behind `report`. Turning it off also
    # disables the settings that count repeats.
    "history": True,
    # Suggestions to stay silent about, as regular expressions matched against
    # the banner text: the shortcuts you already know.
    "mute_suggestions": [],
    # "22:00" to "07:00" stays silent overnight. Empty values disable it.
    "quiet_hours": {"from": "", "to": ""},
}
HISTORY_LIMIT = 5000


def config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "keyboard-coach/config.json"


def load_config() -> dict[str, Any]:
    """Read the user's coaching preferences, falling back to the shipped ones.

    A broken or partial file never stops coaching: each key is taken only when
    it has the type the default has.
    """
    config = copy.deepcopy(CONFIG_DEFAULTS)
    try:
        configured = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return config
    if not isinstance(configured, dict):
        return config
    for key, default in CONFIG_DEFAULTS.items():
        value = configured.get(key)
        if isinstance(default, bool):
            # bool is a subclass of int, so it is taken only where one is meant.
            if isinstance(value, bool):
                config[key] = value
        elif isinstance(value, type(default)) and not isinstance(value, bool):
            config[key] = value
    for key in ("mute_apps", "mute_suggestions", "ignore_apps"):
        config[key] = [str(item) for item in config[key] if isinstance(item, str)]
    return config


def ignored_app(app: str, config: dict[str, Any] | None = None) -> bool:
    """Whether the user has put this window class out of the coach's reach."""
    config = config if config is not None else load_config()
    return any(_matches([pattern], app) for pattern in config["ignore_apps"] if pattern)


def history_path() -> Path:
    return state_dir() / "suggestions.jsonl"


def unmatched_path() -> Path:
    return state_dir() / "unmatched.jsonl"


def read_history(limit_bytes: int = 512 * 1024) -> list[dict[str, Any]]:
    """The recent suggestion log, newest last."""
    try:
        with history_path().open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            start = max(0, stream.tell() - limit_bytes)
            stream.seek(start)
            lines = stream.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if start and lines:
        # Reading from the middle of the file cuts its first line in half.
        lines = lines[1:]
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def record_suggestion(record: dict[str, Any]) -> None:
    try:
        with history_path().open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
        os.chmod(history_path(), 0o600)
    except OSError:
        return
    prune_log(history_path())


def prune_log(path: Path) -> None:
    """Keep a log bounded; these are coaching aids, not audit trails."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= HISTORY_LIMIT * 2:
        return
    _write_private(path, "\n".join(lines[-HISTORY_LIMIT:]) + "\n")


def _clock_minutes(value: str) -> int | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value).strip())
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    return hours * 60 + minutes if hours < 24 and minutes < 60 else None


def in_quiet_hours(config: dict[str, Any], now: time.struct_time) -> bool:
    quiet = config.get("quiet_hours") or {}
    start, end = _clock_minutes(quiet.get("from", "")), _clock_minutes(quiet.get("to", ""))
    if start is None or end is None or start == end:
        return False
    minute = now.tm_hour * 60 + now.tm_min
    # A range that ends before it starts runs through midnight.
    return start <= minute < end if start < end else minute >= start or minute < end


def suggestion_decision(
    suggestion: str, context: dict[str, Any], config: dict[str, Any],
    history: list[dict[str, Any]], now: float | None = None,
) -> tuple[bool, str]:
    """Whether to show this suggestion now, and why not when it is held back."""
    app = str(context.get("app") or "")
    if any(_matches([pattern], app) for pattern in config["mute_apps"] if pattern):
        return False, "muted app"
    if any(_matches([pattern], suggestion) for pattern in config["mute_suggestions"] if pattern):
        return False, "muted suggestion"
    if in_quiet_hours(config, time.localtime(now)):
        return False, "quiet hours"
    cutoff = (now if now is not None else time.time()) - max(0, int(config["repeat_window_hours"])) * 3600
    recent = [item for item in history if item.get("suggestion") == suggestion and float(item.get("time", 0)) >= cutoff]
    shown = sum(1 for item in recent if item.get("shown"))
    limit = int(config["stop_after_suggestions"])
    if limit > 0 and shown >= limit:
        return False, f"already shown {shown} times"
    repeats = max(1, int(config["repeats_before_suggesting"]))
    # The click being decided counts as one; `history` holds the ones before it.
    clicks = len(recent) + 1
    if clicks < repeats:
        return False, f"click {clicks} of {repeats} before suggesting"
    return True, ""


# --- Click handling ---------------------------------------------------------

def current_context(catalog: dict[str, Any]) -> dict[str, Any]:
    x, y = cursor_position()
    context: dict[str, Any] = {"app": "", "title": "", "pid": 0, "omarchy": {}, "target": {}}
    context["omarchy"] = omarchy_context(x, y, catalog)
    if context["omarchy"]:
        return context
    monitors = hypr_request("monitors")
    client = window_at(x, y, hypr_request("clients"), monitors)
    if not client:
        return context
    app = str(client.get("class") or "")[:120]
    if ignored_app(app):
        # Not even the window title is read: an ignored app is one the user
        # does not want looked at, not one they want looked at quietly.
        return {**context, "app": app, "ignored": True}
    context.update({
        "app": app,
        "title": str(client.get("title") or "")[:180],
        "pid": int(client.get("pid") or 0),
    })
    context["target"] = semantic_target(client, x, y, monitor_scale(client, monitors))
    return context


def snapshot_click(button: str, catalog: dict[str, Any]) -> int:
    if paused():
        return 0
    started = time.monotonic()
    try:
        snapshot = {"time": time.time(), "context": current_context(catalog)}
        snapshot["context"]["capture_ms"] = round((time.monotonic() - started) * 1000)
        attach_herdr(snapshot["context"], started)
    except HyprlandUnavailable as error:
        log(str(error))
        return 0
    _write_private(runtime_dir() / f"press-{button}.json", json.dumps(snapshot))
    return 0


def press_context(button: str) -> dict[str, Any]:
    path = runtime_dir() / f"press-{button}.json"
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        path.unlink(missing_ok=True)
        if time.time() - float(snapshot.get("time", 0)) <= 2:
            return snapshot.get("context", {})
    except (OSError, ValueError):
        pass
    return {}


def _write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)
    # O_CREAT sets the mode only for a new file; a file from an older version
    # keeps the mode it was made with until it is told otherwise.
    os.chmod(path, 0o600)


def handle_click(button: str, catalog: dict[str, Any] | None = None) -> int:
    if paused():
        return 0
    released_at = time.monotonic()
    run_dir = runtime_dir()
    with (run_dir / "worker.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        catalog = catalog if catalog is not None else load_catalog()
        try:
            context = press_context(button) or current_context(catalog)
        except HyprlandUnavailable as error:
            log(str(error))
            return 0
        config = load_config()
        if context.get("ignored"):
            close_banner()
            return 0
        resolve_herdr_effect(context, released_at)
        suggestion, source = deterministic_suggestion(context, button, catalog)
        shown, held_back = False, ""
        if suggestion:
            history = read_history() if config["history"] else []
            shown, held_back = suggestion_decision(suggestion, context, config, history)
            if config["history"]:
                record_suggestion({
                    "time": int(time.time()), "app": str(context.get("app") or ""),
                    "source": source, "suggestion": suggestion, "shown": shown,
                })
        _write_private(run_dir / "last-context.json", json.dumps({
            "button": button, "context": context, "suggestion": suggestion,
            "source": source, "shown": shown, "held_back": held_back,
        }, indent=2))
        _write_private(run_dir / "last-suggestion", suggestion or "")
        _write_private(run_dir / "last-source", source)
        if shown:
            show_banner(suggestion, int(config["banner_duration_ms"]))
        else:
            # A banner still showing belongs to an earlier click; leaving it up
            # would read as the answer for this one.
            close_banner()
            if not suggestion and config["history"]:
                record_unmatched(context)
        maybe_observe(context)
    return 0


OBSERVE_INTERVAL_SECONDS = 900
_observed_at: dict[tuple[int, str], float] = {}
_observer: subprocess.Popen | None = None
# Window classes queued for a first capture, and those already captured in
# this run of the daemon.
_pending_observations: collections.deque[str] = collections.deque(maxlen=64)
_observed_classes: set[str] = set()


def observe_window(pid: int, app: str, site: str = "", now: float | None = None) -> bool:
    """Capture one window's shortcuts in the background, at most every 15 minutes.

    A page is captured per site, because each site brings its own shortcuts.
    """
    global _observer
    if not pid or not app or ignored_app(app):
        return False
    if _observer is not None and _observer.poll() is None:
        return False
    now = time.monotonic() if now is None else now
    key = (pid, site)
    if now - _observed_at.get(key, float("-inf")) < OBSERVE_INTERVAL_SECONDS:
        return False
    command = harvest_command()
    if command is None:
        return False
    _observed_at[key] = now
    _observed_classes.add(app)
    try:
        _observer = subprocess.Popen(
            [*command, "observe", "--pid", str(pid), "--class", app],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, preexec_fn=lambda: os.nice(10),
        )
    except OSError as error:
        log(f"shortcut capture failed to start: {error}")
        return False
    return True


def maybe_observe(context: dict[str, Any], now: float | None = None) -> bool:
    """Capture the clicked app's shortcuts, for the page it is showing."""
    target = context.get("target") or {}
    pid, app = int(context.get("pid") or 0), str(context.get("app") or "")
    if not target or context.get("omarchy"):
        return False
    site = str(target.get("site") or "") if target.get("in_document") else ""
    return observe_window(pid, app, site, now)


def queue_observation(app: str) -> None:
    """Note a window class to capture once, when its first window opens."""
    if not app or app in _observed_classes or app in _pending_observations:
        return
    if ignored_app(app):
        return
    _pending_observations.append(app)


def drain_observations() -> bool:
    """Capture one queued class, resolving a live window of it to a process.

    Classes are captured one at a time: a capture reads a whole accessibility
    tree, and a session that restores a dozen windows at login would otherwise
    start a dozen at once.
    """
    if not _pending_observations or paused():
        return False
    if _observer is not None and _observer.poll() is None:
        return False
    app = _pending_observations.popleft()
    try:
        clients = hypr_request("clients")
    except HyprlandUnavailable:
        return False
    windows = [
        client for client in clients
        if str(client.get("class") or "") == app and client.get("mapped", True) and client.get("pid")
    ]
    if not windows:
        return False
    # The most recently focused window of the class is the one most likely to
    # have finished building its interface.
    window = min(windows, key=lambda client: int(client.get("focusHistoryID") or 1 << 30))
    return observe_window(int(window["pid"]), app)


def watch_windows() -> None:
    """Queue a capture for every window class that appears, from Hyprland's events.

    Waiting for a click means the first click in a newly started application is
    answered from an index that does not know it yet.
    """
    while True:
        try:
            for path in hypr_socket_candidates(".socket2.sock"):
                try:
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client.connect(str(path))
                except OSError:
                    continue
                with client, client.makefile("r", encoding="utf-8", errors="replace") as stream:
                    for line in stream:
                        event, _, data = line.strip().partition(">>")
                        fields = data.split(",")
                        if event == "openwindow" and len(fields) >= 3:
                            queue_observation(fields[2].strip())
                        elif event == "activewindow" and fields:
                            queue_observation(fields[0].strip())
                break
        except OSError:
            pass
        time.sleep(2)


def record_unmatched(context: dict[str, Any]) -> None:
    """Keep privacy-minimized local evidence for coverage work."""
    target = context.get("target", {})
    record = {
        "time": int(time.time()), "app": str(context.get("app") or "unknown"),
        "role": str(target.get("role") or ""), "provider": str(target.get("provider") or ""),
        "in_document": target.get("in_document"), "site": str(target.get("site") or ""),
        "intents": sorted(semantic_intents(context)),
        "namespace": str(context.get("omarchy", {}).get("namespace") or ""),
        "widget": str(context.get("omarchy", {}).get("widget") or ""),
    }
    path = unmatched_path()
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
        os.chmod(path, 0o600)
    except OSError:
        return
    prune_log(path)


# --- Daemon -----------------------------------------------------------------

def event_socket_path() -> Path:
    return runtime_dir() / "events.sock"


# Chromium browsers whose preferences carry shortcut customizations; mirrors
# ingest_catalog.CHROMIUM_BROWSERS, so a change there belongs here too.
CHROMIUM_PREFERENCE_DIRS = (
    "BraveSoftware/Brave-Browser", "chromium", "google-chrome", "vivaldi", "microsoft-edge",
)


def discovery_signature() -> str:
    """Fingerprint shortcut sources cheaply enough for periodic hot reloads."""
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    watched = [
        Path("/usr/share/omarchy/default/hypr"), Path("/usr/share/omarchy/shell/plugins"),
        config_home / "hypr", config_home / "omarchy/plugins", state_home / "omarchy/toggles/hypr",
        config_home / "keyboard-coach/commands", data_home / "keyboard-coach/commands",
        data_home / "keyboard-coach/base-catalog.json",
        *(
            config_home / directory / "Default/Preferences"
            for directory in CHROMIUM_PREFERENCE_DIRS
        ),
        data_home / "applications", Path("/usr/share/applications"),
        # Application shortcut sources read by keyboard-coach-harvest.
        Path("/usr/lib/libreoffice/share/registry"), config_home / "libreoffice/4/user/registrymodifications.xcu",
        Path("/etc/xdg/foot/foot.ini"), config_home / "foot", config_home / "mpv", config_home / "imv",
        config_home / "lazygit", config_home / "tmux", config_home / "herdr",
        Path.home() / ".t3/userdata/keybindings.json",
    ]
    digest = hashlib.sha256()
    allowed = {".desktop", ".json", ".qml", ".lua", ".xcd", ".xcu", ".ini", ".conf", ".yml", ".toml", ""}
    for root in watched:
        paths = [root] if root.is_file() else root.rglob("*") if root.exists() else []
        for path in paths:
            if path.suffix not in allowed and path.name != "Preferences":
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(f"{path}:{stat.st_mtime_ns}:{stat.st_size}\n".encode())
    return digest.hexdigest()


def ingest_command() -> list[str] | None:
    here = Path(__file__).resolve().parent
    for candidate in (here / "keyboard-coach-ingest", here / "ingest_catalog.py"):
        if candidate.is_file():
            return [sys.executable, str(candidate)]
    found = shutil.which("keyboard-coach-ingest")
    return [found] if found else None


def harvest_command() -> list[str] | None:
    here = Path(__file__).resolve().parent
    for candidate in (here / "keyboard-coach-harvest", here.parent / "target/release/keyboard-coach-harvest"):
        if candidate.is_file():
            return [str(candidate)]
    found = shutil.which("keyboard-coach-harvest")
    return [found] if found else None


def refresh_catalog() -> dict[str, Any] | None:
    refreshed = False
    for name, command in (("catalog", ingest_command()), ("shortcut index", harvest_command())):
        if command is None:
            log(f"{name} builder not found; not refreshed")
            continue
        argv = [*command, "--quiet"] if name == "catalog" else command
        try:
            result = run(argv, timeout=60)
        except (OSError, subprocess.SubprocessError) as error:
            log(f"{name} refresh failed: {error}")
            continue
        if result.returncode != 0:
            log(f"{name} refresh failed: {result.stderr.strip()}")
            continue
        refreshed = True
    return load_catalog() if refreshed else None


def serve() -> int:
    """Serialize press/release events and keep the indexed catalog warm."""
    socket_path = event_socket_path()
    socket_path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(socket_path))
    socket_path.chmod(0o600)
    server.settimeout(5)
    # Follow running herdr sessions from the start, so the first click in one
    # already has the state before it.
    for session in herdr_sessions():
        herdr_watcher(session)
    # Index what is already open, then everything that opens from now on.
    threading.Thread(target=watch_windows, name="hypr windows", daemon=True).start()
    try:
        for client in hypr_request("clients"):
            queue_observation(str(client.get("class") or ""))
    except HyprlandUnavailable as error:
        log(str(error))
    catalog = load_catalog()
    observed = observed_stamp()
    signature = discovery_signature()
    checked_at = time.monotonic()
    try:
        while True:
            try:
                parts = server.recv(256).decode("utf-8", errors="replace").strip().split()
            except socket.timeout:
                parts = []
            try:
                if observed_stamp() != observed:
                    # A background capture finished since the last event.
                    observed = observed_stamp()
                    catalog = load_catalog()
                if parts == ["reload"]:
                    catalog = load_catalog()
                elif len(parts) == 2 and parts[0] == "snapshot" and parts[1] in BUTTONS:
                    snapshot_click(parts[1], catalog)
                elif len(parts) == 2 and parts[0] == "click" and parts[1] in BUTTONS:
                    handle_click(parts[1], catalog)
                drain_observations()
                if time.monotonic() - checked_at >= 10:
                    checked_at = time.monotonic()
                    current_signature = discovery_signature()
                    if current_signature != signature:
                        # Record the attempt even on failure: a broken user pack
                        # must not rerun ingestion every ten seconds.
                        signature = current_signature
                        catalog = refresh_catalog() or catalog
            except Exception:
                traceback.print_exc(file=sys.stderr)
    finally:
        server.close()
        socket_path.unlink(missing_ok=True)


# --- CLI --------------------------------------------------------------------

def set_paused(value: bool) -> int:
    marker = state_dir() / "paused"
    if value:
        marker.touch(mode=0o600, exist_ok=True)
        show_banner("Keyboard Coach paused")
    else:
        marker.unlink(missing_ok=True)
        show_banner("Keyboard Coach active")
    return 0


def coverage_report(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    loaded = catalog if catalog is not None else load_catalog()
    index = loaded.get("system_index", {})
    plugins = index.get("plugins", [])
    unmatched_by_app: dict[str, int] = {}
    try:
        lines = unmatched_path().read_text(encoding="utf-8").splitlines()[-500:]
    except OSError:
        lines = []
    for line in lines:
        try:
            app = str(json.loads(line).get("app") or "unknown")
        except json.JSONDecodeError:
            continue
        unmatched_by_app[app] = unmatched_by_app.get(app, 0) + 1
    return {
        "catalog_version": loaded.get("version", 0),
        "app_packs": len(loaded.get("command_sets", [])),
        "match_rules": len(loaded.get("entries", [])),
        "installed_apps": len(index.get("desktop_apps", [])),
        "indexed_bindings": len(index.get("hypr_bindings", [])),
        "bindings_with_commands": sum(bool(item.get("command")) for item in index.get("hypr_bindings", [])),
        "binding_source": index.get("hypr_binding_source", ""),
        "omarchy_commands": len(index.get("omarchy_commands", [])),
        "plugins": len(plugins),
        "bar_panel_plugins": sum(bool(item.get("bar_panel")) for item in plugins),
        "plugins_with_declared_shortcuts": sum(bool(item.get("declared_shortcuts")) for item in plugins),
        "plugin_contract_errors": sum(bool(item.get("contract_error")) for item in plugins),
        "unmatched_by_app": dict(sorted(unmatched_by_app.items(), key=lambda item: (-item[1], item[0]))),
        "apps": sorted((
            {
                "id": app.get("id", ""), "name": app.get("name", ""), "toolkit": app.get("toolkit") or "",
                "shortcuts": len(app.get("shortcuts", [])),
                "sources": sorted({source.get("harvester", "") for source in app.get("sources", []) if source.get("shortcuts")}),
            }
            for app in (loaded.get("harvested") or {}).get("apps", [])
        ), key=lambda app: (-app["shortcuts"], app["id"])),
    }


# --- Doctor -----------------------------------------------------------------
#
# Coverage depends on what each application publishes through accessibility,
# which differs per toolkit and is off by default in several of them. The
# doctor reports, per open window, whether the daemon can see anything at all
# and what would change that.

TERMINAL_CLASSES = {
    "foot", "footclient", "alacritty", "kitty", "org.wezfurlong.wezterm", "wezterm",
    "com.mitchellh.ghostty", "ghostty", "xterm", "st", "urxvt", "org.omarchy.terminal",
}
ACCESSIBILITY_FIXES = {
    "qt-widgets": "set QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 for the session and restart it",
    "qt-quick": "set QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 for the session and restart it",
    "kde": "set QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 for the session and restart it",
    "chromium": "add --force-renderer-accessibility to its flags file, then restart it",
    "electron": "start it with --force-renderer-accessibility, then restart it",
    "gtk3": "run: gsettings set org.gnome.desktop.interface toolkit-accessibility true",
}


def accessible_pids() -> set[int] | None:
    """Processes that publish an accessibility tree, or None without AT-SPI."""
    Atspi = _atspi()
    if Atspi is None:
        return None
    desktop = _safe(lambda: Atspi.get_desktop(0))
    if desktop is None:
        return None
    found = set()
    for app in _children(desktop, 256):
        pid = _safe(app.get_process_id, 0)
        if pid:
            found.add(int(pid))
    return found


def indexed_app_entry(app: str, catalog: dict[str, Any]) -> dict[str, Any] | None:
    lowered = app.lower()
    for entry in (catalog.get("harvested") or {}).get("apps", []):
        if lowered in entry.get("classes", []):
            return entry
    return None


def covering_packs(app: str, catalog: dict[str, Any]) -> list[str]:
    return [
        str(command_set.get("id") or "")
        for command_set in catalog.get("command_sets", [])
        if _matches(command_set.get("apps", []), app)
    ]


def environment_checks(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str, fix: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "fix": "" if ok else fix})

    bindings = config_home / "hypr/bindings.lua"
    try:
        installed = "BEGIN keyboard-coach" in bindings.read_text(encoding="utf-8")
    except OSError:
        installed = False
    check("Click bindings", installed, str(bindings), "run scripts/install.sh again")
    check("socat", bool(shutil.which("socat")), "delivers press and release events", "install socat")
    check(
        "Lua interpreter", bool(shutil.which("lua5.5") or shutil.which("lua")),
        "reads Omarchy's binding commands", "install lua",
    )
    harvester = harvest_command()
    check("Shortcut harvester", harvester is not None, harvester[0] if harvester else "not found", "run scripts/install.sh again")

    entries = len(catalog.get("entries", []))
    check("Catalog", entries > 0, f"{entries} match rules, {len(catalog.get('command_sets', []))} app packs", "run keyboard-coach-ingest")

    pids = accessible_pids()
    check(
        "Accessibility bus", bool(pids), "no AT-SPI connection" if pids is None else f"{len(pids or [])} applications visible",
        "install python-atspi and at-spi2-core",
    )

    toolkit_accessibility = run(["gsettings", "get", "org.gnome.desktop.interface", "toolkit-accessibility"]).stdout.strip()
    check(
        "GTK accessibility", toolkit_accessibility == "true", toolkit_accessibility or "unknown",
        ACCESSIBILITY_FIXES["gtk3"],
    )
    qt_value = os.environ.get("QT_LINUX_ACCESSIBILITY_ALWAYS_ON")
    qt_file = config_home / "environment.d/90-keyboard-coach.conf"
    # The file is read when the session starts, so writing it does not change
    # the session that wrote it; saying "run the installer again" would be wrong.
    check(
        "Qt accessibility", qt_value == "1",
        "QT_LINUX_ACCESSIBILITY_ALWAYS_ON=" + (qt_value or "unset"),
        f"written to {qt_file}; log out and back in to pick it up" if qt_file.exists()
        else "run scripts/install.sh again, then log out and back in",
    )
    flags = sorted(config_home.glob("*-flags.conf"))
    enabled = [path.name for path in flags if "--force-renderer-accessibility" in _read_text(path)]
    check(
        "Chromium accessibility", bool(enabled) or not flags,
        ", ".join(enabled) if enabled else "no flags file carries --force-renderer-accessibility",
        "add --force-renderer-accessibility to " + ", ".join(path.name for path in flags),
    )
    return checks


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def window_report(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        clients = hypr_request("clients")
    except HyprlandUnavailable:
        return []
    pids = accessible_pids()
    seen = set()
    rows = []
    for client in clients:
        app = str(client.get("class") or "")
        pid = int(client.get("pid") or 0)
        if not app or (app, pid) in seen:
            continue
        seen.add((app, pid))
        entry = indexed_app_entry(app, catalog) or {}
        toolkit = str(entry.get("toolkit") or "")
        accessible = None if pids is None else pid in pids
        shortcuts = len(entry.get("shortcuts", []))
        packs = covering_packs(app, catalog)
        terminal = app.lower() in TERMINAL_CLASSES
        if terminal:
            advice = "terminal: its interface is text, so shortcuts come from the program running in it"
        elif accessible is False:
            advice = ACCESSIBILITY_FIXES.get(toolkit, "this window publishes no accessibility tree; check the app's accessibility support")
        elif shortcuts or packs:
            advice = ""
        else:
            advice = "no shortcuts indexed yet: click in it once, or run keyboard-coach-harvest observe --pid {} --class {} --print".format(pid, app)
        rows.append({
            "app": app, "pid": pid, "toolkit": toolkit, "accessible": accessible,
            "shortcuts": shortcuts, "packs": packs, "advice": advice,
        })
    return sorted(rows, key=lambda row: row["app"])


def recorded_report() -> dict[str, Any]:
    """What the coach is holding about this user right now, file by file.

    The coach reads a great deal to do its job, so it should be able to say
    exactly what it kept.
    """
    config = load_config()
    files = []
    for path in recorded_paths():
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append({
            "path": str(path), "bytes": stat.st_size,
            "mode": format(stat.st_mode & 0o777, "03o"), "modified": int(stat.st_mtime),
        })
    return {
        "history": config["history"],
        "ignored_apps": config["ignore_apps"],
        "files": files,
        "leaves_the_machine": False,
    }


def doctor_report(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    loaded = catalog if catalog is not None else load_catalog()
    return {
        "daemon": {
            "running": event_socket_path().exists(),
            "paused": paused(),
        },
        "environment": environment_checks(loaded),
        "windows": window_report(loaded),
        "recorded": recorded_report(),
    }


def print_doctor(report: dict[str, Any]) -> None:
    daemon = report["daemon"]
    state = "paused" if daemon["paused"] else "running" if daemon["running"] else "not running"
    print(f"Daemon: {state}")
    print("Environment:")
    for check in report["environment"]:
        mark = "ok  " if check["ok"] else "warn"
        print(f"  [{mark}] {check['name']}: {check['detail']}")
        if check["fix"]:
            print(f"         fix: {check['fix']}")
    print("Open windows:")
    if not report["windows"]:
        print("  none")
    for row in report["windows"]:
        seen = "no tree" if row["accessible"] is False else "unknown" if row["accessible"] is None else "accessible"
        packs = f", packs: {', '.join(row['packs'])}" if row["packs"] else ""
        toolkit = row["toolkit"] or "unknown toolkit"
        print(f"  {row['app']} (pid {row['pid']}): {toolkit}, {seen}, {row['shortcuts']} shortcuts{packs}")
        if row["advice"]:
            print(f"    {row['advice']}")
    recorded = report["recorded"]
    print("Recorded on this machine:")
    for row in recorded["files"]:
        print(f"  {row['bytes']:>9} bytes  mode {row['mode']}  {row['path']}")
    if not recorded["files"]:
        print("  nothing")
    ignored = ", ".join(recorded["ignored_apps"]) or "none"
    print(f"  History: {'on' if recorded['history'] else 'off'}. Ignored apps: {ignored}.")
    print("  Nothing is sent anywhere. `keyboard-coach forget` removes all of it.")


def report_summary(days: int = 7, now: float | None = None) -> dict[str, Any]:
    """What you clicked that has a shortcut, and whether you are clicking it less."""
    now = time.time() if now is None else now
    window = max(1, days) * 86400
    config = load_config()
    history = read_history() if config["history"] else []
    current = [item for item in history if float(item.get("time", 0)) >= now - window]
    previous = [
        item for item in history
        if now - 2 * window <= float(item.get("time", 0)) < now - window
    ]
    counts: dict[str, dict[str, Any]] = {}
    for item in current:
        suggestion = str(item.get("suggestion") or "")
        if not suggestion:
            continue
        row = counts.setdefault(suggestion, {"suggestion": suggestion, "clicks": 0, "shown": 0, "apps": set()})
        row["clicks"] += 1
        row["shown"] += bool(item.get("shown"))
        row["apps"].add(str(item.get("app") or ""))
    unmatched: dict[str, int] = {}
    try:
        lines = unmatched_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if float(record.get("time", 0)) >= now - window:
            app = str(record.get("app") or "unknown")
            unmatched[app] = unmatched.get(app, 0) + 1
    top = sorted(counts.values(), key=lambda row: (-row["clicks"], row["suggestion"]))
    return {
        "days": days,
        "history": config["history"],
        "coached_clicks": len(current),
        "previous_period_clicks": len(previous),
        "distinct_actions": len(counts),
        "top": [{**row, "apps": sorted(row["apps"])} for row in top[:10]],
        "unmatched_by_app": dict(sorted(unmatched.items(), key=lambda item: (-item[1], item[0]))[:10]),
    }


def print_report(report: dict[str, Any]) -> None:
    days = report["days"]
    if not report.get("history", True):
        print("History is off in config.json, so there is nothing to report.")
        print("Anything recorded before it was turned off is removed by `keyboard-coach forget`.")
        return
    print(f"Last {days} day{'s' if days != 1 else ''}: {report['coached_clicks']} clicks with a keyboard equivalent, "
          f"{report['distinct_actions']} distinct actions.")
    previous = report["previous_period_clicks"]
    if previous:
        change = report["coached_clicks"] - previous
        direction = "fewer" if change < 0 else "more"
        print(f"That is {abs(change)} {direction} than the {days} days before.")
    if report["top"]:
        print("Worth learning first:")
        for row in report["top"]:
            apps = ", ".join(row["apps"]) or "unknown"
            print(f"  {row['clicks']:>4}x  {row['suggestion']}  ({apps})")
    if report["unmatched_by_app"]:
        print("Clicks with no known shortcut:")
        for app, count in report["unmatched_by_app"].items():
            print(f"  {count:>4}x  {app}")
        print("Run `keyboard-coach doctor` to see whether those apps are visible to the coach.")


def recorded_paths() -> list[Path]:
    """Every file holding something the coach noticed the user do."""
    run_dir = runtime_dir()
    return [
        history_path(), unmatched_path(), observed_path(),
        run_dir / "last-context.json", run_dir / "last-suggestion", run_dir / "last-source",
        *(run_dir / f"press-{button}.json" for button in sorted(BUTTONS)),
    ]


def forget() -> int:
    """Erase what the coach has recorded, leaving the settings and the index.

    Captures of running windows rebuild themselves as those windows are opened
    again, so this clears what is known now, not what can be learned later.
    """
    removed = 0
    for path in recorded_paths():
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as error:
            log(f"could not remove {path}: {error}")
            return 1
        print(f"removed {path}")
        removed += 1
    print(f"Forgot {removed} file{'' if removed == 1 else 's'}." if removed else "Nothing recorded.")
    return 0


USAGE = (
    "usage: keyboard-coach serve | snapshot|click <left|right|middle> | pause | resume | toggle | status"
    " | inspect [seconds] | last | forget | coverage [--json] | doctor [--json] | report [--days N] [--json]"
)


def main(argv: list[str]) -> int:
    command, arguments = (argv[0], argv[1:]) if argv else ("", [])
    if command == "serve" and not arguments:
        return serve()
    if command in {"snapshot", "click"} and len(arguments) == 1 and arguments[0] in BUTTONS:
        catalog = load_catalog()
        return snapshot_click(arguments[0], catalog) if command == "snapshot" else handle_click(arguments[0], catalog)
    if command in {"pause", "resume"} and not arguments:
        return set_paused(command == "pause")
    if command == "toggle" and not arguments:
        return set_paused(not paused())
    if command == "status" and not arguments:
        print("paused" if paused() else "active")
        return 0
    if command == "inspect" and len(arguments) <= 1:
        time.sleep(float(arguments[0]) if arguments else 0)
        catalog = load_catalog()
        context = current_context(catalog)
        suggestion, source = deterministic_suggestion(context, "left", catalog)
        print(json.dumps({"context": context, "left_click": {"suggestion": suggestion, "source": source}}, indent=2))
        return 0
    if command == "last" and not arguments:
        try:
            print((runtime_dir() / "last-context.json").read_text(encoding="utf-8"))
        except OSError:
            print("no click recorded since login", file=sys.stderr)
            return 1
        return 0
    if command == "forget" and not arguments:
        return forget()
    if command == "report":
        days, rest = 7, list(arguments)
        if len(rest) >= 2 and rest[0] == "--days" and rest[1].isdigit():
            days, rest = int(rest[1]), rest[2:]
        if rest in ([], ["--json"]):
            summary = report_summary(days)
            if rest:
                print(json.dumps(summary, indent=2))
            else:
                print_report(summary)
            return 0
    if command == "doctor" and arguments in ([], ["--json"]):
        report = doctor_report()
        if arguments:
            print(json.dumps(report, indent=2))
        else:
            print_doctor(report)
        return 0
    if command == "coverage" and arguments in ([], ["--json"]):
        report = coverage_report()
        if arguments:
            print(json.dumps(report, indent=2))
            return 0
        for key, value in report.items():
            if key not in {"unmatched_by_app", "apps"}:
                print(f"{key.replace('_', ' ').title()}: {value}")
        covered = [app for app in report["apps"] if app["shortcuts"]]
        print(f"Apps with harvested shortcuts: {len(covered)} of {len(report['apps'])}")
        for app in covered:
            print(f"  {app['shortcuts']:>4}  {app['name']} ({', '.join(app['sources'])})")
        uncovered: dict[str, list[str]] = {}
        for app in report["apps"]:
            if not app["shortcuts"]:
                uncovered.setdefault(app["toolkit"] or "other", []).append(app["name"])
        if uncovered:
            print("Apps without harvested shortcuts, by toolkit:")
            for toolkit, names in sorted(uncovered.items()):
                print(f"  {toolkit}: {', '.join(sorted(names))}")
        if report["unmatched_by_app"]:
            print("Unmatched clicks (last 500):")
            for app, count in report["unmatched_by_app"].items():
                print(f"  {app}: {count}")
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
