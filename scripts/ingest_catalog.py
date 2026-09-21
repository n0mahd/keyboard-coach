#!/usr/bin/env python3
"""Validate app command packs and compile the runtime shortcut catalog."""

from __future__ import annotations

import argparse
import copy
import configparser
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any


MATCH_FIELDS = {
    "actions", "buttons", "intents", "names", "namespaces", "panel_indexes", "parent_roles", "roles", "sites",
    "surfaces", "target_in_dialog", "target_in_document", "target_is_last", "target_parent_selected",
    "target_positions", "target_set_sizes", "titles", "widgets",
}


def default_data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "keyboard-coach"


def default_config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "keyboard-coach"


def run_json(argv: list[str], default: Any) -> Any:
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=8, check=True)
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return default


def format_hypr_binding(binding: dict[str, Any]) -> str:
    parts = []
    mask = int(binding.get("modmask") or 0)
    for bit, name in ((64, "Super"), (4, "Ctrl"), (8, "Alt"), (1, "Shift")):
        if mask & bit:
            parts.append(name)
    key = str(binding.get("key") or "")
    code_match = re.fullmatch(r"code:(1[0-9])", key)
    if code_match:
        code = int(code_match.group(1))
        key = str(code - 9) if code <= 18 else "0"
    key = {"escape": "Esc", "return": "Enter", "space": "Space"}.get(
        key.lower(), key.upper() if len(key) == 1 else key,
    )
    return "+".join([*parts, key]) if key else ""


MODIFIER_NAMES = {
    "SUPER": "Super", "WIN": "Super", "LOGO": "Super", "MOD4": "Super",
    "CTRL": "Ctrl", "CONTROL": "Ctrl", "ALT": "Alt", "MOD1": "Alt", "SHIFT": "Shift",
}
MODIFIER_ORDER = ("Super", "Ctrl", "Alt", "Shift")
KEY_NAMES = {
    "space": "Space", "return": "Enter", "enter": "Enter", "escape": "Esc", "tab": "Tab",
    "backspace": "Backspace", "delete": "Delete", "insert": "Insert", "print": "Print",
    "home": "Home", "end": "End", "prior": "PageUp", "page_up": "PageUp", "next": "PageDown",
    "page_down": "PageDown", "left": "Left", "right": "Right", "up": "Up", "down": "Down",
    "comma": ",", "period": ".", "slash": "/", "backslash": "\\", "minus": "-", "equal": "=",
    "grave": "`", "semicolon": ";", "apostrophe": "'", "bracketleft": "[", "bracketright": "]",
}
# Hyprland keycodes are evdev codes plus eight: code:10 is the 1 key.
KEYCODE_NAMES = {**{10 + index: str((index + 1) % 10) for index in range(10)}, 20: "-", 21: "="}


def format_hypr_keys(keys: str) -> str:
    """Render a Hyprland Lua key string such as `SUPER + CTRL + code:10`."""
    modifiers = set()
    key = ""
    for part in (item.strip() for item in keys.split("+")):
        if not part:
            continue
        if part.upper() in MODIFIER_NAMES:
            modifiers.add(MODIFIER_NAMES[part.upper()])
            continue
        code = re.fullmatch(r"code:(\d+)", part)
        if code:
            key = KEYCODE_NAMES.get(int(code.group(1)), part)
        elif len(part) == 1:
            key = part.upper()
        else:
            key = KEY_NAMES.get(part.lower(), part)
    if not key:
        return ""
    return "+".join([*(name for name in MODIFIER_ORDER if name in modifiers), key])


def hypr_dump_script() -> Path | None:
    configured = os.environ.get("KEYBOARD_COACH_HYPR_DUMP")
    for candidate in (
        Path(configured) if configured else None,
        Path(__file__).resolve().with_name("hypr-bind-dump.lua"),
        default_data_dir() / "hypr-bind-dump.lua",
    ):
        if candidate and candidate.is_file():
            return candidate
    return None


