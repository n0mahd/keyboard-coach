#!/usr/bin/env python3

import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/corpus"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


coach = _load("coach", ROOT / "scripts/coach.py")
build_catalog = _load("build_catalog", CORPUS / "build_catalog.py")


class CorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((CORPUS / "catalog.json").read_text(encoding="utf-8"))
        cls.cases = json.loads((CORPUS / "cases.json").read_text(encoding="utf-8"))["cases"]

    def test_committed_catalog_matches_sources(self):
        fresh = build_catalog.render(build_catalog.build())
        self.assertEqual(
            (CORPUS / "catalog.json").read_text(encoding="utf-8"), fresh,
            "tests/corpus/catalog.json is stale; run tests/corpus/build_catalog.py",
        )

    def test_case_names_are_unique(self):
        names = [case["name"] for case in self.cases]
        self.assertEqual(len(names), len(set(names)))

    def test_cases(self):
        for case in self.cases:
            with self.subTest(case["name"]):
                suggestion, source = coach.deterministic_suggestion(case["context"], case["button"], self.catalog)
                self.assertEqual(
                    {"suggestion": suggestion, "source": source}, case["expect"],
                )


if __name__ == "__main__":
    sys.exit(unittest.main())
