"""Regresje naprawionych błędów importu HU (feed SAP / Power BI).

1. Cache przesyłek kluczowany (nazwa, is_stock) — dostawa „Stock magazynowy” ≠ kontener stock.
2. BIZ-001 rozszerzone: HU w kontroli (status ≠ planned lub z próbami) nie dostaje z feedu
   ani pozycji, ani metadanych, ani przepięcia na inną dostawę; liczymy takie HU.
3. Cofnięcie kompletacji przesyłki (picking_done) zeruje picking_complete_at.
4. Wymiar 0 w feedzie = „brak danych” (model/UI traktują 0 jak brak) — nie zapisujemy.
"""
from django.test import TestCase

from huctl.hu_import import import_hu_rows
from huctl.models import HandlingUnit
from huctl.models_control import HUControlAttempt
from ui.models import Product, Shipment

HDR = ["dokument", "jednostka obsługi", "materiał", "ilość", "miejsce składowania",
       "skompletowana", "status zapasu", "picking_done", "długość"]


def _row(doc="D-1", hu="HU-A", ref="P1", qty="10", loc="", done="", stock="", picking="",
         length=""):
    return [doc, hu, ref, qty, loc, done, stock, picking, length]


class HuImportFixesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Product.objects.create(code="P1", name="Produkt P1")

    def test_stock_row_and_same_named_delivery_are_separate(self):
        ok, info = import_hu_rows(HDR, [_row(doc="Stock magazynowy", hu="HU-T"),
                                        _row(doc="", hu="HU-S")])
        self.assertTrue(ok)
        t = HandlingUnit.objects.select_related("shipment").get(code="HU-T")
        s = HandlingUnit.objects.select_related("shipment").get(code="HU-S")
        self.assertFalse(t.shipment.is_stock)
        self.assertTrue(s.shipment.is_stock)
        self.assertNotEqual(t.shipment_id, s.shipment_id)
        self.assertEqual(Shipment.objects.filter(name="Stock magazynowy").count(), 2)

    def test_locked_hu_gets_nothing_from_feed(self):
        import_hu_rows(HDR, [_row(loc="A-01", stock="F2"), _row(hu="HU-B", loc="B-01")])
        HandlingUnit.objects.filter(code="HU-A").update(status="in_progress")
        HUControlAttempt.objects.create(hu=HandlingUnit.objects.get(code="HU-B"))
        HandlingUnit.objects.filter(code="HU-B").update(location="")
        before = {h.code: (h.shipment_id, h.seq) for h in HandlingUnit.objects.all()}
        ok, info = import_hu_rows(HDR, [
            _row(doc="D-7", qty="77", loc="X-1", done="X", stock="Q4", length="120"),
            _row(hu="HU-B", qty="66", loc="Y-1", done="X"),
            _row(hu="HU-B", qty="65"),
        ])
        self.assertTrue(ok)
        a = HandlingUnit.objects.get(code="HU-A")
        b = HandlingUnit.objects.get(code="HU-B")
        self.assertEqual((a.shipment_id, a.seq), before["HU-A"])
        self.assertEqual((b.shipment_id, b.seq), before["HU-B"])
        self.assertEqual((a.location, a.stock_status, a.is_completed, a.length_cm),
                         ("A-01", "F2", False, None))
        self.assertEqual((b.location, b.is_completed), ("", False))
        self.assertEqual([i.base_qty for i in a.items.all()], [10.0])
        self.assertEqual(info["skipped_locked"], 3)
        self.assertEqual(info["locked_hus"], 2)
        # Zablokowana HU nie tworzy pustej przesyłki z feedu, ale jest „widziana”.
        self.assertFalse(Shipment.objects.filter(name="D-7").exists())
        self.assertIsNotNone(a.last_seen_at)

    def test_picking_revert_clears_timestamp(self):
        import_hu_rows(HDR, [_row(picking="X")])
        sh = Shipment.objects.get(name="D-1")
        self.assertTrue(sh.picking_complete)
        self.assertIsNotNone(sh.picking_complete_at)
        import_hu_rows(HDR, [_row(picking="nie")])
        sh.refresh_from_db()
        self.assertFalse(sh.picking_complete)
        self.assertIsNone(sh.picking_complete_at)
        # Ponowne zakończenie stempluje świeżo.
        import_hu_rows(HDR, [_row(picking="X")])
        sh.refresh_from_db()
        self.assertIsNotNone(sh.picking_complete_at)

    def test_zero_dimension_means_no_data(self):
        import_hu_rows(HDR, [_row(length="0")])
        self.assertIsNone(HandlingUnit.objects.get(code="HU-A").length_cm)
        import_hu_rows(HDR, [_row(length="120")])
        self.assertEqual(HandlingUnit.objects.get(code="HU-A").length_cm, 120.0)
