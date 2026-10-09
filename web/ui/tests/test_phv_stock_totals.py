"""PHV domknięcie 3 decyzji: headline = CAŁY stock on-hand (proces + kubły), podliczenie
niepobieralnego (blocked/quality/returns), 9010 DLT po całym segmencie (nie substring)."""
from django.test import TestCase

from ui.models import Product, Shipment, HandlingUnit, HandlingUnitItem
from ui.views.phv import _storage_strategy, _wh_bucket


class PhvStockTotalTests(TestCase):
    def _stock(self, code, wt, loc, qty, status=""):
        p = Product.objects.get(code="P") if Product.objects.filter(code="P").exists() \
            else Product.objects.create(code="P", name="X")
        sh = Shipment.objects.create(name="S" + code, is_stock=True)
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code=code, warehouse_type=wt,
                                         location=loc, stock_status=status)
        HandlingUnitItem.objects.create(hu=hu, product=p, ref_code="P", expected_qty=qty)
        return p

    def test_total_includes_process_stock(self):
        # 100 w procesie pickingowym (0050=Fix) + 40 w zapasie (0010) = 140 on-hand, 2 palety.
        self._stock("HFIX", "0050", "B0-01", 100)
        p = self._stock("HZAP", "0010", "C1-02", 40)
        st = _storage_strategy(p)["stock_total"]
        self.assertEqual(st["count"], 2)              # obie palety, mimo że Fix nie jest kubłem
        self.assertEqual(st["base_qty"], 140)         # suma z procesem, nie tylko kubły

    def test_blocked_qty_subtotal(self):
        self._stock("HB", "0010", "C1-03", 50, status="B6")   # B6 = zablokowane
        p = self._stock("HF", "0010", "C1-04", 30, status="")  # wolne
        st = _storage_strategy(p)["stock_total"]
        self.assertEqual(st["base_qty"], 80)
        self.assertEqual(st["blocked_qty"], 50)       # z tego niepobieralne

    def test_9010_dlt_segment_not_substring(self):
        # „SPLIT"/„LITER" NIE są DLT; „DLT-05" jest.
        self.assertEqual(_wh_bucket("9010", "SPLIT-01")[1], "inne")   # GR-Zone, nie DLT
        self.assertEqual(_wh_bucket("9010", "LITER-2")[1], "inne")
        self.assertEqual(_wh_bucket("9010", "DLT-05")[1], "dlt")
        self.assertEqual(_wh_bucket("9010", "05.DLT")[1], "dlt")
