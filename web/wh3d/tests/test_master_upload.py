"""Regression: warehouse master upload was importing 0 rows (break after header)."""
import io
import openpyxl
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from ui.models import WarehouseLocationMaster, WarehouseLocationMasterBatch


def _xlsx(rows):
    wb = openpyxl.Workbook(); ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return SimpleUploadedFile("master.xlsx", buf.read(),
                              content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


class MasterUploadTests(TestCase):
    def test_imports_data_rows(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        f = _xlsx([
            ["Adres lokalizacji", "Poziom", "Typ magazynu", "Wysokość", "Maksymalna objętość", "Maksymalna waga"],
            ["B0-01-100A", 1, "0052", 235, 2.1, 500],
            ["B0-01-100X", 2, "0010", 226, 0, 500],
            ["B0-01-101A", 1, "0052", 235, 2.1, 500],
        ])
        r = self.client.post("/magazyn/master/upload/", {"name": "T", "file": f})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(WarehouseLocationMasterBatch.objects.filter(name="T", is_active=True).exists())
        # the regression: this used to be 0
        self.assertEqual(WarehouseLocationMaster.objects.count(), 3)