def lua_interpreter() -> str | None:
    # Hyprland embeds Lua 5.5; prefer the matching interpreter.
    for name in ("lua5.5", "lua"):
        found = shutil.which(name)
        if found:
            return found
    return None


def replay_hypr_config(entry: Path | None = None) -> dict[str, Any] | None:
    script, lua = hypr_dump_script(), lua_interpreter()
    if script is None or lua is None:
        return None
    argv = [lua, str(script)] + ([str(entry)] if entry else [])
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=15, check=True)
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def bindings_from_replay(payload: dict[str, Any], live: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    live_descriptions: dict[str, int] = {}
    for item in live or []:
        if isinstance(item, dict):
            description = str(item.get("description") or "")
            live_descriptions[description] = live_descriptions.get(description, 0) + 1
    records = []
    for item in payload.get("bindings", []):
        if not isinstance(item, dict):
            continue
        keys = str(item.get("keys") or "")
        flags = item.get("flags") if isinstance(item.get("flags"), dict) else {}
        # Mouse and scroll bindings have no keyboard equivalent to teach.
        if "mouse" in keys.lower() or flags.get("mouse"):
            continue
        shortcut = format_hypr_keys(keys)
        if not shortcut:
            continue
        description = str(item.get("description") or "")
        record = {
            "shortcut": shortcut,
            "keys": keys,
            "description": description,
            "command": str(item.get("command") or ""),
            "dispatcher": str(item.get("dispatcher") or item.get("kind") or ""),
            "source": str(item.get("source") or ""),
            "confidence": "configured",
        }
        if live is not None:
            record["live"] = live_descriptions.get(description, 0) > 0
        records.append(record)
    return records


def discover_hypr_bindings() -> tuple[list[dict[str, Any]], str]:
    """Index bindings from the Lua config, falling back to hyprctl's reduced view."""
    live = run_json(["hyprctl", "binds", "-j"], None)
    live = live if isinstance(live, list) else None
    payload = replay_hypr_config()
    if payload is not None and payload.get("bindings"):
        return bindings_from_replay(payload, live), "lua-replay"
    records = []
    for item in live or []:
        if not isinstance(item, dict) or item.get("mouse"):
            continue
        shortcut = format_hypr_binding(item)
        if not shortcut:
            continue
        # Lua-registered bindings report only an opaque `__lua <ref>` argument.
        command = "" if item.get("dispatcher") == "__lua" else str(item.get("arg") or "")
        records.append({
            "shortcut": shortcut,
            "description": str(item.get("description") or ""),
            "command": command,
            "dispatcher": str(item.get("dispatcher") or ""),
            "source": "hyprland-live",
            "confidence": "effective",
        })
    return records, "hyprctl"


def desktop_app_roots() -> list[Path]:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    roots = [
        data_home / "applications", Path("/usr/share/applications"),
        Path("/usr/share/omarchy/applications"), Path("/usr/share/omarchy/default/applications"),
    ]
    for value in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"):
        candidate = Path(value) / "applications"
        if candidate not in roots:
            roots.append(candidate)
    roots.extend([
        Path.home() / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
    ])
    return roots


def discover_desktop_apps(roots: list[Path] | None = None) -> list[dict[str, Any]]:
    records = []
    seen = set()
    for root in roots or desktop_app_roots():
        if not root.exists():
            continue
        for path in sorted(root.glob("*.desktop")):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(path, encoding="utf-8")
                entry = parser["Desktop Entry"]
                if entry.get("Type", "Application") != "Application":
                    continue
                name = entry.get("Name", "").strip()
                exec_line = entry.get("Exec", "").strip()
            except (OSError, KeyError, ValueError, configparser.Error):
                continue
            identity = (path.stem, exec_line)
            if not name or identity in seen:
                continue
            seen.add(identity)
            try:
                argv = shlex.split(exec_line)
            except ValueError:
                argv = []
            executable = next((Path(value).name for value in argv if value != "env" and "=" not in value and not value.startswith(("-", "%"))), "")
            records.append({
                "id": path.stem, "name": name, "exec": exec_line, "executable": executable,
                "startup_wm_class": entry.get("StartupWMClass", "").strip(),
                "source": str(path), "confidence": "installed",
            })
    return records


def plugin_roots() -> list[Path]:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return [Path("/usr/share/omarchy/shell/plugins"), config_home / "omarchy/plugins"]


def _shortcut_contract(manifest_path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    contract: Any = manifest.get("keyboardCoach")
    default_path = manifest_path.parent / "shortcuts.json"
    if isinstance(contract, dict) and isinstance(contract.get("shortcuts"), str):
        contract_path = manifest_path.parent / contract["shortcuts"]
        contract = read_json(contract_path) if contract_path.exists() else {}
    elif contract is None and default_path.exists():
        contract = read_json(default_path)
    if contract and (not isinstance(contract, dict) or contract.get("schema_version", 1) != 1):
        raise ValueError(f"{manifest_path}: keyboard shortcut contract schema_version must be 1")
    shortcuts = contract.get("shortcuts", []) if isinstance(contract, dict) else []
    records = []
    for item in shortcuts:
        if not isinstance(item, dict) or not item.get("shortcut") or not item.get("description"):
            continue
        match = item.get("match") or {}
        if not isinstance(match, dict) or set(match) - MATCH_FIELDS:
            raise ValueError(f"{manifest_path}: shortcut contract contains unsupported match fields")
        for field, patterns in match.items():
            validate_patterns(manifest_path, field, patterns)
        records.append({
            "shortcut": str(item["shortcut"]), "description": str(item["description"]),
            "intent": str(item.get("intent") or ""), "match": match,
            "when": str(item.get("when") or ""), "source": "plugin-contract", "confidence": "declared",
        })
    return records


def _qml_shortcut_inventory(plugin_dir: Path) -> list[dict[str, str]]:
    records = []
    pair_pattern = re.compile(r"\{\s*keys:\s*[\"']([^\"']+)[\"']\s*,\s*action:\s*[\"']([^\"']+)[\"']")
    shortcut_pattern = re.compile(r"Shortcut\s*\{.{0,500}?sequence:\s*[\"']([^\"']+)[\"']", re.DOTALL)
    for path in plugin_dir.rglob("*.qml"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for shortcut, description in pair_pattern.findall(text):
            records.append({"shortcut": shortcut, "description": description, "source": str(path), "confidence": "source"})
        for shortcut in shortcut_pattern.findall(text):
            records.append({"shortcut": shortcut, "description": "", "source": str(path), "confidence": "source-unmapped"})
    unique = {(item["shortcut"], item["description"], item["source"]): item for item in records}
    return list(unique.values())


def _markdown_shortcut_inventory(plugin_dir: Path) -> list[dict[str, str]]:
    """Index explicit shortcut tables as diagnostics, never as executable rules."""
    records = []
    row_pattern = re.compile(r"^\s*\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|", re.MULTILINE)
    for path in plugin_dir.rglob("README.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for shortcut, description in row_pattern.findall(text):
            if not re.search(r"(?:Ctrl|Alt|Shift|Super|Enter|Return|Escape|Esc|Tab|Space|F\d|[/+])", shortcut, re.IGNORECASE):
                continue
            records.append({
                "shortcut": shortcut.strip(), "description": description.strip(),
                "source": str(path), "confidence": "documented-unmapped",
            })
    unique = {(item["shortcut"], item["description"], item["source"]): item for item in records}
    return list(unique.values())


def bar_panel_widget(manifest_path: Path, manifest: dict[str, Any]) -> bool:
    """Detect the open/close/opened contract Omarchy's numbered panel bindings use."""
    if "bar-widget" not in (manifest.get("kinds") or []):
        return False
    entry = str((manifest.get("entryPoints") or {}).get("barWidget") or "")
    try:
        qml = (manifest_path.parent / entry).read_text(encoding="utf-8") if entry else ""
    except OSError:
        return False
    if re.search(r"^\s*(?:\w+\.)?Panel\s*\{", qml, re.MULTILINE):
        return True
    return all(re.search(pattern, qml, re.MULTILINE) for pattern in (
        r"\bproperty\s+bool\s+opened\b", r"\bfunction\s+open\s*\(", r"\bfunction\s+close\s*\(",
    ))


def discover_plugins(roots: list[Path] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    plugins_by_id: dict[str, dict[str, Any]] = {}
    entries_by_id: dict[str, list[dict[str, Any]]] = {}
    for root in roots or plugin_roots():
        if not root.exists():
            continue
        for manifest_path in sorted(root.rglob("manifest.json")):
            try:
                manifest = read_json(manifest_path)
            except ValueError:
                continue
            plugin_id = str(manifest.get("id") or "")
            if not plugin_id:
                continue
            contract_error = ""
            try:
                declared = _shortcut_contract(manifest_path, manifest)
            except ValueError as error:
                declared = []
                contract_error = str(error)
            inferred = _qml_shortcut_inventory(manifest_path.parent)
            documented = _markdown_shortcut_inventory(manifest_path.parent)
            plugins_by_id[plugin_id] = {
                "id": plugin_id, "name": str(manifest.get("name") or plugin_id),
                "display_name": str((manifest.get("barWidget") or {}).get("displayName") or manifest.get("name") or plugin_id),
                "bar_panel": bar_panel_widget(manifest_path, manifest),
                "kinds": manifest.get("kinds") or [], "path": str(manifest_path.parent),
                "declared_shortcuts": declared, "source_shortcuts": inferred,
                "documented_shortcuts": documented,
                "contract_error": contract_error,
            }
            plugin_entries = []
            for shortcut in declared:
                match = shortcut.get("match") or {}
                if shortcut.get("intent"):
                    match = {**match, "intents": [f"^{re.escape(shortcut['intent'])}$"]}
                entry = {
                    **match, "shortcut": shortcut["shortcut"], "description": shortcut["description"],
                    "priority": 300, "command_id": f"plugin.{plugin_id}",
                    "provenance": {"source": shortcut["source"], "confidence": shortcut["confidence"]},
                }
                entry.setdefault("namespaces", [f"^{re.escape(plugin_id)}$"])
                entry.setdefault("buttons", ["left"])
                plugin_entries.append(entry)
            entries_by_id[plugin_id] = plugin_entries
    return list(plugins_by_id.values()), [entry for entries in entries_by_id.values() for entry in entries]


def discover_system_index() -> dict[str, Any]:
    plugins, plugin_entries = discover_plugins()
    commands_payload = run_json(["omarchy", "commands", "--json"], {})
    commands = commands_payload.get("commands", []) if isinstance(commands_payload, dict) else []
    package_path = Path("/usr/share/omarchy/install/omarchy-base.packages")
    try:
        packages = [line.strip() for line in package_path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    except OSError:
        packages = []
    bindings, binding_source = discover_hypr_bindings()
    return {
        "generated_at": int(time.time()),
        "desktop_apps": discover_desktop_apps(),
        "hypr_bindings": bindings,
        "hypr_binding_source": binding_source,
        "global_shortcuts": run_json(["hyprctl", "globalshortcuts", "-j"], {}),
        "omarchy_commands": [
            {key: item.get(key) for key in ("route", "binary", "summary", "args", "aliases")}
            for item in commands if isinstance(item, dict)
        ],
        "omarchy_packages": packages,
        "plugins": plugins,
        "entries": plugin_entries,
    }


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{path}: {error}") from error


def validate_patterns(path: Path, field: str, patterns: Any) -> list[str]:
    if not isinstance(patterns, list) or not all(isinstance(item, str) and item for item in patterns):
        raise ValueError(f"{path}: {field} must be a non-empty string array")
    for pattern in patterns:
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as error:
            raise ValueError(f"{path}: invalid {field} regex {pattern!r}: {error}") from error
    return patterns


def validate_pack(path: Path, pack: Any) -> dict[str, Any]:
    if not isinstance(pack, dict) or pack.get("schema_version") != 1:
        raise ValueError(f"{path}: schema_version must be 1")
    if not isinstance(pack.get("id"), str) or not pack["id"]:
        raise ValueError(f"{path}: id is required")
    validate_patterns(path, "apps", pack.get("apps"))
    if not isinstance(pack.get("source"), dict) or not pack["source"].get("url"):
        raise ValueError(f"{path}: source.url is required")
    default_match = pack.get("default_match", {})
    if not isinstance(default_match, dict) or set(default_match) - MATCH_FIELDS:
        raise ValueError(f"{path}: default_match has unsupported fields")
    for field, patterns in default_match.items():
        validate_patterns(path, f"default_match.{field}", patterns)
    commands = pack.get("commands")
    if not isinstance(commands, list) or not commands:
        raise ValueError(f"{path}: commands must be a non-empty array")
    seen = set()
    for command in commands:
        if not isinstance(command, dict):
            raise ValueError(f"{path}: every command must be an object")
        command_id = command.get("id")
        if not isinstance(command_id, str) or not command_id or command_id in seen:
            raise ValueError(f"{path}: command ids must be non-empty and unique")
        seen.add(command_id)
        if not isinstance(command.get("shortcut"), str) or not command["shortcut"]:
            raise ValueError(f"{path}: {command_id} requires a shortcut")
        if not isinstance(command.get("description"), str) or not command["description"]:
            raise ValueError(f"{path}: {command_id} requires a description")
        if "native_id" in command and not isinstance(command["native_id"], (int, str)):
            raise ValueError(f"{path}: {command_id}.native_id must be an integer or string")
        match = command.get("match", {})
        if not isinstance(match, dict) or set(match) - MATCH_FIELDS:
            unknown = ", ".join(sorted(set(match) - MATCH_FIELDS))
            raise ValueError(f"{path}: {command_id} has unsupported match fields: {unknown}")
        for field, patterns in match.items():
            validate_patterns(path, f"{command_id}.{field}", patterns)
    return pack


def format_accelerator(value: str) -> str:
    parts = value.split("+")
    replacements = {
        "Control": "Ctrl", "ArrowLeft": "Left", "ArrowRight": "Right",
        "ArrowUp": "Up", "ArrowDown": "Down", "Escape": "Esc", "Equal": "=",
        "Minus": "-", "NumpadAdd": "+", "NumpadSubtract": "-",
    }
    formatted = []
    for part in parts:
        if part.startswith("Key") and len(part) == 4:
            part = part[3:]
        elif part.startswith("Digit") and len(part) == 6:
            part = part[5:]
        elif part.startswith("Numpad") and part[6:].isdigit():
            part = part[6:]
        formatted.append(replacements.get(part, part))
    return "+".join(formatted)


def preferred_accelerators(values: list[str]) -> str:
    conventional = [
        value for value in values
        if not value.startswith(("Browser", "App", "AltGr")) and "Numpad" not in value
    ]
    selected = conventional or values
    if any(value.startswith("Control+Digit") for value in selected):
        selected = [value for value in selected if not value.startswith("Alt+Digit")]
    selected = sorted(selected, key=lambda value: (value == "Alt+KeyE",))
    formatted = list(dict.fromkeys(format_accelerator(value) for value in selected))
    return " / ".join(formatted[:2])


def read_brave_accelerators(path: Path) -> dict[str, str]:
    """Shortcuts the user reassigned inside a Chromium browser, by command id.

    Only Brave publishes `brave.accelerators`; the key is simply absent in the
    other Chromium browsers, which leaves the pack's defaults in place.
    """
    if not path.exists():
        return {}
    preferences = read_json(path)
    raw = preferences.get("brave", {}).get("accelerators", {}) if isinstance(preferences, dict) else {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(command_id): preferred_accelerators(values)
        for command_id, values in raw.items()
        if isinstance(values, list) and values and all(isinstance(value, str) for value in values)
    }


# Where each Chromium browser keeps its default profile, and the window classes
# a customization found there may be applied to. A user running two of them must
# not have one browser's reassigned keys suggested inside the other.
CHROMIUM_BROWSERS = (
    ("BraveSoftware/Brave-Browser", ["^(brave|brave-browser(-(beta|dev|nightly))?|com\\.brave\\.Browser.*)$", "^brave-[^-]+-(default|profile[ _]?\\d+)$"]),
    ("chromium", ["^(chromium|chromium-browser|org\\.chromium\\.Chromium.*|ungoogled-chromium)$", "^chromium?-[^-]+-(default|profile[ _]?\\d+)$"]),
    ("google-chrome", ["^(chrome|google-chrome(-(beta|unstable))?|com\\.google\\.Chrome.*)$", "^chrome-[^-]+-(default|profile[ _]?\\d+)$"]),
    ("vivaldi", ["^(vivaldi|vivaldi-stable|vivaldi-snapshot|com\\.vivaldi\\.Vivaldi.*)$", "^vivaldi-[^-]+-(default|profile[ _]?\\d+)$"]),
    ("microsoft-edge", ["^(microsoft-edge(-(beta|dev))?|com\\.microsoft\\.Edge.*)$", "^microsoft-edge-[^-]+-(default|profile[ _]?\\d+)$"]),
)


def chromium_preference_paths() -> list[tuple[Path, list[str]]]:
    """Every installed Chromium browser's default profile preferences."""
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    flatpak = Path.home() / ".var/app"
    paths = []
    for directory, apps in CHROMIUM_BROWSERS:
        candidates = [config_home / directory / "Default/Preferences"]
        candidates += [
            path / "config" / directory / "Default/Preferences"
            for path in sorted(flatpak.glob("*")) if path.is_dir()
        ]
        for candidate in candidates:
            if candidate.is_file():
                paths.append((candidate, apps))
    return paths


def read_chromium_accelerators(paths: list[tuple[Path, list[str]]]) -> list[dict[str, Any]]:
    """Per-browser shortcut customizations, each scoped to that browser's windows."""
    sources = []
    for path, apps in paths:
        accelerators = read_brave_accelerators(path)
        if accelerators:
            sources.append({"apps": apps, "accelerators": accelerators, "source": str(path)})
    return sources


def resolve_native_shortcuts(pack: dict[str, Any], accelerators: dict[str, str]) -> dict[str, Any]:
    resolved = copy.deepcopy(pack)
    for command in resolved["commands"]:
        shortcut = accelerators.get(str(command.get("native_id", "")))
        if shortcut:
            command["shortcut"] = shortcut
    return resolved


ACCELERATOR_SOURCES = {"brave_preferences", "chromium_preferences"}
# A customization outranks the shipped default for the browser it was read from.
CUSTOMIZED_PRIORITY_BONUS = 5


def accelerator_variants(
    pack: dict[str, Any], accelerator_sources: list[dict[str, Any]],
) -> list[tuple[list[str], list[dict[str, Any]], int]]:
    """The pack as shipped, plus one narrower rule set per browser that reassigned keys.

    A family pack covers every Chromium browser, but a reassigned shortcut holds
    only in the browser whose preferences declared it, so those commands are
    emitted again, scoped to that browser's windows and ranked above the default.
    """
    variants = [(pack["apps"], pack["commands"], 0)]
    if pack.get("accelerator_source") not in ACCELERATOR_SOURCES:
        return variants
    for source in accelerator_sources:
        accelerators = source.get("accelerators") or {}
        customized = [
            {**command, "shortcut": accelerators[str(command["native_id"])]}
            for command in pack["commands"]
            if command.get("native_id") is not None
            and accelerators.get(str(command["native_id"]))
            and accelerators[str(command["native_id"])] != command["shortcut"]
        ]
        if customized:
            variants.append((source.get("apps") or pack["apps"], customized, CUSTOMIZED_PRIORITY_BONUS))
    return variants


def normalize_accelerator_sources(accelerators: Any) -> list[dict[str, Any]]:
    """Accept either one unscoped set of command overrides or several scoped ones."""
    if not accelerators:
        return []
    if isinstance(accelerators, dict):
        return [{"apps": None, "accelerators": accelerators}]
    return list(accelerators)


def compile_catalog(
    base_path: Path, pack_paths: list[Path],
    accelerators: dict[str, str] | list[dict[str, Any]] | None = None,
    system_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = read_json(base_path)
    if not isinstance(base, dict) or not isinstance(base.get("entries"), list):
        raise ValueError(f"{base_path}: base catalog requires an entries array")
    entries = []
    command_sets = []
    packs_by_id = {}
    for path in pack_paths:
        pack = validate_pack(path, read_json(path))
        packs_by_id[pack["id"]] = (path, pack)
    accelerator_sources = normalize_accelerator_sources(accelerators)
    for path, pack in packs_by_id.values():
        command_sets.append({
            "id": pack["id"], "apps": pack["apps"], "source": pack["source"],
            "commands": [
                {key: command[key] for key in ("id", "shortcut", "description", "native_id") if key in command}
                for command in pack["commands"]
            ],
        })
        for apps, commands, bonus in accelerator_variants(pack, accelerator_sources):
            for command in commands:
                if not command.get("match"):
                    continue
                entry = {
                    "apps": apps,
                    # A command's own match fields override the pack-wide defaults.
                    **pack.get("default_match", {}),
                    **command["match"],
                    "shortcut": command["shortcut"],
                    "description": command["description"],
                    "priority": int(command.get("priority", 100)) + bonus,
                    "command_id": f"{pack['id']}.{command['id']}",
                }
                entry.setdefault("buttons", ["left"])
                entries.append(entry)
    entries.sort(key=lambda entry: entry["priority"], reverse=True)
    discovered = system_index or {}
    system_entries = discovered.get("entries", []) if isinstance(discovered, dict) else []
    return {
        "version": 3,
        "generated_from": [str(path) for path in pack_paths],
        "command_sets": command_sets,
        "entries": sorted(entries + system_entries, key=lambda entry: int(entry.get("priority", 0)), reverse=True) + base["entries"],
        "system_index": {key: value for key, value in discovered.items() if key != "entries"},
    }


def main() -> int:
    data_dir = default_data_dir()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=data_dir / "base-catalog.json")
    parser.add_argument("--commands-dir", type=Path, action="append")
    parser.add_argument("--output", type=Path, default=data_dir / "catalog.json")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--no-system-index", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--chromium-preferences", type=Path, action="append",
        help="a Chromium browser Preferences file to read shortcut customizations from",
    )
    args = parser.parse_args()
    command_dirs = args.commands_dir or [data_dir / "commands", default_config_dir() / "commands"]
    pack_paths = [
        path for directory in command_dirs if directory.exists()
        for path in sorted(directory.glob("*.json"))
    ]
    try:
        preferences = (
            [(path, None) for path in args.chromium_preferences]
            if args.chromium_preferences else chromium_preference_paths()
        )
        catalog = compile_catalog(
            args.base, pack_paths, read_chromium_accelerators(preferences),
            {} if args.no_system_index else discover_system_index(),
        )
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=args.output.parent, prefix=".catalog-", delete=False,
    ) as stream:
        stream.write(payload)
        temporary_path = Path(stream.name)
    temporary_path.chmod(0o600)
    os.replace(temporary_path, args.output)
    if not args.quiet:
        index = catalog["system_index"]
        print(
            f"Compiled {len(catalog['command_sets'])} app packs, {len(catalog['entries'])} match rules, "
            f"{len(index.get('desktop_apps', []))} apps, {len(index.get('plugins', []))} plugins, "
            f"and {len(index.get('hypr_bindings', []))} live bindings."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
