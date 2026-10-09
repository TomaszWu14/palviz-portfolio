"""Django {# #} comments are single-line only — a multi-line one renders as page text
(and, sitting in <head>, gets hoisted by the browser to the top of the body).
Regression: scan every template for an opening {# without a closing #} on the same line."""
import pathlib
import re

from django.test import SimpleTestCase

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "templates"


class TemplateCommentTests(SimpleTestCase):
    def test_no_multiline_hash_comments(self):
        bad = []
        for f in TEMPLATES.rglob("*.html"):
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                for m in re.finditer(r"\{#", line):
                    if "#}" not in line[m.start():]:
                        bad.append(f"{f.relative_to(TEMPLATES)}:{i}")
        self.assertEqual(bad, [], "Wieloliniowy komentarz {# #} renderuje się jako tekst strony")
