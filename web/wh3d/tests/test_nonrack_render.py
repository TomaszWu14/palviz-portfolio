"""Etap 5 slice 3: lokalizacje nietypowe (nonrack) dopasowane po zone_code do strefy
block_zone/returns → sloty ze stanami do renderu siatki wewnątrz strefy (bezstratność 3D)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui import models as m
from wh3d.views.warehouse_map import _hall_features_data


class HallSlotsUnitTests(TestCase):
    def _rows(self):
        return [
            {"location_code": "B0-01-300A", "zone": "B0", "is_empty": False,
             "blocked_pick": False, "blocked_put": False},                      # regał — pomijany
            {"location_code": "ZWROTY-01", "zone": "", "is_empty": False,
             "blocked_pick": False, "blocked_put": False},                      # stan 1
            {"location_code": "ZWROTY-02", "zone": "", "is_empty": True,
             "blocked_pick": False, "blocked_put": False},                      # stan 0
            {"location_code": "ZWROTY-03", "zone": "", "is_empty": False,
             "blocked_pick": True, "blocked_put": False},                       # stan 2
        ]

    def test_zone_gets_matched_slots(self):
        layout = m.WarehouseLayout.objects.create(name="L", is_active=True)
        feat = m.WarehouseHallFeature.objects.create(
            layout=layout, kind="returns", label="Zwroty", zone_code="ZWROTY",
            x_m=0, y_m=0, width_m=4, depth_m=3)
        data = _hall_features_data([feat], self._rows(), {"ZWROTY-01": 5})
        d = data[0]
        self.assertEqual(sorted(d["slots"]), [0, 1, 2])   # 3 dopasowane, regał pominięty
        self.assertEqual(d["occupied"], 2)                # stany 1 i 2
        self.assertEqual(d["hu"], 5)

    def test_non_zone_feature_has_no_slots(self):
        layout = m.WarehouseLayout.objects.create(name="L", is_active=True)
        feat = m.WarehouseHallFeature.objects.create(layout=layout, kind="dock", label="Dok")
        data = _hall_features_data([feat], self._rows(), {})
        self.assertNotIn("slots", data[0])                # dok bez zone_code = brak slotów

    def test_no_rows_no_slots(self):
        layout = m.WarehouseLayout.objects.create(name="L", is_active=True)
        feat = m.WarehouseHallFeature.objects.create(
            layout=layout, kind="block_zone", zone_code="X")
        data = _hall_features_data([feat])                # bez rows_list
        self.assertNotIn("slots", data[0])


class HallSlotsRenderTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.layout = m.WarehouseLayout.objects.create(name="L", is_active=True)
        self.snap = m.WarehouseSnapshot.objects.create(name="Snap")
        m.WarehouseSnapshotRow.objects.create(
            snapshot=self.snap, location_code="ZWROTY-01", zone="", stack="",
            aisle="01", level=1, is_empty=False)
        m.WarehouseHallFeature.objects.create(
            layout=self.layout, kind="returns", label="Zwroty", zone_code="ZWROTY",
            width_m=4, depth_m=3)

    def test_detail_renders_zone_with_slots(self):
        r = self.client.get(reverse("ui:warehouse_map_detail", args=[self.snap.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '"slots"')     # sloty w danych elementów
        self.assertContains(r, "Zwroty")
