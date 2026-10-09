"""Picking a pallet-height scenario for the quote + warehouse build, and clearing it.
The chosen height persists on the shipment so the warehouse knows which version to prepare."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from ui import models as m


class ScenarioSelectTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.ship = m.Shipment.objects.create(name="S-sel")

    def _url(self):
        return reverse("ui:planner_shipment_select_scenario", kwargs={"pk": self.ship.pk})

    def test_select_then_clear(self):
        r = self.client.post(self._url(), {"height": "220", "h1": "1.8", "h2": "2.2", "eff": "80"})
        self.assertEqual(r.status_code, 302)
        self.ship.refresh_from_db()
        self.assertEqual(self.ship.selected_pallet_height_cm, 220)
        # view params are preserved on the redirect
        self.assertIn("h1=1.8", r.url)

        r = self.client.post(self._url(), {"height": "clear"})
        self.assertEqual(r.status_code, 302)
        self.ship.refresh_from_db()
        self.assertIsNone(self.ship.selected_pallet_height_cm)

    def test_get_is_rejected(self):
        self.assertEqual(self.client.get(self._url()).status_code, 405)
