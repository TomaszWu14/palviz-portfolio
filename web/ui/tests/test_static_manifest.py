"""Statyki z manifestem (audyt PERF-001).

Django 5.1+ ignoruje STATICFILES_STORAGE — prod serwował statyki bez hashy i kompresji.
Po przejściu na STORAGES każde {% static "…" %} do nieistniejącego pliku daje na produkcji
błąd 500 (manifest), więc pilnujemy, żeby wszystkie literały w szablonach istniały."""
import importlib
import re
from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

from palletweb.storage import GrooveStaticStorage

WEB = Path(__file__).resolve().parents[2]
STATIC_TAG = re.compile(r"""{%\s*static\s+['"]([^'"]+)['"]""")


class StaticManifestTests(SimpleTestCase):
    def test_prod_settings_use_hashed_compressed_storage(self):
        prod = importlib.import_module("palletweb.settings")
        self.assertEqual(prod.STORAGES["staticfiles"]["BACKEND"], "palletweb.storage.GrooveStaticStorage")
        self.assertFalse(hasattr(prod, "STATICFILES_STORAGE"))

    def test_sourcemaps_skipped_css_urls_kept(self):
        rules = [r[0] if isinstance(r, tuple) else r for _, group in GrooveStaticStorage.patterns for r in group]
        self.assertFalse([r for r in rules if "sourceMappingURL" in r])
        self.assertTrue(any(r.startswith("(?P<matched>url") for r in rules))
        self.assertTrue(any("@import" in r for r in rules))

    def test_every_static_literal_in_templates_exists(self):
        # ui/vendor/ jest poza repo (gitignore) — trafia do obrazu przez fetch_vendor.sh przy buildzie,
        # więc dla niego źródłem prawdy jest lista pobieranych plików, nie dysk.
        vendored = set(re.findall(r'"([\w./-]+)"\s*$', (WEB / "scripts" / "fetch_vendor.sh").read_text(encoding="utf-8"), re.M))
        missing = []
        for tpl in WEB.glob("*/templates/**/*.html"):
            for ref in STATIC_TAG.findall(tpl.read_text(encoding="utf-8")):
                if "{{" in ref:
                    continue
                if ref.startswith("ui/vendor/"):
                    ok = ref.removeprefix("ui/vendor/") in vendored
                else:
                    ok = bool(finders.find(ref))
                if not ok:
                    missing.append(f"{tpl.relative_to(WEB)} → {ref}")
        self.assertEqual(missing, [], "Brakujące pliki statyczne (na prod = błąd 500):")


class PlotlyLoadingTests(SimpleTestCase):
    """UX-001 / CODE-006: plotly (4,8 MB) przez {% static %} (hash + długi cache), a na
    kalkulatorze z `defer` — strona nie czeka na parsowanie biblioteki przed pierwszym renderem."""

    def test_no_hardcoded_static_paths_to_plotly(self):
        offenders = [str(t.relative_to(WEB)) for t in WEB.glob("*/templates/**/*.html")
                     if 'src="/static/ui/js/plotly' in t.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [])

    def test_calc_defers_plotly(self):
        text = (WEB / "ui/templates/ui/planner/calc_index.html").read_text(encoding="utf-8")
        self.assertRegex(text, r"<script defer src=\"{% static 'ui/js/plotly.min.js' %}\">")
