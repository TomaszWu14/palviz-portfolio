"""Permanent regression guard for the URL-e-per-app refactor (phase 05).

Compares a committed golden fixture (name -> path pattern, captured from the
resolver BEFORE the leaf urls.py extraction) against the LIVE resolver state.
Any dropped/renamed URL name or changed path string fails this test loudly.
Stays in the suite as the guard for phases 6-8 (D-03)."""
import json
import os

from django.test import SimpleTestCase
from django.urls import get_resolver

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "url_snapshot.json")


def _walk(patterns, prefix=""):
    seen = {}
    for p in patterns:
        if hasattr(p, "url_patterns"):
            seen.update(_walk(p.url_patterns, prefix + str(p.pattern)))
        elif p.name:
            seen[f"ui:{p.name}"] = prefix + str(p.pattern)
    return seen


class UrlSnapshotTests(SimpleTestCase):
    def test_url_names_and_patterns_unchanged(self):
        with open(FIXTURE_PATH, encoding="utf-8") as f:
            expected = json.load(f)
        actual = _walk(get_resolver().url_patterns)
        self.assertEqual(actual, expected)
