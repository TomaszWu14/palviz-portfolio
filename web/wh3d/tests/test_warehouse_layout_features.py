"""Elementy hali w module A (Etap 5 slice 2): WarehouseHallFeature z FK=layout,
CRUD na aktywnym layoutcie, render w mapie 3D. Współdzielony model z modułem B."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui import models as m


class LayoutHallFeatureTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.layout = m.WarehouseLayout.objects.create(name="L", is_active=True)
        self.snap = m.WarehouseSnapshot.objects.create(name="Snap")
        self.url = reverse("ui:warehouse_layout_features", args=[self.snap.pk])
        self.detail_url = reverse("ui:warehouse_map_detail", args=[self.snap.pk])

    def _post(self, rows, deleted_ids=""):
        data = {"deleted_ids": deleted_ids}
        keys = ["row_id", "kind", "label", "zone_code", "x_m", "y_m",
                "width_m", "depth_m", "angle_deg", "color_hex", "notes"]
        for k in keys:
            data[k] = [str(r.get(k, "")) for r in rows]
        return self.client.post(self.url, data, follow=True)

    def test_model_layout_fk(self):
        f = m.WarehouseHallFeature.objects.create(layout=self.layout, kind="dock", label="Dok 1")
        self.assertIsNone(f.model_id)          # model FK nullable
        self.assertEqual(f.layout_id, self.layout.pk)
        self.assertEqual(self.layout.features.count(), 1)

    def test_get_200(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Elementy hali")

    def test_create_on_layout(self):
        self._post([{"kind": "returns", "label": "Zwroty", "zone_code": "zwroty",
                     "x_m": 3, "y_m": 4, "width_m": 6, "depth_m": 5}])
        f = m.WarehouseHallFeature.objects.get(layout=self.layout)
        self.assertEqual(f.kind, "returns")
        self.assertEqual(f.zone_code, "ZWROTY")
        self.assertEqual((f.x_m, f.width_m), (3, 6))

    def test_delete(self):
        f = m.WarehouseHallFeature.objects.create(layout=self.layout, kind="gate", label="B")
        self._post([], deleted_ids=str(f.pk))
        self.assertFalse(m.WarehouseHallFeature.objects.filter(pk=f.pk).exists())

    def test_no_active_layout_redirects(self):
        self.layout.is_active = False
        self.layout.save()
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)

    def test_detail_renders_features(self):
        m.WarehouseHallFeature.objects.create(layout=self.layout, kind="dock", label="Dok TEST")
        r = self.client.get(self.detail_url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Dok TEST")
        self.assertContains(r, "id_hall_features")

    def test_detail_no_features_ok(self):
        r = self.client.get(self.detail_url)
        self.assertEqual(r.status_code, 200)   # pusty katalog elementów działa


class ModelHallFeatureStillWorksTests(TestCase):
    """Regresja ETAP 2: elementy hali modułu B (FK=model) nadal działają."""
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.wm = m.WarehouseModel.objects.create(name="M")

    def test_model_features_get_200(self):
        r = self.client.get(reverse("ui:warehouse_model_features", args=[self.wm.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Elementy hali")

    def test_model_feature_create(self):
        f = m.WarehouseHallFeature.objects.create(model=self.wm, kind="leader", label="L")
        self.assertIsNone(f.layout_id)
        self.assertEqual(self.wm.features.count(), 1)


class HallFeatureConstraintTests(TestCase):
    """CheckConstraint: dokładnie jeden FK (model XOR layout) — review: brakujący inwariant."""
    def test_neither_owner_rejected(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                m.WarehouseHallFeature.objects.create(kind="dock", label="sierota")

    def test_both_owners_rejected(self):
        from django.db import IntegrityError, transaction
        wm = m.WarehouseModel.objects.create(name="M")
        layout = m.WarehouseLayout.objects.create(name="L")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                m.WarehouseHallFeature.objects.create(model=wm, layout=layout, kind="dock")
