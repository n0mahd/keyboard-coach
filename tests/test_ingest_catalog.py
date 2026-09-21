#!/usr/bin/env python3

import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ingest_catalog", ROOT / "scripts/ingest_catalog.py")
ingest = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(ingest)


def write_pack(directory: Path, name: str, pack: dict) -> Path:
    path = directory / name
    path.write_text(json.dumps(pack), encoding="utf-8")
    return path


class CatalogCompileTest(unittest.TestCase):
    def test_compiles_shipped_packs(self):
        packs = sorted((ROOT / "assets/commands").glob("*.json"))

        catalog = ingest.compile_catalog(ROOT / "assets/base-catalog.json", packs)

        self.assertEqual(
            {item["id"] for item in catalog["command_sets"]},
            {"brave", "chromium", "chromium-installed", "firefox", "signal-desktop"},
        )
        self.assertTrue(any(entry.get("command_id") == "chromium.settings" for entry in catalog["entries"]))

    def test_browser_accelerator_customization_is_scoped_to_that_browser(self):
        packs = sorted((ROOT / "assets/commands").glob("*.json"))

        catalog = ingest.compile_catalog(ROOT / "assets/base-catalog.json", packs, [
            {"apps": ["^brave-browser$"], "accelerators": {"34020": "Super+3"}},
        ])

        select_tab_3 = [
            entry for entry in catalog["entries"] if entry.get("command_id") == "chromium-installed.select-tab-3"
        ]
        customized = next(entry for entry in select_tab_3 if entry["shortcut"] == "Super+3")
        shipped = next(entry for entry in select_tab_3 if entry["shortcut"] == "Ctrl+3")
        # Only Brave windows, and ahead of the shipped default for them.
        self.assertEqual(customized["apps"], ["^brave-browser$"])
        self.assertGreater(customized["priority"], shipped["priority"])
        self.assertLess(catalog["entries"].index(customized), catalog["entries"].index(shipped))

    def test_unchanged_accelerators_add_no_rules(self):
        packs = sorted((ROOT / "assets/commands").glob("*.json"))

        catalog = ingest.compile_catalog(ROOT / "assets/base-catalog.json", packs, [
            {"apps": ["^brave-browser$"], "accelerators": {"34020": "Ctrl+3"}},
        ])

        self.assertEqual(
            len([entry for entry in catalog["entries"] if entry.get("command_id") == "chromium-installed.select-tab-3"]), 1,
        )

    def test_default_match_applies_unless_a_command_overrides_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_pack(Path(directory), "pack.json", {
                "schema_version": 1, "id": "example", "apps": ["^example$"], "source": {"url": "https://example.com"},
                "default_match": {"target_in_document": ["^false$"]},
                "commands": [
                    {"id": "a", "shortcut": "Ctrl+A", "description": "a.", "match": {"names": ["^a$"]}},
                    {"id": "b", "shortcut": "Ctrl+B", "description": "b.",
                     "match": {"names": ["^b$"], "target_in_document": ["^true$"]}},
                ],
            })

            catalog = ingest.compile_catalog(ROOT / "assets/base-catalog.json", [path])

        entries = {entry.get("command_id"): entry for entry in catalog["entries"]}
        self.assertEqual(entries["example.a"]["target_in_document"], ["^false$"])
        self.assertEqual(entries["example.b"]["target_in_document"], ["^true$"])

    def test_invalid_default_match_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_pack(Path(directory), "pack.json", {
                "schema_version": 1, "id": "example", "apps": ["^example$"], "source": {"url": "https://example.com"},
                "default_match": {"colour": ["red"]},
                "commands": [{"id": "a", "shortcut": "Ctrl+A", "description": "a."}],
            })

            with self.assertRaisesRegex(ValueError, "default_match"):
                ingest.compile_catalog(ROOT / "assets/base-catalog.json", [path])


