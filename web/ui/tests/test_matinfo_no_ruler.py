"""MatInfo: karty hierarchii renderują się bez linijki wymiarowej („N cm”) na grafice 3D.
Flaga `noRuler` jest w three_data każdego poziomu z build_hierarchy (oba ekrany MatInfo);
renderer three.js pomija linijkę, gdy jest ustawiona."""
import json

from django.test import TestCase

from ui.models import Product, PalletizationInstruction, Carton
from ui.hierarchy import build_hierarchy


class MatInfoNoRulerTests(TestCase):
    def _product(self):
        carton = Carton.objects.create(name="K", length_cm=40, width_cm=30, height_cm=25,
                                       unit_weight_kg=0.5, pieces_per_carton=10)
        p = Product.objects.create(code="DMOM10001", name="Rękawice nitrylowe M",
                                   unit_length_cm=10, unit_width_cm=8, unit_height_cm=5)
        PalletizationInstruction.objects.create(
            product=p, version=1, is_active=True, unit_weight=0.5, pcs_per_carton=10,
            carton=carton, carton_l=40, carton_w=30, carton_h=25,
            pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15,
            max_height_total_cm=200)
        return p

    def test_three_data_has_noruler_flag(self):
        levels = build_hierarchy(self._product())["levels"]
        self.assertTrue(levels)
        for lv in levels:
            td = json.loads(lv["three_data"])
            self.assertTrue(td.get("noRuler"), f"poziom {lv['key']} bez noRuler")
