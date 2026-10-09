"""Import master daty lokalizacji — przypadki BRZEGOWE (ticket 11,
.scratch/concerns-cleanup): wadliwy plik, brak kolumny adresu, duplikaty kodów,
nagłówki SAP (aliasy), normalizacja liczb SAP, dezaktywacja starego batcha.
Happy-path jest w test_master_upload.py; tu są ścieżki błędne, które dotąd
łapało dopiero produkcyjne wgranie."""
import io

import openpyxl
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from ui.models import WarehouseLocationMaster, WarehouseLocationMasterBatch


def _xlsx(rows, name="master.xlsx"):
    wb = openpyxl.Workbook(); ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return SimpleUploadedFile(
        name, buf.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


class MasterUploadEdgeTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="edge", password="x")
        self.client.post("/login/", {"username": "edge", "password": "x"})

    def _upload(self, f, name="T"):
        return self.client.post("/magazyn/master/upload/", {"name": name, "file": f})

    def test_malformed_file_rejected_without_batch(self):
        """Nie-xlsx (śmieci bajtowe) → komunikat błędu, zero batchy, zero wierszy."""
        f = SimpleUploadedFile("master.xlsx", b"to nie jest xlsx", content_type="text/plain")
        r = self._upload(f)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(WarehouseLocationMasterBatch.objects.count(), 0)
        self.assertEqual(WarehouseLocationMaster.objects.count(), 0)

    def test_missing_location_column_rejected_without_batch(self):
        """Nagłówek bez kolumny adresu → błąd, batch NIE powstaje (stary zostaje aktywny)."""
        old = WarehouseLocationMasterBatch.objects.create(name="Stary", is_active=True)
        f = _xlsx([["Poziom", "Typ magazynu"], [1, "0052"]])
        self._upload(f)
        self.assertEqual(WarehouseLocationMasterBatch.objects.count(), 1)
        old.refresh_from_db()
        self.assertTrue(old.is_active)                       # aktywny batch nietknięty

    def test_duplicate_codes_first_wins(self):
        """Zduplikowany kod lokalizacji w pliku: pierwszy wiersz wygrywa, brak dubla w DB."""
        f = _xlsx([
            ["Adres lokalizacji", "Poziom", "Typ magazynu", "Wysokość", "Maksymalna objętość", "Maksymalna waga"],
            ["B0-01-100A", 1, "0052", 235, 2.1, 500],
            ["B0-01-100A", 9, "9999", 1, 0, 1],              # duplikat — pominięty
            ["B0-01-101A", 1, "0052", 235, 2.1, 500],
        ])
        self._upload(f)
        self.assertEqual(WarehouseLocationMaster.objects.count(), 2)
        row = WarehouseLocationMaster.objects.get(location_code="B0-01-100A")
        self.assertEqual(row.warehouse_type, "0052")          # dane z PIERWSZEGO wiersza

    def test_empty_and_none_codes_skipped(self):
        f = _xlsx([
            ["Adres lokalizacji", "Poziom"],
            ["", 1], [None, 1], ["None", 1], ["B0-02-200B", 1],
        ])
        self._upload(f)
        self.assertEqual(WarehouseLocationMaster.objects.count(), 1)

    def test_sap_export_headers_and_number_normalization(self):
        """Surowy eksport EWM/SAP: aliasy nagłówków (Miejsce składowania, Całkowite
        zdolności) + liczby z odstępem tysięcy i przecinkiem ('2 350,000')."""
        f = _xlsx([
            ["Miejsce składowania", "Poziom miejsca skł.", "Typ magazynu",
             "Całkowite zdolności", "Maksymalna objętość", "Maksymalna waga", "Blok. wyd."],
            ["B0-03-300C", 1, "0010", "2 350,000", "2,100", "1 000,5", "X"],
        ])
        self._upload(f)
        row = WarehouseLocationMaster.objects.get(location_code="B0-03-300C")
        self.assertEqual(row.height_mm, 2350)
        self.assertAlmostEqual(row.max_volume_m3, 2.1, places=3)
        self.assertAlmostEqual(row.max_weight_kg, 1000.5, places=1)
        self.assertTrue(row.blocked_pick)

    def test_new_batch_deactivates_previous(self):
        """Kolejne wgranie: nowy batch aktywny, poprzedni dezaktywowany (nie skasowany)."""
        rows = [["Adres lokalizacji", "Poziom"], ["B0-04-400D", 1]]
        self._upload(_xlsx(rows), name="Pierwszy")
        self._upload(_xlsx(rows), name="Drugi")
        first = WarehouseLocationMasterBatch.objects.get(name="Pierwszy")
        second = WarehouseLocationMasterBatch.objects.get(name="Drugi")
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)
        self.assertEqual(second.location_count, 1)

    def test_oversize_file_rejected(self):
        f = SimpleUploadedFile("big.xlsx", b"x" * (10 * 1024 * 1024 + 1))
        self._upload(f)
        self.assertEqual(WarehouseLocationMasterBatch.objects.count(), 0)
