"""Komponenty bazowe design systemu (etap 3 audytu UX, krok 2): ikony Lucide, przycisk
z samą ikoną, pole z etykietą, żywy podgląd /ui/."""

import re
from pathlib import Path

from django import forms
from django.template import Context, Template, TemplateSyntaxError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from testkit.personas import client_for

WEB = Path(__file__).resolve().parents[2]
SPRITE = WEB / "ui" / "static" / "ui" / "icons" / "lucide.svg"


def render(src, **ctx):
    return Template("{% load palviz_extras %}" + src).render(Context(ctx))


class IconTagTests(SimpleTestCase):
    def test_decorative_icon_is_hidden_from_screen_readers(self):
        html = render('{% icon "package" %}')
        self.assertIn('aria-hidden="true"', html)
        self.assertIn("lucide.svg#package", html)
        self.assertIn('width="16"', html)
        self.assertIn('stroke="currentColor"', html)  # działa też bez app.css (skaner)

    def test_labelled_icon_is_announced(self):
        html = render('{% icon "triangle-alert" 20 label="Uwaga" %}')
        self.assertIn('role="img" aria-label="Uwaga"', html)
        self.assertNotIn("aria-hidden", html)

    def test_size_outside_scale_is_rejected(self):
        with self.assertRaises(TemplateSyntaxError):
            render('{% icon "package" 18 %}')

    def test_icon_button_requires_accessible_name(self):
        with self.assertRaises(TemplateSyntaxError):
            render('{% icon_button "trash-2" %}')

    def test_icon_button_renders_label_confirm_and_escapes(self):
        html = render('{% icon_button "trash-2" label=l confirm=c testid="del" %}',
                      l='Usuń "A"', c="Usunąć <b>A</b>?")
        self.assertIn('aria-label="Usuń &quot;A&quot;"', html)
        self.assertIn('title="Usuń &quot;A&quot;"', html)
        self.assertIn('data-confirm="Usunąć &lt;b&gt;A&lt;/b&gt;?"', html)
        self.assertIn('data-testid="del"', html)
        self.assertIn('type="button"', html)
        self.assertIn('aria-hidden="true"', html)  # ikona w środku dekoracyjna — nazwa z przycisku

    def test_icon_button_as_link(self):
        html = render('{% icon_button "pencil" label="Edytuj" href="/x/" %}')
        self.assertTrue(html.startswith('<a href="/x/"'))

    def test_every_icon_used_in_templates_exists_in_sprite(self):
        ids = set(re.findall(r'<symbol id="([\w-]+)"', SPRITE.read_text(encoding="utf-8")))
        self.assertGreater(len(ids), 100)
        used = {}
        for p in WEB.rglob("*.html"):
            if "staticfiles" in p.parts:
                continue
            for name in re.findall(r'{%\s*(?:icon|icon_button)\s+"([\w-]+)"', p.read_text(encoding="utf-8")):
                used.setdefault(name, p.name)
        missing = {n: f for n, f in used.items() if n not in ids}
        self.assertEqual(missing, {}, "Dopisz ikonę do audit/tools/build_icons.py i przebuduj sprite")


class _F(forms.Form):
    ean = forms.CharField(label="Kod EAN", help_text="13 cyfr.")
    q = forms.CharField(label="Szukaj", required=False)


class FieldComponentTests(SimpleTestCase):
    def test_field_with_label_help_and_error(self):
        f = _F({"ean": "x"})
        f.is_valid()
        f.add_error("ean", "Kod musi mieć 13 cyfr.")
        html = Template('{% include "ui/components/_field.html" with field=f.ean %}').render(Context({"f": f}))
        self.assertIn('<label class="gv-field__label" for="id_ean">Kod EAN', html)
        self.assertIn('class="req"', html)
        self.assertIn('id="id_ean_helptext"', html)
        self.assertIn('aria-invalid="true"', html)
        self.assertIn("Kod musi mieć 13 cyfr.", html)

    def test_hidden_label_stays_for_screen_readers(self):
        html = Template('{% include "ui/components/_field.html" with field=f.q hide_label=1 %}').render(
            Context({"f": _F()}))
        self.assertIn('class="gv-field__label sr-only" for="id_q"', html)
        self.assertNotIn('class="req"', html)


class StyleguideTests(TestCase):
    def test_styleguide_shows_all_components(self):
        r = client_for("superuser").get(reverse("ui:ui_styleguide"))
        self.assertEqual(r.status_code, 200)
        for marker in ("sg-icons", "sg-buttons", "sg-toolbar", "sg-table", "sg-badges", "sg-field",
                       "sg-feedback", "sg-states", 'class="toolbar"', 'class="filterbar"',
                       "table-scroll", "cell-clip", "is-hu-to_recheck", "is-wh-dlt",
                       'data-testid="empty-state"', "data-confirm=", "gv-skeleton"):
            self.assertContains(r, marker)


class ModuleIconTests(SimpleTestCase):
    def test_every_module_has_lucide_icon_in_sprite(self):
        from core.platform_modules import MODULES
        from ui.nav_icons import MODULE_ICONS

        ids = set(re.findall(r'<symbol id="([\w-]+)"', SPRITE.read_text(encoding="utf-8")))
        for m in MODULES:
            with self.subTest(module=m.key):
                self.assertIn(m.key, MODULE_ICONS, "Dopisz moduł do ui/nav_icons.MODULE_ICONS")
                self.assertIn(MODULE_ICONS[m.key], ids)

    def test_module_icon_filter_and_nav_share_one_source(self):
        from ui.nav_icons import nav_icon_svg

        self.assertIn("lucide.svg#truck", render('{{ "transport"|module_icon }}'))
        self.assertIn("lucide.svg#truck", nav_icon_svg("transport"))
        self.assertIn("topnav__ico", nav_icon_svg("transport"))


class TableScrollA11yTests(SimpleTestCase):
    def test_every_scroll_region_is_keyboard_reachable_and_named(self):
        bad = []
        for p in WEB.rglob("*.html"):
            if "staticfiles" in p.parts:
                continue
            for tag in re.findall(r'<div class="table-scroll"[^>]*>', p.read_text(encoding="utf-8")):
                if 'tabindex="0"' not in tag or "aria-label=" not in tag:
                    bad.append(f"{p.name}: {tag}")
        self.assertEqual(bad, [], "Dodaj tabindex=\"0\" role=\"region\" aria-label=\"…\" (axe scrollable-region-focusable)")


class TemplateHeadingBalanceTests(SimpleTestCase):
    def test_every_h1_is_closed_by_h1(self):
        # Regresja #708: <div class="page-header__sub">…</h1> wciągał filtry i tabelę do nagłówka.
        bad = []
        for p in WEB.rglob("*.html"):
            if "staticfiles" in p.parts:
                continue
            s = p.read_text(encoding="utf-8")
            if len(re.findall(r"<h1\b", s)) != s.count("</h1>") or re.search(r"<div[^>]*>[^<]*</h1>", s):
                bad.append(p.name)
        self.assertEqual(bad, [])
