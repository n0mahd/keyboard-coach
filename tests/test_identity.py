import json
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class PluginIdentityTest(unittest.TestCase):
    def test_all_public_entry_points_use_keyboard_coach(self):
        plugin = json.loads((PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text())
        shell = json.loads((PLUGIN_ROOT / "manifest.json").read_text())
        service = (PLUGIN_ROOT / "systemd/keyboard-coach.service").read_text()
        bindings = (PLUGIN_ROOT / "scripts/hypr-bindings.lua").read_text()
        skill = (PLUGIN_ROOT / "skills/keyboard-coach/SKILL.md").read_text()

        self.assertEqual(PLUGIN_ROOT.name, "keyboard-coach")
        self.assertEqual(plugin["name"], "keyboard-coach")
        self.assertEqual(plugin["interface"]["displayName"], "Keyboard Coach")
        self.assertEqual(shell["id"], "io.github.n0mahd.keyboard-coach")
        self.assertEqual(shell["name"], "Keyboard Coach")
        self.assertIn("%h/.local/bin/keyboard-coach serve", service)
        self.assertIn("keyboard-coach-emit", bindings)
        self.assertIn("name: keyboard-coach", skill)

    def test_legacy_identity_is_limited_to_migration_compatibility(self):
        public_files = [
            PLUGIN_ROOT / ".codex-plugin/plugin.json",
            PLUGIN_ROOT / "manifest.json",
            PLUGIN_ROOT / "Banner.qml",
            PLUGIN_ROOT / "scripts/emit.sh",
            PLUGIN_ROOT / "scripts/hypr-bindings.lua",
            PLUGIN_ROOT / "scripts/ingest_catalog.py",
            PLUGIN_ROOT / "systemd/keyboard-coach.service",
            PLUGIN_ROOT / "skills/keyboard-coach/SKILL.md",
        ]
        for path in public_files:
            with self.subTest(path=path):
                self.assertNotIn("mouse-keyboard-coach", path.read_text())


if __name__ == "__main__":
    unittest.main()
