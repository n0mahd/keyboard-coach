#!/usr/bin/env python3

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ingest_catalog", PLUGIN_ROOT / "scripts/ingest_catalog.py")
ingest = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(ingest)


class CatalogIngestionTest(unittest.TestCase):
    def test_compiles_sourced_app_commands_and_match_rules(self):
        packs = sorted((PLUGIN_ROOT / "assets/commands").glob("*.json"))

        catalog = ingest.compile_catalog(PLUGIN_ROOT / "assets/base-catalog.json", packs)

        self.assertEqual(catalog["version"], 3)
        self.assertEqual(
            {item["id"] for item in catalog["command_sets"]},
            {"brave", "brave-installed", "signal-desktop"},
        )
        self.assertTrue(any(entry.get("command_id") == "brave.settings" for entry in catalog["entries"]))
        self.assertTrue(any(entry.get("command_id") == "signal-desktop.switch-chat" for entry in catalog["entries"]))

    def test_installed_brave_accelerator_overrides_pack_default(self):
        packs = sorted((PLUGIN_ROOT / "assets/commands").glob("*.json"))

        catalog = ingest.compile_catalog(
            PLUGIN_ROOT / "assets/base-catalog.json", packs, {"34020": "Super+3"},
        )

        command_set = next(item for item in catalog["command_sets"] if item["id"] == "brave-installed")
        shortcut = next(item["shortcut"] for item in command_set["commands"] if item["id"] == "select-tab-3")
        self.assertEqual(shortcut, "Super+3")

    def test_discovers_installed_desktop_apps_without_an_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.desktop"
            path.write_text("""[Desktop Entry]
Type=Application
Name=Example Notes
Exec=example-notes %U
StartupWMClass=example-notes
""", encoding="utf-8")

            apps = ingest.discover_desktop_apps([Path(directory)])

        self.assertEqual(apps[0]["name"], "Example Notes")
        self.assertEqual(apps[0]["executable"], "example-notes")

    def test_plugin_shortcut_contract_becomes_a_deterministic_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = Path(directory) / "example.plugin"
            plugin.mkdir()
            (plugin / "manifest.json").write_text(json.dumps({
                "id": "example.plugin", "name": "Example", "kinds": ["panel"],
                "entryPoints": {"panel": "Panel.qml"},
                "keyboardCoach": {"shortcuts": "shortcuts.json"},
            }), encoding="utf-8")
            (plugin / "Panel.qml").write_text("Item {}", encoding="utf-8")
            (plugin / "shortcuts.json").write_text(json.dumps({
                "schema_version": 1,
                "shortcuts": [{
                    "intent": "refresh", "shortcut": "Ctrl+R", "description": "refresh the panel.",
                    "match": {"names": ["refresh"]},
                }],
            }), encoding="utf-8")

            plugins, entries = ingest.discover_plugins([Path(directory)])

        self.assertEqual(plugins[0]["declared_shortcuts"][0]["shortcut"], "Ctrl+R")
        self.assertEqual(entries[0]["namespaces"], ["^example\\.plugin$"])
        self.assertEqual(entries[0]["intents"], ["^refresh$"])

    def test_qml_shortcuts_are_inventory_only_until_semantically_mapped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Panel.qml"
            path.write_text('property var shortcuts: [{ keys: "Ctrl+P", action: "Play" }]', encoding="utf-8")

            records = ingest._qml_shortcut_inventory(Path(directory))

        self.assertEqual(records[0]["description"], "Play")
        self.assertEqual(records[0]["confidence"], "source")

    def test_documented_plugin_shortcuts_are_inventory_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "README.md"
            path.write_text("| Shortcut | Action |\n|---|---|\n| `Ctrl+R` | Refresh data |\n", encoding="utf-8")

            records = ingest._markdown_shortcut_inventory(Path(directory))

        self.assertEqual(records[0]["shortcut"], "Ctrl+R")
        self.assertEqual(records[0]["confidence"], "documented-unmapped")


if __name__ == "__main__":
    unittest.main()
