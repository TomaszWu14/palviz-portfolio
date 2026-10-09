"""Strefy nietypowe (Etap 5, slice 1): lokalizacje spoza wzorca regału (np. ZWROTY-01)
grupowane po prefiksie z zajętością zamiast samego licznika — bezstratność w panelu."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui import models as m
from wh3d.views.warehouse_map import _special_zones


class SpecialZonesUnitTests(TestCase):
    def _rows(self):
        return [
            # regałowe — pomijane
            {"location_code": "B0-01-300A", "zone": "B0", "is_empty": False,
             "blocked_pick": False, "blocked_put": False},
            # nonrack — strefa ZWROTY
            {"location_code": "ZWROTY-01", "zone": "", "is_empty": False,
             "blocked_pick": False, "blocked_put": False},
            {"location_code": "ZWROTY-02", "zone": "", "is_empty": True,
             "blocked_pick": False, "blocked_put": False},
            {"location_code": "BLOK-1", "zone": "", "is_empty": False,
             "blocked_pick": True, "blocked_put": False},
        ]

    def test_groups_by_prefix_with_occupancy(self):
        zones = _special_zones(self._rows(), {"ZWROTY-01": 2})
        by = {z["prefix"]: z for z in zones}
        self.assertNotIn("B0", by)                 # regałowe pominięte
        self.assertEqual(by["ZWROTY"]["total"], 2)
        self.assertEqual(by["ZWROTY"]["occupied"], 1)
        self.assertEqual(by["ZWROTY"]["hu"], 2)
        self.assertEqual(by["ZWROTY"]["fill_pct"], 50)
        self.assertEqual(by["BLOK"]["blocked"], 1)

    def test_empty_when_all_rack(self):
        rows = [{"location_code": "B0-01-300A", "zone": "B0", "is_empty": True,
                 "blocked_pick": False, "blocked_put": False}]
        self.assertEqual(_special_zones(rows, {}), [])


class SpecialZonesRenderTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.snap = m.WarehouseSnapshot.objects.create(name="Snap")
        m.WarehouseSnapshotRow.objects.create(
            snapshot=self.snap, location_code="ZWROTY-01", zone="", stack="",
            aisle="01", level=1, is_empty=False)
        self.url = reverse("ui:warehouse_map_detail", args=[self.snap.pk])

    def test_detail_lists_special_zone(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Strefy nietypowe")
        self.assertContains(r, "ZWROTY")
