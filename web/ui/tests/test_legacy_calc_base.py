"""Migracja legacy szablonów paletyzacji (saved_list/batch_detail/pallet_detail)
na ui/base: branding {{ app_name }}, wspólny layout, {% url %} zamiast twardych
ścieżek (audyt poza-top10)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from ui import models as m


class LegacyCalcBaseTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.batch = m.Batch.objects.create(name="B1")
        self.pal = m.Palletization.objects.create(
            batch=self.batch, sku="P1", carton_l=40, carton_w=30, carton_h=20,
            unit_weight=2.0, pcs_per_carton=10, demand_pcs=500)

    def _assert_migrated(self, html):
        self.assertNotIn("Paletyzacja PRO", html)      # stary branding zniknął
        self.assertNotIn("background: #111", html)     # stary ciemny topbar zniknął
        self.assertIn("topnav", html)                  # wspólny layout GROOVE (base.html)

    def test_saved_list_migrated(self):
        r = self.client.get(reverse("ui:saved_list"))
        self.assertEqual(r.status_code, 200)
        self._assert_migrated(r.content.decode())
        # link do batcha przez {% url %} (poprawna ścieżka /planner/calc/saved/…)
        self.assertContains(r, reverse("ui:batch_detail", args=[self.batch.pk]))

    def test_batch_detail_migrated(self):
        r = self.client.get(reverse("ui:batch_detail", args=[self.batch.pk]))
        self.assertEqual(r.status_code, 200)
        self._assert_migrated(r.content.decode())

    def test_pallet_detail_migrated(self):
        r = self.client.get(reverse("ui:pallet_detail", args=[self.pal.pk]))
        self.assertEqual(r.status_code, 200)
        self._assert_migrated(r.content.decode())
