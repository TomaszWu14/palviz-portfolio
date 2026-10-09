"""Skrzydło prostopadłe (Etap 4): aleje z kątem 90° trafiają do osobnego regionu podłogi
(_apply_perpendicular_wings), zapis kąta w konfiguracji, kąt w aisle_meta/locs; bez kątów
zero zmian pozycji."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui import models as m
from ui.views.core.helpers import _apply_perpendicular_wings


class PerpendicularWingTests(TestCase):
    def _base(self):
        # 2 aleje główne (01,02) + 1 do obrócenia (55), klucze 3-elementowe.
        return {
            ("B0", "01", "300"): (0, 0),
            ("B0", "02", "300"): (0, 2),
            ("B0", "55", "300"): (0, 4),
        }

    def test_no_angles_identical(self):
        base = self._base()
        out, rotated = _apply_perpendicular_wings(base, {})
        self.assertIs(out, base)          # ten sam obiekt — zero zmian (regresja)
        self.assertEqual(rotated, set())

    def test_rotated_aisle_moved_to_wing_region(self):
        base = self._base()
        out, rotated = _apply_perpendicular_wings(base, {"55": 90})
        self.assertEqual(rotated, {"55"})
        # główne aleje bez zmian
        self.assertEqual(out[("B0", "01", "300")], (0, 0))
        self.assertEqual(out[("B0", "02", "300")], (0, 2))
        # skrzydło przesunięte w prawo (col >> max_col głównego bloku)
        wing_col = out[("B0", "55", "300")][0]
        self.assertGreater(wing_col, 0)

    def test_non_90_angle_ignored(self):
        base = self._base()
        out, rotated = _apply_perpendicular_wings(base, {"55": 45})
        self.assertEqual(rotated, set())   # tylko ~90° tworzy skrzydło
        self.assertIs(out, base)

    def test_two_tuple_keys_layout_path(self):
        # Ścieżka layout: klucze 2-elementowe (aisle, stack) — aleja to element [0].
        base = {("01", "300"): (0, 0), ("55", "300"): (0, 2)}
        out, rotated = _apply_perpendicular_wings(base, {"55": 90})
        self.assertEqual(rotated, {"55"})
        self.assertGreater(out[("55", "300")][0], 0)


class WingConfigAndRenderTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.layout = m.WarehouseLayout.objects.create(name="L", is_active=True)
        self.snap = m.WarehouseSnapshot.objects.create(name="Snap")
        for a in ("01", "55"):
            m.WarehouseSnapshotRow.objects.create(
                snapshot=self.snap, location_code=f"B0-{a}-300A",
                zone="B0", aisle=a, stack="300", col_code="A", level=1,
                is_empty=False, warehouse_type="RT", capacity_mm=2000)
        m.WarehouseRackType.objects.create(code="RT", name="Typ", manip_mm=900)
        self.cfg_url = reverse("ui:warehouse_aisle_config", args=[self.snap.pk])
        self.detail_url = reverse("ui:warehouse_map_detail", args=[self.snap.pk])

    def test_post_saves_angle(self):
        self.client.post(self.cfg_url, {"width_01": "", "angle_01": "0",
                                        "width_55": "", "angle_55": "90"})
        self.assertEqual(m.WarehouseAisleConfig.objects.get(layout=self.layout, aisle="55").angle_deg, 90)
        self.assertFalse(m.WarehouseAisleConfig.objects.filter(aisle="01").exists())  # 0°+puste = brak wiersza

    def test_detail_carries_angle_when_configured(self):
        m.WarehouseAisleConfig.objects.create(layout=self.layout, aisle="55", width_m=2, angle_deg=90)
        r = self.client.get(self.detail_url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '"angle_deg": 90')   # aisle_meta niesie kąt

    def test_detail_no_angle_without_config(self):
        r = self.client.get(self.detail_url)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, '"angle_deg": 90')  # bez konfiguracji render jak dziś
