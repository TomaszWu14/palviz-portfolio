"""R2: lokalizacje mapy 3D dociągane fetch-em (?fmt=json) zamiast inline w HTML —
dokument lekki także przy ~37k lokalizacji z EWM."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui import models as m


class MapLocationsJsonTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="mapj", password="x")
        self.client.post("/login/", {"username": "mapj", "password": "x"})
        self.snap = m.WarehouseSnapshot.objects.create(name="SnapJ")
        m.WarehouseSnapshotRow.objects.create(
            snapshot=self.snap, location_code="B0-01-300A", zone="B0", aisle="01",
            stack="300", col_code="A", level=1, is_empty=True)

    def test_html_has_no_inline_locations_but_fetch_hook(self):
        r = self.client.get(reverse("ui:warehouse_map_detail", args=[self.snap.pk]))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertNotIn('id="id_locs"', body)             # koniec 37k inline w HTML
        self.assertIn("fmt=json", body)                    # szablon dociąga fetch-em

    def test_json_mode_returns_locations(self):
        r = self.client.get(reverse("ui:warehouse_map_detail", args=[self.snap.pk]),
                            {"fmt": "json"})
        self.assertEqual(r["Content-Type"], "application/json")
        locs = r.json()["locs"]
        self.assertEqual(len(locs), 1)
        self.assertIn("B0-01-300A", locs[0])               # loc_code w rekordzie
