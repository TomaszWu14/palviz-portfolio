"""Elementy hali w Modelu magazynu: CRUD (dodawanie/edycja/usuwanie wierszy przez
równoległe listy + deleted_ids) oraz render etykiet w wizualizacji."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui.models import WarehouseModel, WarehouseHallFeature


class WarehouseHallFeatureTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.wm = WarehouseModel.objects.create(name="M", floor_width_m=50, floor_depth_m=30)
        self.url = reverse("ui:warehouse_model_features", args=[self.wm.pk])
        self.view_url = reverse("ui:warehouse_model_view", args=[self.wm.pk])

    def _post(self, rows, deleted_ids=""):
        """rows: lista dictów pól równoległych → payload z listami."""
        data = {"deleted_ids": deleted_ids}
        keys = ["row_id", "kind", "label", "zone_code", "x_m", "y_m",
                "width_m", "depth_m", "angle_deg", "color_hex", "notes"]
        for k in keys:
            data[k] = [str(r.get(k, "")) for r in rows]
        return self.client.post(self.url, data, follow=True)

    def test_get_renders_form(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Elementy hali")

    def test_create_feature(self):
        self._post([{"kind": "returns", "label": "Strefa zwrotów", "zone_code": "zwroty",
                     "x_m": 3, "y_m": 4, "width_m": 6, "depth_m": 5, "angle_deg": 90}])
        f = WarehouseHallFeature.objects.get(model=self.wm)
        self.assertEqual(f.kind, "returns")
        self.assertEqual(f.label, "Strefa zwrotów")
        self.assertEqual(f.zone_code, "ZWROTY")          # .upper()
        self.assertEqual((f.x_m, f.y_m, f.width_m, f.depth_m, f.angle_deg), (3, 4, 6, 5, 90))

    def test_edit_feature(self):
        f = WarehouseHallFeature.objects.create(model=self.wm, kind="dock", label="Dok 1", width_m=2)
        self._post([{"row_id": f.pk, "kind": "gate", "label": "Brama A",
                     "width_m": 4, "depth_m": 3}])
        f.refresh_from_db()
        self.assertEqual(f.kind, "gate")
        self.assertEqual(f.label, "Brama A")
        self.assertEqual(f.width_m, 4)
        self.assertEqual(WarehouseHallFeature.objects.filter(model=self.wm).count(), 1)  # brak duplikatu

    def test_delete_feature(self):
        f = WarehouseHallFeature.objects.create(model=self.wm, kind="leader", label="Lider")
        self._post([], deleted_ids=str(f.pk))
        self.assertFalse(WarehouseHallFeature.objects.filter(pk=f.pk).exists())

    def test_invalid_kind_falls_back_to_other(self):
        self._post([{"kind": "nonsense", "label": "X"}])
        f = WarehouseHallFeature.objects.get(model=self.wm)
        self.assertEqual(f.kind, "other")

    def test_view_contains_feature_labels(self):
        WarehouseHallFeature.objects.create(model=self.wm, kind="returns", label="Zwroty TEST")
        r = self.client.get(self.view_url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Zwroty TEST")

    def test_view_renders_without_features(self):
        r = self.client.get(self.view_url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "FEATURES_JSON")  # stała JS zawsze obecna (pusta lista)
