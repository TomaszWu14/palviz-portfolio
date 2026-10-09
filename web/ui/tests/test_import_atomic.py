"""Data Center — tryb importu „wszystko albo nic" (atomiczny rollback przy błędzie)."""
import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import Product
from ui.roles import GROUP_MASTER_DATA


def _xlsx(headers, *data_rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)                                  # row 1 — headers
    ws.append(["opis"] * len(headers))                  # row 2 — descriptions (skipped)
    for r in data_rows:
        ws.append([r.get(h, "") for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(
        "produkty.xlsx", buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


class AtomicImportTests(TestCase):
    HEADERS = ["product_code", "product_name"]

    def setUp(self):
        u = get_user_model().objects.create_user("md", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)

    def _post(self, file, strict):
        data = {"file": file}
        if strict:
            data["all_or_nothing"] = "1"
        return self.client.post(reverse("ui:excel_import_products"), data)

    def _file(self):
        return _xlsx(self.HEADERS,
                     {"product_code": "GOOD", "product_name": "Dobry"},
                     {"product_code": "BAD", "product_name": "Zły"})

    @staticmethod
    def _patch_bad():
        """Make update_or_create blow up only on the BAD row, else behave normally."""
        real = Product.objects.update_or_create

        def fake(*args, **kwargs):
            if kwargs.get("code") == "BAD":
                raise ValueError("symulowany błąd wiersza")
            return real(*args, **kwargs)
        return patch.object(Product.objects, "update_or_create", side_effect=fake)

    def test_strict_mode_rolls_back_everything(self):
        with self._patch_bad():
            self._post(self._file(), strict=True)
        # The good row must NOT survive — the whole import was rolled back.
        self.assertFalse(Product.objects.filter(code="GOOD").exists())
        self.assertFalse(Product.objects.filter(code="BAD").exists())

    def test_non_strict_keeps_good_rows(self):
        with self._patch_bad():
            self._post(self._file(), strict=False)
        # Default behaviour: the valid row is committed, the bad one is skipped.
        self.assertTrue(Product.objects.filter(code="GOOD").exists())
        self.assertFalse(Product.objects.filter(code="BAD").exists())

    def test_strict_mode_commits_when_all_rows_valid(self):
        f = _xlsx(self.HEADERS,
                  {"product_code": "A1", "product_name": "Jeden"},
                  {"product_code": "A2", "product_name": "Dwa"})
        self._post(f, strict=True)
        self.assertTrue(Product.objects.filter(code="A1").exists())
        self.assertTrue(Product.objects.filter(code="A2").exists())


class UnitVolumeBackfillTests(TestCase):
    """Re-importing the master data must refresh the SAP per-OP volume on an EXISTING
    instruction — get_or_create only fills new rows, so a backfill is needed."""
    HEADERS = ["product_code", "product_name", "carton_name", "carton_l_cm", "carton_w_cm",
               "carton_h_cm", "unit_weight_kg", "pieces_per_carton", "unit_volume_m3"]

    def setUp(self):
        u = get_user_model().objects.create_user("mdv", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)

    def _imp(self, vol):
        f = _xlsx(self.HEADERS, {
            "product_code": "VOLP", "product_name": "Vol", "carton_name": "K 50x40x30",
            "carton_l_cm": 50, "carton_w_cm": 40, "carton_h_cm": 30,
            "unit_weight_kg": 0.1, "pieces_per_carton": 10, "unit_volume_m3": vol})
        return self.client.post(reverse("ui:excel_import_products"), {"file": f})

    def test_reimport_backfills_unit_volume(self):
        from ui.models import PalletizationInstruction
        self._imp(0.005)
        instr = PalletizationInstruction.objects.get(product__code="VOLP")
        self.assertAlmostEqual(instr.unit_volume_m3, 0.005, places=5)
        # Re-import with a new volume → the existing instruction is updated (not skipped).
        self._imp(0.003)
        instr.refresh_from_db()
        self.assertAlmostEqual(instr.unit_volume_m3, 0.003, places=5)


def _marm_xlsx(*rows):
    import openpyxl
    HEADERS = ["Materiał", "Alternatywna jednostka miary", "Mianownik", "Licznik",
               "Szerokość", "Wysokość", "Długość", "Jednostka wymiaru",
               "Waga brutto", "Jednostka wagi", "Objętość", "Jednostka objętości"]
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(HEADERS)
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO(); wb.save(buf)
    return SimpleUploadedFile("marm.xlsx", buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


class MarmDirectImportTests(TestCase):
    """One-template path: upload the raw SAP MARM export straight into PalViz; the per-OP
    Objętość becomes unit_volume_m3 so shipment volumes match SAP without a CLI step."""
    def setUp(self):
        u = get_user_model().objects.create_user("mdm", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)

    def test_marm_import_folds_units_into_instruction_with_op_volume(self):
        from ui.models import PalletizationInstruction
        f = _marm_xlsx(
            ["002ML", "OP",  1, 1,    0,  0,   0,  "",   0.1, "KG", 2.83, "CD3"],
            ["002ML", "KAR", 1, 25,  37, 41,  57,  "CM", 2.6, "KG", 86.5, "CD3"],
            ["002ML", "PAZ", 1, 1000, 80, 180, 120, "CM", 300, "KG", 0,    ""],
        )
        resp = self.client.post(reverse("ui:excel_import_marm"), {"file": f})
        self.assertEqual(resp.status_code, 302)
        instr = PalletizationInstruction.objects.get(product__code="002ML")
        self.assertEqual(instr.pcs_per_carton, 25)
        self.assertEqual((instr.carton_l, instr.carton_w, instr.carton_h), (57, 37, 41))
        self.assertAlmostEqual(instr.unit_volume_m3, 0.00283, places=5)

    def test_non_marm_file_is_rejected_clearly(self):
        f = _xlsx(["product_code", "product_name"], {"product_code": "X", "product_name": "Y"})
        resp = self.client.post(reverse("ui:excel_import_marm"), {"file": f})
        self.assertEqual(resp.status_code, 302)
        from ui.models import Product
        self.assertFalse(Product.objects.filter(code="X").exists())
