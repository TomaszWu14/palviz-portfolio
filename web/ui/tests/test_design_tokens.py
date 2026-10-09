"""Strażnik design systemu (etap 3 audytu UX, krok 1): tokeny, kontrast AA, czcionka.

Liczy kontrast WCAG z wartości w tokens.css — zmiana koloru, która zjedzie poniżej
4,5:1, wywala test zamiast wrócić na produkcję jako szary tekst na szarym tle.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

STATIC = Path(__file__).resolve().parents[1] / "static" / "ui"
CSS = STATIC / "css"
TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def _lum(hexcol):
    h = hexcol.lstrip("#")
    rgb = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    rgb = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _block(css, selector):
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", m.group(1)))


class DesignTokensTests(SimpleTestCase):
    css = (CSS / "tokens.css").read_text(encoding="utf-8")
    dark = _block(css, ":root")
    light = {**dark, **_block(css, ':root[data-theme="light"]')}

    # (tekst, tło) — pary, które realnie występują w UI.
    PAIRS = [
        ("--fg", "--bg"), ("--fg", "--surface"), ("--fg-muted", "--surface"),
        ("--fg-subtle", "--bg"), ("--fg-subtle", "--surface"), ("--fg-subtle", "--surface-2"),
        ("--primary-fg", "--primary"), ("--primary-fg", "--primary-hover"),
        ("--link", "--bg"), ("--link", "--surface"), ("--link", "--primary-soft"),
        ("--ok-fg", "--ok-bg"), ("--warn-fg", "--warn-bg"),
        ("--danger-fg", "--danger-bg"), ("--info-fg", "--info-bg"),
        ("--neutral-fg", "--neutral-bg"), ("--hu-control-fg", "--primary-soft"),
        ("--wh-acme-fg", "--wh-acme-bg"), ("--wh-dlt-fg", "--wh-dlt-bg"),
        ("--wh-other-fg", "--wh-other-bg"), ("--violet-fg", "--violet-bg"),
    ]
    SOLIDS = ["--primary", "--ok-solid", "--warn-solid", "--danger-solid", "--info-solid"]

    def test_text_pairs_meet_wcag_aa_in_both_themes(self):
        for name, theme in (("ciemny", self.dark), ("jasny", self.light)):
            for fg, bg in self.PAIRS:
                with self.subTest(theme=name, fg=fg, bg=bg):
                    self.assertGreaterEqual(contrast(theme[fg], theme[bg]), 4.5)

    def test_white_on_solid_backgrounds_meets_aa(self):
        for solid in self.SOLIDS:
            with self.subTest(solid=solid):
                self.assertGreaterEqual(contrast("#ffffff", self.dark[solid]), 4.5)

    def test_legacy_light_gray_400_is_readable(self):
        # --gray-400 to w praktyce kolor tekstu pomocniczego (~180 użyć) — nigdy #94a3b8.
        app = (CSS / "app.css").read_text(encoding="utf-8")
        light = _block(app, ':root[data-theme="light"]')
        self.assertGreaterEqual(contrast(light["--gray-400"], "#f8fafc"), 4.5)

    def test_inter_ships_polish_glyphs(self):
        fonts = (CSS / "fonts.css").read_text(encoding="utf-8")
        self.assertIn("inter-latin-ext-wght.woff2", fonts)
        self.assertIn("unicode-range: U+0100-017F;", fonts)  # ą ć ę ł ń ś ź ż
        self.assertEqual(fonts.count("font-weight: 400 700"), 2)
        for f in ("inter-latin-wght.woff2", "inter-latin-ext-wght.woff2"):
            self.assertTrue((STATIC / "fonts" / f).is_file(), f)

    def test_latin_ext_is_subset_with_polish_glyphs(self):
        # Pełny latin-ext (85 kB) kosztował ~0,45 s mobile LCP — plik ma zostać podzbiorem.
        ext = STATIC / "fonts" / "inter-latin-ext-wght.woff2"
        self.assertLess(ext.stat().st_size, 30_000)
        try:
            import brotli  # noqa: F401  (fontTools czyta woff2 tylko z brotli)
            from fontTools.ttLib import TTFont
        except ImportError:
            self.skipTest("brak fontTools/brotli")
        cmap = TTFont(ext).getBestCmap()
        self.assertTrue(all(ord(c) in cmap for c in "ąćęłńśźżĄĆĘŁŃŚŹŻ"))

    def test_base_loads_fonts_and_tokens_before_app_css(self):
        base = (TEMPLATES / "ui" / "base.html").read_text(encoding="utf-8")
        i = [base.index(f"ui/css/{n}.css") for n in ("fonts", "tokens", "app")]
        self.assertEqual(i, sorted(i))

    def test_app_css_whole_pixels_and_max_weight_700(self):
        app = (CSS / "app.css").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"font-size:\s*\d+\.\d+px", app))
        self.assertIsNone(re.search(r"font-weight:\s*(800|900)", app))

    def test_touch_targets_on_coarse_pointer(self):
        # Etap 6 audytu UX: na dotyku kontrolki formularzy ≥ --hit-min, checkbox/radio ≥ 24 px
        # (WCAG 2.5.8), linki w tabelach powiększone paddingiem. Na telefonie cele < 24 px: 503 → 6.
        # Pełny skan (103 wzorce): 53 → 7, zostały tylko linki w zdaniu (wyjątek AA) — checkbox
        # nie ściskany w flex-labelu, suwak wygrywa z lokalnym height:4px, summary, link-ikona
        # w tabeli ≥ 24 px szerokości, lokalne mini-przyciski (kamery 3D, kąt regału).
        app = (CSS / "app.css").read_text(encoding="utf-8")
        coarse = app[app.index("@media (pointer: coarse)"):]
        coarse = coarse[: coarse.index("\n    }\n")]
        for rule in ("select, textarea { min-height: var(--hit-min); }",
                     "input[type=checkbox], input[type=radio] { width: 24px !important; height: 24px !important; flex-shrink: 0; }",
                     "input[type=range] { height: var(--hit-min) !important; }",
                     "summary { min-height: 24px; }",
                     "td a:not(.btn), .table a:not(.btn) { padding-block: 5px; display: inline-block; min-width: 24px; }",
                     ".cam-btn, .disp-btn, .angle-btns button, .flow-head__link, .z-foot__user { min-width: 24px; min-height: 24px; }"):
            self.assertIn(rule, coarse)

    def test_solid_tokens_are_backgrounds_not_text(self):
        # Etap 9 audytu UX: --red/green/yellow/cyan-solid są TŁAMI pod biały tekst (te same w obu
        # motywach). Jako kolor tekstu na ciemnej karcie są nieczytelne — tekst bierze --red/--green/…
        pat = re.compile(r"(?<![-\w])color:(?:\{%[^%]*%\}[^;\"]*?)?\s*var\(--(?:red|green|yellow|cyan)-solid\)")
        found = [f"{p.relative_to(TEMPLATES.parents[2])}" for p in TEMPLATES.parents[2].glob("*/templates/**/*.html")
                 if pat.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(found, [])
