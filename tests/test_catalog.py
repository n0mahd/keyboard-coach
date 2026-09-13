#!/usr/bin/env python3

import importlib.util
import os
from pathlib import Path
import json
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("coach", PLUGIN_ROOT / "scripts/coach.py")
coach = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(coach)


class CatalogSuggestionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_catalog = os.environ.get("KEYBOARD_COACH_CATALOG")
        os.environ["KEYBOARD_COACH_CATALOG"] = str(PLUGIN_ROOT / "assets/catalog.json")

    @classmethod
    def tearDownClass(cls):
        if cls.previous_catalog is None:
            os.environ.pop("KEYBOARD_COACH_CATALOG", None)
        else:
            os.environ["KEYBOARD_COACH_CATALOG"] = cls.previous_catalog

    def test_brave_new_tab_without_accessible_target_is_local(self):
        context = {
            "app": "brave-browser",
            "title": "New Tab - Brave",
            "target": {},
            "omarchy": {},
        }

        suggestion, source = coach.deterministic_suggestion(context, "left")

        self.assertEqual(source, "catalog")
        self.assertEqual(suggestion, "Ctrl+T — open a new tab.")

    def test_brave_page_tab_still_uses_tab_switching_shortcut(self):
        context = {
            "app": "brave-browser",
            "title": "New Tab - Brave",
            "target": {"role": "page tab", "name": "New Tab", "description": "", "actions": []},
            "omarchy": {},
        }

        suggestion, source = coach.deterministic_suggestion(context, "left")

        self.assertEqual(source, "catalog")
        self.assertEqual(suggestion, "Ctrl+Tab / Ctrl+Shift+Tab — switch to the next or previous tab.")

    def test_brave_tab_position_gets_direct_shortcut(self):
        context = {
            "app": "brave-browser",
            "title": "Example - Brave",
            "target": {
                "role": "page tab", "name": "Example", "description": "", "actions": [],
                "position": 3, "set_size": 5, "is_last": False,
            },
            "omarchy": {},
        }

        suggestion, source = coach.deterministic_suggestion(context, "left")

        self.assertEqual(source, "catalog")
        self.assertEqual(suggestion, "Ctrl+3 — switch directly to tab 3.")

    def test_brave_settings_beats_incidental_escape_binding(self):
        context = {
            "app": "brave-browser",
            "title": "Example - Brave",
            "target": {
                "role": "menu item", "name": "Settings", "description": "",
                "actions": [{"name": "click", "key_binding": "Escape"}],
            },
            "omarchy": {},
        }

        suggestion, source = coach.deterministic_suggestion(context, "left")

        self.assertEqual(source, "catalog")
        self.assertEqual(suggestion, "Alt+F, then S — open Brave Settings from the main menu.")

    def test_brave_more_options_uses_main_menu_accelerator(self):
        context = {
            "app": "brave-browser",
            "title": "Example - Brave",
            "target": {
                "role": "push button", "name": "More options", "description": "",
                "actions": [{"name": "click", "description": "", "key_binding": "Escape"}],
            },
            "omarchy": {},
        }

        suggestion, source = coach.deterministic_suggestion(context, "left")

        self.assertEqual(source, "catalog")
        self.assertEqual(suggestion, "Alt+F / Alt+E — open the Brave main menu.")

    def test_escape_is_not_used_to_activate_an_opening_control(self):
        target = {
            "role": "push button", "name": "More options", "description": "",
            "actions": [{"name": "click", "description": "", "key_binding": "Escape"}],
        }

        self.assertIsNone(coach.exposed_shortcut(target))

    def test_browser_menu_wording_variants_share_one_semantic_intent(self):
        for label in ("More options", "More actions", "Application menu", "Customize and control Chromium"):
            with self.subTest(label=label):
                context = {
                    "app": "chromium", "title": "Example",
                    "target": {"role": "button", "name": label, "description": "", "actions": []},
                    "omarchy": {},
                }
                self.assertIn("open-application-menu", coach.semantic_intents(context))

    def test_overflow_button_is_not_promoted_to_browser_menu_in_other_apps(self):
        context = {
            "app": "signal", "title": "Signal",
            "target": {"role": "button", "name": "More actions", "description": "", "actions": []},
            "omarchy": {},
        }

        self.assertEqual(coach.semantic_intents(context), {"open-menu"})

    def test_signal_chat_uses_navigation_shortcuts(self):
        context = {
            "app": "signal",
            "title": "Signal",
            "target": {"role": "list item", "name": "Ada", "description": "", "actions": []},
            "omarchy": {},
        }

        suggestion, source = coach.deterministic_suggestion(context, "left")

        self.assertEqual(source, "catalog")
        self.assertEqual(suggestion, "Alt+Up / Alt+Down — switch to the previous or next chat.")

    def test_known_commands_are_scoped_to_the_active_app(self):
        catalog = coach.load_catalog()

        commands = coach.known_app_commands({"app": "signal"}, catalog)

        self.assertIn("next-chat", {command["id"] for command in commands})
        self.assertNotIn("new-tab", {command["id"] for command in commands})

    def test_installed_panel_plugin_is_discovered_without_a_name_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = Path(directory) / "example.settings"
            plugin.mkdir()
            (plugin / "manifest.json").write_text(json.dumps({
                "id": "example.settings", "name": "Example Settings",
                "kinds": ["bar-widget"], "entryPoints": {"barWidget": "Widget.qml"},
                "barWidget": {"displayName": "Settings"},
            }), encoding="utf-8")
            (plugin / "Widget.qml").write_text("""
BarWidget {
  property bool opened: false
  function open() {}
  function close() {}
}
""", encoding="utf-8")

            registry = coach.panel_widget_registry([Path(directory)])

        self.assertEqual(registry, {"example.settings": "Settings"})

    def test_panel_position_skips_non_panels_and_supports_installed_widgets(self):
        widgets = [
            {"id": "omarchy.audio", "section": "right", "x": 0, "y": 0, "width": 20, "height": 20, "visible": True},
            {"id": "omarchy.media", "section": "right", "x": 20, "y": 0, "width": 20, "height": 20, "visible": True},
            {"id": "example.settings", "section": "right", "x": 40, "y": 0, "width": 20, "height": 20, "visible": True},
        ]

        context = coach.bar_widget_context(
            widgets, 45, 10, {"omarchy.audio": "Audio", "example.settings": "Settings"},
        )

        self.assertEqual(context["panel_index"], 2)
        self.assertEqual(context["widget_name"], "Settings")

    def test_panel_position_uses_shell_layout_not_geometry_creation_order(self):
        widgets = [
            {"id": "example.settings", "section": "right", "x": 20, "y": 0, "width": 20, "height": 20, "visible": True},
            {"id": "omarchy.audio", "section": "right", "x": 0, "y": 0, "width": 20, "height": 20, "visible": True},
        ]

        context = coach.bar_widget_context(
            widgets, 25, 10, {"omarchy.audio": "Audio", "example.settings": "Settings"},
            ["omarchy.audio", "example.settings"],
        )

        self.assertEqual(context["panel_index"], 2)

    def test_left_side_installed_widget_uses_launcher_without_luna(self):
        context = {
            "app": "unknown", "title": "unknown", "target": {},
            "omarchy": {"surface": "bar", "section": "left", "widget_name": "OmaSettings"},
        }
        apps = [{"name": "OmaSettings", "aliases": {"omasettings"}}]
        original = coach.desktop_app_registry
        coach.desktop_app_registry = lambda: apps
        try:
            suggestion, source = coach.deterministic_suggestion(context, "left", {"entries": []})
        finally:
            coach.desktop_app_registry = original

        self.assertEqual(source, "system")
        self.assertEqual(suggestion, "Super+Space, type OmaSettings, Enter — open OmaSettings.")

    def test_newly_installed_app_uses_effective_system_binding(self):
        before = {"app": "omarchy-menu", "title": "Apps", "omarchy": {"namespace": "omarchy-menu"}}
        after = {"app": "acme-notes", "title": "Acme Notes", "omarchy": {}}
        apps = [{
            "name": "Acme Notes", "exec": "acme-notes", "executable": "acme-notes",
            "aliases": {"acme notes", "acme-notes"},
        }]
        bindings = [{"shortcut": "Super+N", "description": "Acme Notes", "command": "uwsm app -- acme-notes"}]

        suggestion = coach.transition_binding_suggestion(before, after, bindings, apps)

        self.assertEqual(suggestion, "Super+N — open Acme Notes.")

    def test_installed_plugin_widget_uses_its_effective_direct_binding(self):
        context = {
            "app": "foot", "title": "Terminal", "target": {},
            "omarchy": {"widget": "example.network", "namespace": "omarchy-bar", "surface": "bar"},
        }
        catalog = {"system_index": {"hypr_bindings": [{
            "shortcut": "Super+Ctrl+W", "description": "Network",
            "command": "omarchy-shell shell toggle example.network",
        }]}}

        suggestion = coach.indexed_system_suggestion(context, catalog)

        self.assertEqual(suggestion, "Super+Ctrl+W — network.")

    def test_coverage_report_counts_discovered_sources(self):
        catalog = {
            "version": 3, "command_sets": [{"id": "example"}], "entries": [{"shortcut": "X"}],
            "system_index": {
                "desktop_apps": [{}, {}], "hypr_bindings": [{}], "omarchy_commands": [{}, {}, {}],
                "omarchy_packages": [{}], "plugins": [
                    {"declared_shortcuts": [{}], "source_shortcuts": []},
                    {"declared_shortcuts": [], "source_shortcuts": [{}]},
                ],
            },
        }

        report = coach.coverage_report(catalog)

        self.assertEqual(report["installed_apps"], 2)
        self.assertEqual(report["plugins_with_declared_shortcuts"], 1)
        self.assertEqual(report["plugins_with_source_shortcuts"], 1)


if __name__ == "__main__":
    unittest.main()
