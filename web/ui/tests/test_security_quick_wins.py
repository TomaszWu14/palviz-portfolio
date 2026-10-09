"""Szybkie poprawki bezpieczeństwa z audytu 2026-09 (strażnicy regresji).

- ACL-001: /media/ domyślnie tylko po zalogowaniu; publiczne wyłącznie quotes/ i site/
- ACL-002: dokumentacja i schemat API (/api/v2/docs, openapi.json) tylko dla staff
- SEC-009: `next` w zamykaniu/usuwaniu wyjątku MD nie wyprowadza poza nasz host
- SEC-006: „Ostatnio przeglądane” w MATinfo nie składa innerHTML z kodu produktu
- SEC-007: neutralizacja formuł w eksportach CSV/XLSX
"""
import io
import os
import re
import shutil
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from testkit import factories as f
from testkit.personas import client_for
from ui.views.core.xlsx import neutralize_workbook, safe_cell, safe_csv_writer

EVIL = "https://evil.example/login"


class MediaDefaultPrivateTests(TestCase):          # ACL-001
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.enterContext(override_settings(MEDIA_ROOT=self.root))
        for rel in ("product_art/x.png", "quotes/2026/09/x.pdf", "site/mapka.png"):
            os.makedirs(os.path.join(self.root, os.path.dirname(rel)), exist_ok=True)
            with open(os.path.join(self.root, rel), "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")

    def test_anon_product_art_redirects_to_login(self):
        r = client_for("anon").get("/media/product_art/x.png")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])

    def test_logged_in_product_art_served(self):
        self.assertEqual(client_for("bez_roli").get("/media/product_art/x.png").status_code, 200)

    def test_token_page_folders_stay_public(self):
        # quotes/ = załącznik oferty przewoźnika, site/ = mapka na stronie kierowcy
        for url in ("/media/quotes/2026/09/x.pdf", "/media/site/mapka.png"):
            with self.subTest(url=url):
                self.assertEqual(client_for("anon").get(url).status_code, 200)


class ApiDocsStaffOnlyTests(TestCase):             # ACL-002
    URLS = ("/api/v2/openapi.json", "/api/v2/docs")

    def test_anon_denied(self):
        for url in self.URLS:
            with self.subTest(url=url):
                self.assertNotEqual(client_for("anon").get(url).status_code, 200)

    def test_non_staff_denied(self):
        for url in self.URLS:
            with self.subTest(url=url):
                self.assertNotEqual(client_for("Administratorzy").get(url).status_code, 200)

    def test_staff_allowed(self):
        for url in self.URLS:
            with self.subTest(url=url):
                self.assertEqual(client_for("superuser").get(url).status_code, 200)


class MdExceptionSafeNextTests(TestCase):          # SEC-009 / BIZ-010
    def _post(self, name):
        it = f.HandlingUnitItemFactory(md_exception=True)
        return client_for("Lider kontroli").post(
            reverse(f"ui:{name}", args=[it.pk]), {"next": EVIL})

    def test_close_and_delete_ignore_external_next(self):
        for name in ("hu_item_md_exception_close", "hu_item_md_exception_delete"):
            with self.subTest(view=name):
                r = self._post(name)
                self.assertEqual(r.status_code, 302)
                self.assertFalse(r["Location"].startswith("https://evil.example"), r["Location"])
                self.assertEqual(r["Location"], reverse("ui:hu_control_leader"))


class PhvRecentNoInnerHtmlTests(SimpleTestCase):   # SEC-006
    def test_recent_block_builds_dom_without_innerhtml(self):
        tpl = Path(__file__).resolve().parents[1] / "templates/ui/phv/home.html"
        src = tpl.read_text(encoding="utf-8")
        block = re.search(r"\{% if recent %\}(.*?)\{% endif %\}\s*\{% endblock %\}", src, re.S)
        self.assertIsNotNone(block, "nie znaleziono bloku „Ostatnio przeglądane”")
        self.assertNotIn("innerHTML", block.group(1))
        self.assertIn("textContent", block.group(1))

    def test_editor_2d_tooltip_builds_dom_without_innerhtml(self):
        tpl = Path(__file__).resolve().parents[2] / "wh3d/templates/ui/warehouse_map/editor.html"
        src = tpl.read_text(encoding="utf-8")
        block = re.search(r"function showTooltip\(.*?\n\}\n", src, re.S)
        self.assertIsNotNone(block, "nie znaleziono showTooltip w edytorze 2D")
        self.assertNotRegex(block.group(0), r"innerHTML\s*\+?=")
        self.assertIn("textContent", block.group(0))

    def test_carton_form_escapes_location_type_names(self):
        tpl = Path(__file__).resolve().parents[1] / "templates/ui/planner/carton_form.html"
        src = tpl.read_text(encoding="utf-8")
        self.assertNotIn("${g.label}", src)
        self.assertNotIn("${g.name}", src)
        self.assertIn("escHtml(g.label)", src)
        self.assertIn("escHtml(g.name)", src)


class FormulaNeutralizationTests(SimpleTestCase):  # SEC-007
    def test_safe_cell(self):
        for v in ("=HYPERLINK(\"http://x\",\"a\")", "+1", "-A1", "@SUM(A1)", "\tx", "\rx"):
            with self.subTest(v=v):
                self.assertEqual(safe_cell(v), "'" + v)
        for v in ("Klient", "", "—", -5, -1.5, 0, None):
            with self.subTest(v=v):
                self.assertEqual(safe_cell(v), v)

    def test_csv_writer(self):
        buf = io.StringIO()
        safe_csv_writer(buf, delimiter=";").writerow(["=1+1", -3, "ok"])
        self.assertEqual(buf.getvalue().strip(), "'=1+1;-3;ok")

    def test_workbook_formula_becomes_text(self):
        import openpyxl
        wb = openpyxl.Workbook()
        wb.active.append(["=HYPERLINK(\"http://x\",\"a\")", "-5", -5])
        neutralize_workbook(wb)
        a, b, c = (wb.active.cell(row=1, column=i) for i in (1, 2, 3))
        self.assertEqual(a.data_type, "s")
        self.assertTrue(a.value.startswith("'="))
        self.assertEqual((b.value, c.value), ("-5", -5))


class CarrierCsvExportTests(TestCase):             # SEC-007 — realny eksport
    def test_recipient_formula_neutralized(self):
        sh = f.ShipmentFactory(recipient_name="=HYPERLINK(\"http://x\",\"a\")")
        r = client_for("superuser").get(reverse("ui:planner_shipment_carrier_csv", args=[sh.pk]))
        self.assertEqual(r.status_code, 200)
        import csv
        cells = [c for row in csv.reader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";")
                 for c in row]
        self.assertIn("'=HYPERLINK(\"http://x\",\"a\")", cells)
        self.assertFalse([c for c in cells if c.startswith("=")])
