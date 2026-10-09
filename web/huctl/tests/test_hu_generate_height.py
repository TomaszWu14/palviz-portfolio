"""Generowanie HU pakuje palety na wysokości wybranej na wysyłce (audyt BIZ-007).

Wycena spedycji, zapytanie do magazynu, KPI i CMR liczą na `selected_pallet_height_cm`
(bez wyboru — domyślnej 1,8 m). Generowanie HU pakowało zawsze na 1,8 m, więc przy
wybranych 2,25 m magazyn dostawał etykiety HU dla niższych (i liczniejszych) palet niż te,
które zamówiono u spedycji. HU powstają z fizycznego ułożenia 3D (nie z szacunku
objętościowego wyceny), więc test porównuje z ułożeniem 3D na tej samej wysokości."""
from django.test import TestCase

from huctl.views.hu import _generate_handling_units
from ui.models import PalletizationInstruction, Product, Shipment, ShipmentLine
from ui.views.core import _DEFAULT_PALLET_HEIGHT, _build_shipment_three_data, _calc_shipment_data


class HuGenerationUsesShipmentHeightTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Lekkie kartony, ~10 m³ → liczba palet zależy od wysokości (objętość dominuje).
        p = Product.objects.create(code="VOL-HU", name="Objętościowy")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.1, pcs_per_carton=10, demand_pcs=100, is_active=True)
        cls.sh = Shipment.objects.create(name="HU-WYS")
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=334, unit="kar",
                                    source_unit="KAR")

    def _generate(self, height_cm):
        Shipment.objects.filter(pk=self.sh.pk).update(selected_pallet_height_cm=height_cm)
        self.sh.refresh_from_db()
        return _generate_handling_units(self.sh), self.sh.handling_units.count()

    def _packed_at(self, height_cm):
        data = _build_shipment_three_data(_calc_shipment_data(self.sh), max_h=height_cm,
                                          render_cap=None)
        return len(data["pallets"])

    def test_hu_follow_3d_packing_at_selected_height(self):
        self.assertLess(self._packed_at(225), self._packed_at(_DEFAULT_PALLET_HEIGHT))  # fikstura
        self.assertEqual(self._generate(225), (self._packed_at(225), self._packed_at(225)))

    def test_without_selection_default_height(self):
        n = self._packed_at(_DEFAULT_PALLET_HEIGHT)
        self.assertEqual(self._generate(None), (n, n))
        self.assertEqual(self._generate(0), (n, n))  # zapisane ≤ 0 → domyślna
