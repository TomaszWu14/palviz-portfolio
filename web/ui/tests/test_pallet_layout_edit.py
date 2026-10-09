"""Ręczny układ palety (custom_layers) jako źródło prawdy get_selected_layout,
flaga nieaktualności przy przeliczeniu, reset i seedowanie edytora z układu silnika."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui import models as m
from ui.views.core.packing_core import _recalculate_instruction

CUSTOM = [
    {"layer_idx": 0, "placements": [
        {"x": 0, "y": 0, "w": 40, "d": 30, "h": 20, "orient": "base"},
        {"x": 40, "y": 0, "w": 40, "d": 30, "h": 20, "orient": "base"},
    ]},
    {"layer_idx": 1, "placements": [
        {"x": 0, "y": 0, "w": 40, "d": 30, "h": 20, "orient": "base"},
    ]},
]


class PalletLayoutEditTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser(username="b", password="x")
        self.client.force_login(user)
        self.prod = m.Product.objects.create(code="P1", name="Produkt")
        self.instr = m.PalletizationInstruction.objects.create(
            product=self.prod, carton_l=40, carton_w=30, carton_h=20,
            unit_weight=2.0, pcs_per_carton=10, demand_pcs=500)

    def test_custom_layout_wins_and_counts_placements(self):
        # Bez custom → engine (albo None); z custom → adapter z pierwszeństwem.
        self.instr.layouts = [{"name": "grid", "cartons_per_pallet": 99, "placements": []}]
        self.instr.selected_layout = "grid"
        self.instr.custom_layers = CUSTOM
        self.instr.save()
        layout = self.instr.get_selected_layout()
        self.assertEqual(layout["source"], "custom")
        # 2 + 1 = 3 realnie ułożonych kartonów, NIE engine'owe 99.
        self.assertEqual(layout["cartons_per_pallet"], 3)
        self.assertEqual(layout["layers_used"], 2)
        # Placement niesie oba schematy + 1-indeksowany layer.
        p0 = layout["placements"][0]
        self.assertEqual((p0["dx"], p0["dy"]), (40, 30))   # dla palviz-three.js
        self.assertEqual((p0["w"], p0["d"]), (40, 30))     # dla edytora/plotly
        self.assertEqual(p0["layer"], 1)
        self.assertEqual(layout["placements"][-1]["layer"], 2)

    def test_recalc_marks_custom_stale_not_deleted(self):
        self.instr.custom_layers = CUSTOM
        self.instr.save()
        _recalculate_instruction(self.instr)
        self.instr.refresh_from_db()
        self.assertTrue(self.instr.custom_layout_stale)      # oznaczony
        self.assertEqual(len(self.instr.custom_layers), 2)   # ale NIE skasowany

    def test_save_clears_stale(self):
        self.instr.custom_layers = CUSTOM
        self.instr.custom_layout_stale = True
        self.instr.save()
        r = self.client.post(
            reverse("ui:pallet_custom_save", kwargs={"pk": self.instr.pk}),
            data=json.dumps({"layers": CUSTOM}), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.instr.refresh_from_db()
        self.assertFalse(self.instr.custom_layout_stale)

    def test_reset_drops_custom(self):
        self.instr.custom_layers = CUSTOM
        self.instr.custom_layout_stale = True
        self.instr.save()
        r = self.client.post(reverse("ui:pallet_custom_reset", kwargs={"pk": self.instr.pk}))
        self.assertEqual(r.status_code, 302)
        self.instr.refresh_from_db()
        self.assertEqual(self.instr.custom_layers, [])
        self.assertFalse(self.instr.custom_layout_stale)

    def test_editor_embeds_parseable_json_not_html_escaped(self):
        # Guard: dane instrukcji muszą być osadzone jako PARSOWALNY JSON (json_script),
        # nie autoescape'owane &quot; — inaczej JSON.parse w edytorze pada i nie wczytuje stanu.
        self.instr.custom_layers = CUSTOM
        self.instr.save()
        r = self.client.get(reverse("ui:pallet_custom_editor", kwargs={"pk": self.instr.pk}))
        html = r.content.decode()
        block = html.split('id="instr-data"', 1)[1].split(">", 1)[1].split("</script>", 1)[0]
        self.assertNotIn("&quot;", block)                 # nie HTML-escaped
        data = json.loads(block)                          # PARSOWALNY (nie rzuci)
        self.assertEqual(data["pk"], self.instr.pk)
        self.assertEqual(len(data["custom_layers"]), 2)

    def test_editor_seeds_from_engine_when_no_custom(self):
        self.instr.layouts = [{
            "name": "grid", "layers_used": 3, "cartons_per_pallet": 6,
            "placements": [{"x": 0, "y": 0, "dx": 40, "dy": 30, "rotated": False},
                           {"x": 40, "y": 0, "dx": 40, "dy": 30, "rotated": True}],
        }]
        self.instr.selected_layout = "grid"
        self.instr.save()
        r = self.client.get(reverse("ui:pallet_custom_editor", kwargs={"pk": self.instr.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["is_seeded"])
        seeded = r.context["instr_data"]["custom_layers"]
        self.assertEqual(len(seeded), 3)                       # layers_used
        self.assertEqual(len(seeded[0]["placements"]), 2)
        self.assertEqual(seeded[0]["placements"][0]["w"], 40)  # dx→w
        self.assertEqual(seeded[0]["placements"][1]["orient"], "rotated")
