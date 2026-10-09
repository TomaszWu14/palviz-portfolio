"""Warehouse bugfixy (code-review): silnik niezgodności stanu dopasowuje lokalizacje
case-insensitive (koniec z fałszywym „błędna lokalizacja" + gubieniem przepełnienia);
heatmapa łączy aktywność z komórkami bez względu na wielkość liter."""
from django.test import TestCase

from ui.models import (Product, PalletizationInstruction, Shipment, HandlingUnit,
                       HandlingUnitItem, WarehouseLocationMasterBatch, WarehouseLocationMaster,
                       Task)
from ui.notifications import run_stock_discrepancy_checks


class StockDiscrepancyLocationCaseTests(TestCase):
    def _master(self, code, vol):
        b = WarehouseLocationMasterBatch.objects.create(is_active=True)
        WarehouseLocationMaster.objects.create(batch=b, location_code=code, max_volume_m3=vol)
        return b

    def _stock_hu(self, loc, cartons):
        p = Product.objects.create(code="RW", name="X")
        PalletizationInstruction.objects.create(product=p, version=1, is_active=True,
            unit_weight=0.5, pcs_per_carton=10, carton_l=40, carton_w=30, carton_h=25,
            pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15, max_height_total_cm=200)
        sh = Shipment.objects.create(name="S", is_stock=True)
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HW", location=loc)
        HandlingUnitItem.objects.create(hu=hu, product=p, ref_code="RW", alt_qty=cartons, alt_unit="KAR")
        return hu

    def test_no_false_badloc_on_case_difference(self):
        self._master("B0-01-100A", vol=50.0)
        self._stock_hu("b0-01-100a", cartons=1)         # ta sama lokalizacja, inny case
        run_stock_discrepancy_checks()
        self.assertFalse(Task.objects.filter(dedup_key__startswith="stock:badloc:").exists())

    def test_overcap_detected_despite_case(self):
        # 1 karton 40×30×25cm = 0,03 m³ ... potrzeba dużo, by przekroczyć — użyj małej pojemności.
        self._master("B0-01-100A", vol=0.01)            # pojemność 0,01 m³
        self._stock_hu("b0-01-100a", cartons=5)         # 5×0,03 = 0,15 m³ > 0,01
        run_stock_discrepancy_checks()
        self.assertTrue(Task.objects.filter(dedup_key__startswith="stock:overcap:").exists())
