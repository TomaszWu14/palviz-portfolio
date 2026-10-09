"""On-demand OR-Tools optimal-layer endpoint (opt-in from the result panel)."""
import unittest

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Batch, Palletization

try:
    from ortools.sat.python import cp_model  # noqa: F401
    _HAS_ORTOOLS = True
except Exception:
    _HAS_ORTOOLS = False


def _login(client):
    u = get_user_model().objects.create_user(username="p", password="x")
    u.groups.add(Group.objects.get_or_create(name="Podgląd")[0])  # _planner wymaga roli
    client.force_login(u)
    return u


def _rec():
    b = Batch.objects.create(name="t", pallet_code="EU", pallet_length_cm=120, pallet_width_cm=80,
                             max_height_total_cm=215, pallet_base_height_cm=15, max_weight_kg=1000)
    # 30×40 carton → perfect tiling, optimum = 8 per layer.
    return Palletization.objects.create(
        batch=b, sku="SKU", variant="STD", carton_l=30, carton_w=40, carton_h=20,
        unit_weight=5, pcs_per_carton=1, demand_pcs=100, carton_tare=0,
        layouts=[{"name": "Heur", "cartons_per_layer": 7}], selected_layout="Heur")


@unittest.skipUnless(_HAS_ORTOOLS, "ortools not installed")
class CalcOptimalLayerTests(TestCase):
    def setUp(self):
        _login(self.client)

    def test_optimal_layer_fragment(self):
        rec = _rec()
        resp = self.client.get(reverse("ui:calc_optimal_layer", args=[rec.id]))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("OR-Tools", body)
        # Optimum (8) beats the recorded heuristic (7) → +1 highlighted.
        self.assertIn("Optymalnie: <b>8</b>", body)
        self.assertIn("+1", body)

    def test_requires_login(self):
        self.client.logout()
        rec = _rec()
        resp = self.client.get(reverse("ui:calc_optimal_layer", args=[rec.id]))
        self.assertEqual(resp.status_code, 302)
