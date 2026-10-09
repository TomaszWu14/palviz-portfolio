"""Typy regałów A/B/C/D: nośność per poziom (level_weights) — zapis w edytorze,
serializacja do rack_types i etykiety kg w widoku 3D."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui import models as m


class RackTypeWeightsTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})

    def test_model_stores_level_weights(self):
        rt = m.WarehouseRackType.objects.create(code="A", name="Typ A",
                                                level_weights={"1": 1000, "2": 300})
        rt.refresh_from_db()
        self.assertEqual(rt.level_weights, {"1": 1000, "2": 300})

    def test_editor_saves_level_weights(self):
        url = reverse("ui:warehouse_rack_type_new")
        self.client.post(url, {
            "code": "B", "name": "Typ B",
            "level_1_height": "2400", "level_1_weight": "1000",
            "level_2_height": "1200", "level_2_weight": "300",
            "width_mm": "800", "manip_mm": "900", "depth_mm": "1100",
            "max_weight_kg": "1200", "max_volume_m3": "2.5",
            "color_hex": "#f59e0b", "level_cols_json": "{}",
        })
        rt = m.WarehouseRackType.objects.get(code="B")
        self.assertEqual(rt.level_weights, {"1": 1000, "2": 300})
        self.assertEqual(rt.level_heights, {"1": 2400, "2": 1200})

    def test_editor_get_200(self):
        rt = m.WarehouseRackType.objects.create(code="C", name="Typ C",
                                                level_weights={"1": 1000})
        r = self.client.get(reverse("ui:warehouse_rack_type_edit", args=[rt.pk]))
        self.assertEqual(r.status_code, 200)

    def test_detail_serializes_level_weights(self):
        layout = m.WarehouseLayout.objects.create(name="L", is_active=True)  # noqa: F841
        snap = m.WarehouseSnapshot.objects.create(name="Snap")
        m.WarehouseSnapshotRow.objects.create(
            snapshot=snap, location_code="B0-01-300A", zone="B0", aisle="01",
            stack="300", col_code="A", level=1, is_empty=False,
            warehouse_type="A", capacity_mm=2400)
        m.WarehouseRackType.objects.create(code="A", name="Typ A", manip_mm=900,
                                           level_weights={"1": 1000})
        r = self.client.get(reverse("ui:warehouse_map_detail", args=[snap.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "level_weights")   # serializowane do RACK_TYPES
        self.assertContains(r, "1000")            # nośność w danych typu
