"""Czytnik xlsx master-daty: pomija wiersz OPISU szablonu, ale nie gubi danych z pliku
zbudowanego bez wiersza opisu (nagłówek + dane od 2. wiersza)."""
import io

from django.test import SimpleTestCase

from ui.views.core.xlsx import _read_xlsx_as_dicts, _looks_like_description_row


def _xlsx(rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class XlsxReaderTests(SimpleTestCase):
    HEADER = ["name", "length_cm", "width_cm", "height_cm"]

    def test_template_with_description_row_skips_it(self):
        # nagłówek + wiersz opisu (sam tekst) + 2 dane → 2 rekordy, opis pominięty.
        f = _xlsx([self.HEADER,
                   ["Unikalna nazwa", "Długość [cm]", "Szerokość [cm]", "Wysokość [cm]"],
                   ["Karton A", 40, 30, 25],
                   ["Karton B", 50, 35, 30]])
        rows = _read_xlsx_as_dicts(f)
        self.assertEqual([r["name"] for r in rows], ["Karton A", "Karton B"])

    def test_file_without_description_row_keeps_first_record(self):
        # nagłówek + dane od 2. wiersza (bez opisu) → NIE gubimy 1. rekordu.
        f = _xlsx([self.HEADER,
                   ["Karton A", 40, 30, 25],
                   ["Karton B", 50, 35, 30]])
        rows = _read_xlsx_as_dicts(f)
        self.assertEqual([r["name"] for r in rows], ["Karton A", "Karton B"])

    def test_looks_like_description_row(self):
        self.assertTrue(_looks_like_description_row(["Nazwa", "Długość [cm]", "opcjonalnie"]))
        self.assertFalse(_looks_like_description_row(["Karton A", 40, 30, 25]))
        self.assertFalse(_looks_like_description_row(["Karton A", "40,5", "30"]))  # liczba z przecinkiem
        self.assertFalse(_looks_like_description_row([None, "", "  "]))            # pusty ≠ opis
