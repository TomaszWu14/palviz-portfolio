# Fala 1: waga/objętość HU (feed → master data) i sumowanie jednostek pobrania.
from django.test import TestCase

from ui.hu_metrics import hu_metrics, summarize_units
from ui.models import (HandlingUnit, HandlingUnitItem, PalletizationInstruction,
                       Product, Shipment)


def _instr(product, **over):
    base = dict(product=product, carton_l=40, carton_w=30, carton_h=20,
                unit_weight=0.5, pcs_per_carton=10, is_active=True)
    base.update(over)
    return PalletizationInstruction.objects.create(**base)


class HuMetricsTests(TestCase):
    def setUp(self):
        self.p = Product.objects.create(code="ZR-1", name="Kompresy")
        self.sh = Shipment.objects.create(name="D-1")

    def test_weight_from_feed_wins_volume_from_master(self):
        _instr(self.p, unit_volume_m3=0.001)
        hu = HandlingUnit.objects.create(shipment=self.sh, code="HU1")
        HandlingUnitItem.objects.create(hu=hu, ref_code="ZR-1", product=self.p,
                                        base_qty=100, weight_kg=42.0)
        m = hu_metrics([hu])[hu.pk]
        self.assertEqual(m["weight_kg"], 42.0)
        self.assertEqual(m["volume_m3"], 0.1)      # 100 × 0.001

    def test_fallbacks_estimated(self):
        _instr(self.p, unit_volume_m3=None)        # objętość z kartonu, waga z unit_weight
        hu = HandlingUnit.objects.create(shipment=self.sh, code="HU2")
        HandlingUnitItem.objects.create(hu=hu, ref_code="ZR-1", product=self.p, base_qty=20)
        m = hu_metrics([hu])[hu.pk]
        self.assertEqual(m["weight_kg"], 10.0)     # 20 × 0.5
        self.assertAlmostEqual(m["volume_m3"], 0.048)   # 2 kartony × 0.024 m³
        self.assertTrue(m["estimated"])

    def test_feed_pallet_dims_win(self):
        hu = HandlingUnit.objects.create(shipment=self.sh, code="HU3",
                                         length_cm=120, width_cm=80, height_cm=100)
        m = hu_metrics([hu])[hu.pk]
        self.assertEqual(m["volume_m3"], 0.96)

    def test_no_data_returns_none(self):
        hu = HandlingUnit.objects.create(shipment=self.sh, code="HU4")
        HandlingUnitItem.objects.create(hu=hu, ref_code="X-1", base_qty=5)
        m = hu_metrics([hu])[hu.pk]
        self.assertIsNone(m["weight_kg"])
        self.assertIsNone(m["volume_m3"])


class SummarizeUnitsTests(TestCase):
    def setUp(self):
        self.sh = Shipment.objects.create(name="D-2")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, code="HU5")

    def _item(self, product, qty, unit="SZT"):
        return HandlingUnitItem.objects.create(hu=self.hu, ref_code=product.code if product else "X",
                                               product=product, base_qty=qty, base_unit=unit)

    def test_szt_rolls_up_to_kar_and_opz(self):
        p = Product.objects.create(code="ZR-2", name="A")
        _instr(p, pcs_per_carton=100, pcs_per_inner_pack=10)
        self._item(p, 235)                          # 2 KAR + 3 OPZ + 5 SZT
        self.assertEqual(summarize_units(self.hu.items.all()), "2 KAR · 3 OPZ · 5 SZT")

    def test_no_conversion_flagged(self):
        self._item(None, 7, unit="KPL")
        self.assertIn("7 KPL (bez przelicznika)", summarize_units(self.hu.items.all()))

    def test_mixed_products_aggregate(self):
        p1 = Product.objects.create(code="ZR-3", name="A")
        p2 = Product.objects.create(code="ZR-4", name="B")
        _instr(p1, pcs_per_carton=10)
        _instr(p2, pcs_per_carton=10)
        self._item(p1, 25)                          # 2 KAR + 5 SZT
        self._item(p2, 10)                          # 1 KAR
        self.assertEqual(summarize_units(self.hu.items.all()), "3 KAR · 5 SZT")
