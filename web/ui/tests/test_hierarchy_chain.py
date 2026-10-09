"""Etykiety hierarchii PHV: brak łańcucha przeliczników; jednostka „KAR" (kod SAP)."""
from django.test import TestCase

from ui.hierarchy import build_hierarchy
from ui.models import Product, PalletizationInstruction, Carton


def _product(code="DMOM10001", pcs_per_carton=10, cpp=48):
    p = Product.objects.create(code=code, name="X",
                               unit_length_cm=21, unit_width_cm=12, unit_height_cm=5.5)
    c = Carton.objects.create(name="K", length_cm=29, width_cm=25, height_cm=22,
                              unit_weight_kg=0.5, pieces_per_carton=pcs_per_carton)
    instr = PalletizationInstruction.objects.create(
        product=p, version=1, is_active=True, unit_weight=0.5, pcs_per_carton=pcs_per_carton,
        carton=c, carton_l=29, carton_w=25, carton_h=22,
        pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15,
        max_height_total_cm=215, units_per_piece=100)
    instr.layouts = [{"name": "L", "layers_used": 9, "cartons_per_pallet": cpp,
                      "placements": [{"x": 0, "y": 0, "dx": 29, "dy": 25}]}]
    instr.save()
    return p


class HierarchyLabelTests(TestCase):
    def test_no_chain_key(self):
        self.assertNotIn("chain", build_hierarchy(_product()))

    def test_carton_unit_is_kar(self):
        levels = build_hierarchy(_product(pcs_per_carton=10))["levels"]
        carton = next(l for l in levels if l["key"] == "carton")
        self.assertEqual(carton["qty"], "10 OP / KAR")

    def test_pallet_unit_is_kar(self):
        levels = build_hierarchy(_product(cpp=48))["levels"]
        pallet = next(l for l in levels if l["key"] == "pallet")
        self.assertIn("48 KAR,", pallet["qty"])
        self.assertNotIn("karton", pallet["qty"])
