#!/usr/bin/env python3
"""Build tests/corpus/catalog.json, the catalog every implementation's corpus tests read.

It is compiled from the shipped base catalog and command packs with no user Brave
preferences, plus a fixed system index derived from fixtures, harvested shortcuts, and shortcuts
captured from running applications merged the way the daemon merges them. Run after changing
assets or fixtures; tests/test_corpus.py fails while the committed file is stale.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests/corpus"
FIXTURES = ROOT / "tests/fixtures"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _ingest():
    return _load("ingest_catalog", ROOT / "scripts/ingest_catalog.py")


def build() -> dict:
    ingest = _ingest()
    replay = json.loads((FIXTURES / "replay-bindings.json").read_text(encoding="utf-8"))
    system_index = {
        "hypr_bindings": ingest.bindings_from_replay(replay, None),
        "hypr_binding_source": "lua-replay",
        "desktop_apps": [
            {"id": "omasettings", "name": "OmaSettings", "exec": "omasettings", "executable": "omasettings", "startup_wm_class": ""},
            {"id": "org.gnome.Nautilus", "name": "Files", "exec": "nautilus --new-window", "executable": "nautilus", "startup_wm_class": ""},
        ],
        "plugins": [
            {"id": "omarchy.audio", "name": "Audio", "display_name": "Audio", "bar_panel": True},
            {"id": "example.settings", "name": "Example Settings", "display_name": "Settings", "bar_panel": True},
        ],
        "entries": [],
    }
    packs = sorted((ROOT / "assets/commands").glob("*.json"))
    catalog = ingest.compile_catalog(ROOT / "assets/base-catalog.json", packs, {}, system_index)
    catalog["generated_from"] = [str(path.relative_to(ROOT)) for path in packs]
    catalog["harvested"] = json.loads((FIXTURES / "shortcuts.json").read_text(encoding="utf-8"))
    coach = _load("coach", ROOT / "scripts/coach.py")
    coach.merge_observed(catalog["harvested"], json.loads((FIXTURES / "observed.json").read_text(encoding="utf-8")))
    return catalog


def render(catalog: dict) -> str:
    return json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    (CORPUS / "catalog.json").write_text(render(build()), encoding="utf-8")
    print("wrote tests/corpus/catalog.json", file=sys.stderr)
