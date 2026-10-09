"""Accessibility / frontend regressions: robots.txt, skip link, label association."""
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

# Zasada projektu (CLAUDE.md): żadnych CDN-ów w runtime — te hosty nie mogą wystąpić
# ani w szablonach, ani na allowliście CSP (core/middleware.py).
FONT_CDN_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")


class A11yTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("root", "r@x.pl", "x")

    def test_robots_disallows_indexing(self):
        r = self.client.get("/robots.txt")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Disallow: /", r.content.decode())

    def test_base_has_skip_link_and_content_target(self):
        self.client.force_login(self.admin)
        html = self.client.get("/planner/products/").content.decode()
        self.assertIn('href="#content"', html)          # skip link
        self.assertIn('id="content"', html)             # its target on <main>

    def test_no_runtime_font_cdn_in_any_template(self):
        # Skan WSZYSTKICH szablonów, nie testy per-strona: samodzielne shelle
        # (login, skaner, TV, e-maile, PDF, standalone) nie dziedziczą z base.html,
        # więc pojedyncze URL-e nie wystarczą — regresja weszła właśnie przez login.
        templates = Path(__file__).resolve().parent.parent / "templates"
        for tpl in templates.rglob("*.html"):
            text = tpl.read_text(encoding="utf-8")
            for host in FONT_CDN_HOSTS:
                self.assertNotIn(host, text, f"{tpl.relative_to(templates)} odwołuje się do {host}")

    def test_csp_does_not_allowlist_font_cdn(self):
        # Polityka i praktyka w jednym miejscu: skoro nic nie ładuje fontów z CDN,
        # CSP nie może ich allowlistować (inaczej cicho zalegalizuje regresję).
        resp = self.client.get("/login/")
        csp = (resp.headers.get("Content-Security-Policy-Report-Only")
               or resp.headers.get("Content-Security-Policy", ""))
        self.assertTrue(csp)   # middleware ustawia nagłówek w obu trybach
        for host in FONT_CDN_HOSTS:
            self.assertNotIn(host, csp)

    def test_form_labels_are_associated(self):
        self.client.force_login(self.admin)
        html = self.client.get("/planner/products/new/").content.decode()
        # The product form's labels now carry for="id_..." (screen-reader association).
        self.assertIn('for="id_code"', html)
