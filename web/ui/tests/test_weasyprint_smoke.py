"""Smoke WeasyPrint: HTML→PDF daje prawdziwy PDF (strażnik podbić wersji, np. 69→70).

Widoki PDF (WZ, lokalizacje, ZARIA) po cichu degradują do HTML, gdy WeasyPrint
nie działa — ich testy przechodzą w obu przypadkach, więc nie wyłapałyby
zepsutego podbicia. Ten test woła bibliotekę wprost. Pomijany, gdy brak natywnych
bibliotek (Pango) — typowo na Windows bez GTK.
"""
from unittest import SkipTest

from django.test import SimpleTestCase


class WeasyPrintSmoke(SimpleTestCase):
    def test_html_to_pdf_bytes(self):
        try:
            from weasyprint import HTML
        except (ImportError, OSError) as exc:  # OSError: brak libpango/libgobject
            raise SkipTest(f"WeasyPrint niedostępny w środowisku: {exc}")
        pdf = HTML(string="<meta charset='utf-8'><h1>Zażółć gęślą jaźń</h1>").write_pdf()
        self.assertTrue(pdf.startswith(b"%PDF"))