class HyprBindingTest(unittest.TestCase):
    def test_lua_key_strings_are_formatted(self):
        for keys, expected in (
            ("SUPER + CTRL + code:10", "Super+Ctrl+1"), ("SUPER + code:19", "Super+0"),
            ("SUPER + ALT + SPACE", "Super+Alt+Space"), ("SHIFT + SUPER + a", "Super+Shift+A"),
            ("SUPER + CTRL + comma", "Super+Ctrl+,"), ("SUPER + SHIFT + TAB", "Super+Shift+Tab"),
            ("SUPER + CTRL", ""),
        ):
            with self.subTest(keys=keys):
                self.assertEqual(ingest.format_hypr_keys(keys), expected)

    def test_replay_records_skip_mouse_bindings_and_mark_liveness(self):
        payload = {"bindings": [
            {"keys": "SUPER + CTRL + A", "description": "Audio", "command": "omarchy-shell shell toggle omarchy.audio"},
            {"keys": "SUPER + mouse:272", "description": "Move window", "flags": {"mouse": True}},
            {"keys": "mouse:272", "command": "keyboard-coach-emit click left", "flags": {"click": True}},
            {"keys": "SUPER + Q", "description": "Removed since reload", "command": "x"},
        ]}

        records = ingest.bindings_from_replay(payload, [{"description": "Audio"}])

        self.assertEqual([record["shortcut"] for record in records], ["Super+Ctrl+A", "Super+Q"])
        self.assertEqual([record["live"] for record in records], [True, False])

    @unittest.skipUnless(shutil.which("lua5.5") or shutil.which("lua"), "Lua interpreter not installed")
    def test_config_replay_recovers_commands_and_refuses_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / "probe"
            previous = os.environ.get("KEYBOARD_COACH_REPLAY_PROBE")
            os.environ["KEYBOARD_COACH_REPLAY_PROBE"] = str(probe)
            try:
                payload = ingest.replay_hypr_config(ROOT / "tests/fixtures/hypr/hyprland.lua")
            finally:
                if previous is None:
                    os.environ.pop("KEYBOARD_COACH_REPLAY_PROBE", None)
                else:
                    os.environ["KEYBOARD_COACH_REPLAY_PROBE"] = previous
            self.assertFalse(probe.exists())

        self.assertEqual(payload["errors"], [])
        by_keys = {item["keys"]: item for item in payload["bindings"]}
        self.assertEqual(by_keys["SUPER + CTRL + A"]["command"], "omarchy-shell shell toggle omarchy.audio")
        self.assertEqual(by_keys["SUPER + CTRL + code:12"]["command"], "omarchy-shell -q shell togglePanelAt right 3")
        self.assertEqual(by_keys["SUPER + W"]["dispatcher"], "window.close")
        self.assertEqual(by_keys["SUPER + CTRL + Z"]["kind"], "lua-function")
        self.assertTrue(by_keys["SUPER + SPACE"]["source"].endswith("hyprland.lua:19"))
        self.assertNotIn("SUPER + SHIFT + M", by_keys)
        self.assertNotIn("SUPER + X", by_keys)

    def test_hyprctl_fallback_does_not_treat_lua_refs_as_commands(self):
        original = ingest.run_json, ingest.replay_hypr_config
        ingest.run_json = lambda argv, default: [
            {"modmask": 64, "key": "SPACE", "description": "Omarchy menu", "dispatcher": "__lua", "arg": "47"},
        ]
        ingest.replay_hypr_config = lambda entry=None: None
        try:
            records, source = ingest.discover_hypr_bindings()
        finally:
            ingest.run_json, ingest.replay_hypr_config = original

        self.assertEqual(source, "hyprctl")
        self.assertEqual((records[0]["shortcut"], records[0]["command"]), ("Super+Space", ""))


class DiscoveryTest(unittest.TestCase):
    def test_discovers_installed_desktop_apps_without_an_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "example.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Example Notes\nExec=example-notes %U\n", encoding="utf-8",
            )

            apps = ingest.discover_desktop_apps([Path(directory)])

        self.assertEqual((apps[0]["name"], apps[0]["executable"]), ("Example Notes", "example-notes"))

    def test_bar_panel_widgets_are_detected_from_their_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            for plugin_id, qml in (
                ("example.settings", "BarWidget {\n  property bool opened: false\n  function open() {}\n  function close() {}\n}\n"),
                ("example.clock", "BarWidget {\n  property string time\n}\n"),
            ):
                plugin = Path(directory) / plugin_id
                plugin.mkdir()
                (plugin / "manifest.json").write_text(json.dumps({
                    "id": plugin_id, "name": plugin_id, "kinds": ["bar-widget"],
                    "entryPoints": {"barWidget": "Widget.qml"}, "barWidget": {"displayName": plugin_id.split(".")[1].title()},
                }), encoding="utf-8")
                (plugin / "Widget.qml").write_text(qml, encoding="utf-8")

            plugins, _ = ingest.discover_plugins([Path(directory)])

        by_id = {plugin["id"]: plugin for plugin in plugins}
        self.assertTrue(by_id["example.settings"]["bar_panel"])
        self.assertEqual(by_id["example.settings"]["display_name"], "Settings")
        self.assertFalse(by_id["example.clock"]["bar_panel"])

    def test_plugin_shortcut_contract_becomes_a_deterministic_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = Path(directory) / "example.plugin"
            plugin.mkdir()
            (plugin / "manifest.json").write_text(json.dumps({
                "id": "example.plugin", "name": "Example", "kinds": ["panel"],
                "entryPoints": {"panel": "Panel.qml"}, "keyboardCoach": {"shortcuts": "shortcuts.json"},
            }), encoding="utf-8")
            (plugin / "shortcuts.json").write_text(json.dumps({
                "schema_version": 1,
                "shortcuts": [{"intent": "refresh", "shortcut": "Ctrl+R", "description": "refresh the panel.",
                               "match": {"names": ["refresh"]}}],
            }), encoding="utf-8")

            plugins, entries = ingest.discover_plugins([Path(directory)])

        self.assertEqual(plugins[0]["declared_shortcuts"][0]["shortcut"], "Ctrl+R")
        self.assertEqual(entries[0]["namespaces"], ["^example\\.plugin$"])
        self.assertEqual(entries[0]["intents"], ["^refresh$"])

    def test_qml_and_markdown_shortcuts_are_inventory_only(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "Panel.qml").write_text('property var shortcuts: [{ keys: "Ctrl+P", action: "Play" }]', encoding="utf-8")
            (Path(directory) / "README.md").write_text("| Shortcut | Action |\n|---|---|\n| `Ctrl+R` | Refresh |\n", encoding="utf-8")

            qml = ingest._qml_shortcut_inventory(Path(directory))
            documented = ingest._markdown_shortcut_inventory(Path(directory))

        self.assertEqual(qml[0]["confidence"], "source")
        self.assertEqual(documented[0]["confidence"], "documented-unmapped")


if __name__ == "__main__":
    unittest.main()
