"""Dwie zmiany UX kontroli HU:
  1) MATinfo pokazuje max 4 poziomy — poziom „Opakowanie handlowe" (sales_unit) jest
     pominięty nawet gdy InnerPack ma wypełnione wymiary sprzedażowe.
  2) Kafle liczenia: domyślnie tylko jednostki POBRANE; reszta pod „inne"; brak danych
     o pobraniu → wszystkie z przelicznikiem widoczne."""
from types import SimpleNamespace

from django.test import TestCase

from ui.hierarchy import build_hierarchy
from ui.models import Product, PalletizationInstruction, Carton, InnerPack
from huctl.views.hu_control import _count_tiles


class MatinfoCapsAtFourLevels(TestCase):
    def test_sales_unit_level_dropped(self):
        ip = InnerPack.objects.create(
            name="OPZ", length_cm=30, width_cm=20, height_cm=15, units_per_pack=6,
            sales_unit_l_cm=15, sales_unit_w_cm=12, sales_unit_h_cm=2,
            sales_unit_ean="590123", sales_units_per_pack=10)
        c = Carton.objects.create(name="K", length_cm=40, width_cm=30, height_cm=25,
                                  unit_weight_kg=0.5, pieces_per_carton=12, inner_pack=ip)
        p = Product.objects.create(code="TRSU", name="X",
                                   unit_length_cm=10, unit_width_cm=8, unit_height_cm=5)
        PalletizationInstruction.objects.create(
            product=p, version=1, is_active=True, unit_weight=0.5, pcs_per_carton=12,
            carton=c, inner_pack=ip, pcs_per_inner_pack=6, carton_l=40, carton_w=30, carton_h=25)
        keys = [l["key"] for l in build_hierarchy(p)["levels"]]
        self.assertNotIn("sales_unit", keys)
        self.assertLessEqual(len(keys), 4)


class CountTilesShowPickedOnly(TestCase):
    def _item(self, picked):
        return SimpleNamespace(
            factors={"base_unit": "OP", "opz": None, "kar": 10.0, "pal": 300.0},
            picked_units=picked, controlled=False, counted_qty=None, confirm_units={})

    def test_only_picked_shown_rest_hidden(self):
        shown, hidden = _count_tiles(self._item({"base", "kar"}))
        self.assertEqual({t["key"] for t in shown}, {"base", "kar"})
        self.assertEqual({t["key"] for t in hidden}, {"pal"})  # opz bez przelicznika → pominięty

    def test_no_pick_data_shows_all_with_factor(self):
        shown, hidden = _count_tiles(self._item(set()))
        self.assertEqual({t["key"] for t in shown}, {"base", "kar", "pal"})
        self.assertEqual(hidden, [])
