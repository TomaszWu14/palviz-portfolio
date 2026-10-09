"""Wybrany układ palety (``PalletizationInstruction.selected_layout`` = NAZWA wariantu)
przeżywa przeliczenie instrukcji, nawet gdy silnik nazwie tę samą geometrię inaczej
(np. inna kolejność/deduplikacja wariantów). Wcześniej nazwa znikała z listy i
``get_selected_layout()`` po cichu spadał na ``layouts[0]`` — ręczny wybór przepadał."""
from django.test import TestCase

from ui import models as m
from ui.views.core.packing_core import _layout_signature, _recalculate_instruction


class SelectedLayoutSurvivesRecalcTests(TestCase):
    def setUp(self):
        prod = m.Product.objects.create(code="SEL-1", name="Produkt")
        self.instr = m.PalletizationInstruction.objects.create(
            product=prod, carton_l=40, carton_w=30, carton_h=20,
            unit_weight=2.0, pcs_per_carton=10, demand_pcs=500)
        _recalculate_instruction(self.instr)
        self.instr.refresh_from_db()

    def _pick_non_first_distinct(self):
        first = _layout_signature(self.instr.layouts[0])
        return next(lay for lay in self.instr.layouts[1:] if _layout_signature(lay) != first)

    def test_renamed_geometry_keeps_user_choice(self):
        chosen = self._pick_non_first_distinct()
        real_name, sig = chosen["name"], _layout_signature(chosen)
        # Stan „sprzed zmiany silnika”: ta sama geometria zapisana pod starą nazwą.
        chosen["name"] = "R8_stary_alias"
        self.instr.selected_layout = "R8_stary_alias"
        self.instr.save()

        _recalculate_instruction(self.instr)
        self.instr.refresh_from_db()
        self.assertEqual(self.instr.selected_layout, real_name)
        self.assertEqual(_layout_signature(self.instr.get_selected_layout()), sig)

    def test_existing_name_unchanged(self):
        chosen = self._pick_non_first_distinct()
        self.instr.selected_layout = chosen["name"]
        self.instr.save()
        _recalculate_instruction(self.instr)
        self.instr.refresh_from_db()
        self.assertEqual(self.instr.selected_layout, chosen["name"])

    def test_unknown_geometry_keeps_name_and_falls_back(self):
        # Wybrany układ, którego geometrii nie ma już w nowej liście: nazwa zostaje
        # (nic nie zgadujemy), get_selected_layout jak dotąd → pierwszy wariant.
        self.instr.layouts[1]["name"] = "Zniknięty"
        self.instr.layouts[1]["placements"] = [{"x": 999, "y": 999, "dx": 1, "dy": 1,
                                                "rotated": False}]
        self.instr.selected_layout = "Zniknięty"
        self.instr.save()
        _recalculate_instruction(self.instr)
        self.instr.refresh_from_db()
        self.assertEqual(self.instr.selected_layout, "Zniknięty")
        self.assertEqual(self.instr.get_selected_layout()["name"], self.instr.layouts[0]["name"])
