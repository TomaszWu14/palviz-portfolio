"""Fixy (stałe lokalizacje pickingowe) z SAP: import min/maks + „Zapas / min" na karcie
produktu (MatInfo). Poniżej minimum → do uzupełnienia."""
import io

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import Product, Shipment, HandlingUnit, HandlingUnitItem, FixLocation
from ui.roles import GROUP_MASTER_DATA, GROUP_WAREHOUSE
from ui.views.phv import _storage_strategy


def _user(group):
    u = get_user_model().objects.create_user(username="u" + group[:3], password="x")
    u.groups.add(Group.objects.get_or_create(name=group)[0])
    return u


def _xlsx(rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile("fix.xlsx", buf.getvalue(),
                              content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


class FixImportTest(TestCase):
    def test_import_creates_fix_with_min_max(self):
        self.client.force_login(_user(GROUP_MASTER_DATA))
        f = _xlsx([
            ["Miejsce składowania", "Produkt", "Typ magazynu", "Maksym. ilość",
             "Ilość minimalna", "Wyśw. JM - il. min."],
            ["B0-15-422B", "BT-039-OT-SMS-35-L", "0050", 480, 40, "OP"],
        ])
        self.client.post(reverse("ui:excel_import_fix"), {"file": f})
        fx = FixLocation.objects.get(location_code="B0-15-422B")
        self.assertEqual(fx.ref_code, "BT-039-OT-SMS-35-L")
        self.assertEqual(fx.min_qty, 40)
        self.assertEqual(fx.max_qty, 480)
        self.assertEqual(fx.warehouse_type, "0050")
        self.assertEqual(fx.uom, "OP")

    def test_import_upsert_by_location_and_ref(self):
        self.client.force_login(_user(GROUP_MASTER_DATA))
        head = ["Miejsce składowania", "Produkt", "Ilość minimalna"]
        self.client.post(reverse("ui:excel_import_fix"),
                         {"file": _xlsx([head, ["L1", "REF1", 10]])})
        self.client.post(reverse("ui:excel_import_fix"),
                         {"file": _xlsx([head, ["L1", "REF1", 25]])})
        self.assertEqual(FixLocation.objects.filter(location_code="L1", ref_code="REF1").count(), 1)
        self.assertEqual(FixLocation.objects.get(location_code="L1").min_qty, 25)


class FixDisplayTest(TestCase):
    def _product_with_fix(self, current, minimum):
        p = Product.objects.create(code="REF-FX", name="X")
        FixLocation.objects.create(location_code="B0-15-422B", ref_code="REF-FX",
                                   warehouse_type="0050", min_qty=minimum, max_qty=480, uom="OP")
        sh = Shipment.objects.create(name="Stock", is_stock=True)
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HU1",
                                         warehouse_type="0050", location="B0-15-422B")
        HandlingUnitItem.objects.create(hu=hu, product=p, ref_code=p.code, expected_qty=current)
        return p

    def test_fix_row_current_vs_min_below(self):
        p = self._product_with_fix(current=40, minimum=60)
        fx = _storage_strategy(p)["fixes"][0]
        self.assertEqual((fx["current"], fx["min_qty"]), (40, 60))
        self.assertTrue(fx["below_min"])                    # 40 < 60 → uzupełnić

    def test_fix_row_at_or_above_min_not_flagged(self):
        p = self._product_with_fix(current=80, minimum=60)
        fx = _storage_strategy(p)["fixes"][0]
        self.assertFalse(fx["below_min"])

    def test_product_view_renders_zapas_min(self):
        self.client.force_login(_user(GROUP_WAREHOUSE))
        self._product_with_fix(current=40, minimum=60)
        r = self.client.get(reverse("ui:phv_home"), {"q": "REF-FX"})
        self.assertContains(r, "40 / 60")                   # Zapas / min w wierszu fix
