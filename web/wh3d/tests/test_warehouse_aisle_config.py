"""Szerokości alej (Etap 3): rozstaw pz per aleja z konfiguracji, bez regresji dla
snapshotów bez konfiguracji; ekran konfiguracji CRUD; aisle_meta niesie width_m."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui import models as m
from ui.views.core.helpers import _stack_base_from_rows, _apply_aisle_widths


class ApplyAisleWidthsTests(TestCase):
    """Etap 3 fix: szerokości alej działają też na gotowym stack_base (ścieżka layout)."""
    def _base(self):
        # klucze 2-elementowe jak layout_base: (aisle, stack)
        return {("01", "300"): (0, 0), ("02", "300"): (0, 2), ("03", "300"): (0, 4)}

    def test_no_widths_identical(self):
        base = self._base()
        self.assertIs(_apply_aisle_widths(base, {}), base)   # regresja: bez zmian

    def test_width_adds_extra_gap_after_aisle(self):
        # aleja 01 step=4 (default 2 → ekstra +2) → rzędy po niej przesunięte o 2.
        out = _apply_aisle_widths(self._base(), {"01": 4})
        self.assertEqual(out[("01", "300")], (0, 0))    # pierwsza bez zmian
        self.assertEqual(out[("02", "300")], (0, 4))    # 2 + ekstra 2
        self.assertEqual(out[("03", "300")], (0, 6))    # 4 + ekstra 2

    def test_step_at_default_no_change(self):
        out = _apply_aisle_widths(self._base(), {"01": 2})   # step == default → 0 ekstra
        self.assertEqual(out, self._base())


def _rows():
    # 3 aleje w strefie B0, po 1 stosie każda → czytelny rozstaw pz.
    return [
        {"zone": "B0", "aisle": "01", "stack": "300", "location_code": "B0-01-300A"},
        {"zone": "B0", "aisle": "02", "stack": "300", "location_code": "B0-02-300A"},
        {"zone": "B0", "aisle": "03", "stack": "300", "location_code": "B0-03-300A"},
    ]


class StackBaseSpacingTests(TestCase):
    def test_no_widths_identical_default_step(self):
        """Bez aisle_widths → stały krok AISLE_STEP=2 (regresja: 0, 2, 4)."""
        base = _stack_base_from_rows(_rows())
        self.assertEqual(base[("B0", "01", "300")][1], 0)
        self.assertEqual(base[("B0", "02", "300")][1], 2)
        self.assertEqual(base[("B0", "03", "300")][1], 4)

    def test_widths_change_pz_spacing(self):
        """aisle_widths → kumulatywny rozstaw wg kroku per aleja."""
        base = _stack_base_from_rows(_rows(), {"01": 5, "02": 3})
        self.assertEqual(base[("B0", "01", "300")][1], 0)   # start
        self.assertEqual(base[("B0", "02", "300")][1], 5)   # +krok alei 01
        self.assertEqual(base[("B0", "03", "300")][1], 8)   # +krok alei 02; 03 default AISLE_STEP


class AisleConfigScreenTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.layout = m.WarehouseLayout.objects.create(name="L", is_active=True)
        self.snap = m.WarehouseSnapshot.objects.create(name="Snap")
        for a in ("01", "02"):
            m.WarehouseSnapshotRow.objects.create(
                snapshot=self.snap, location_code=f"B0-{a}-300A",
                zone="B0", aisle=a, stack="300", col_code="A", level=1, is_empty=False)
        self.url = reverse("ui:warehouse_aisle_config", args=[self.snap.pk])

    def test_get_lists_aisles(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Szerokości")

    def test_post_saves_and_deletes(self):
        self.client.post(self.url, {"width_01": "3.5", "width_02": ""})
        self.assertEqual(m.WarehouseAisleConfig.objects.get(layout=self.layout, aisle="01").width_m, 3.5)
        self.assertFalse(m.WarehouseAisleConfig.objects.filter(aisle="02").exists())
        # Puste = usuń istniejącą konfigurację.
        m.WarehouseAisleConfig.objects.create(layout=self.layout, aisle="02", width_m=2)
        self.client.post(self.url, {"width_01": "3.5", "width_02": ""})
        self.assertFalse(m.WarehouseAisleConfig.objects.filter(aisle="02").exists())

    def test_no_active_layout_redirects(self):
        self.layout.is_active = False
        self.layout.save()
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)


class AisleMetaWidthTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.layout = m.WarehouseLayout.objects.create(name="L", is_active=True)
        self.snap = m.WarehouseSnapshot.objects.create(name="Snap")
        m.WarehouseSnapshotRow.objects.create(
            snapshot=self.snap, location_code="B0-01-300A",
            zone="B0", aisle="01", stack="300", col_code="A", level=1,
            is_empty=False, warehouse_type="RT", capacity_mm=2000)
        # rack type z manip_mm → slot_width_mm > 0 (potrzebne do konwersji metry→pz)
        m.WarehouseRackType.objects.create(code="RT", name="Typ", manip_mm=900)
        m.WarehouseAisleConfig.objects.create(layout=self.layout, aisle="01", width_m=3.0)

    def test_detail_aisle_meta_carries_width_m(self):
        r = self.client.get(reverse("ui:warehouse_map_detail", args=[self.snap.pk]))
        self.assertEqual(r.status_code, 200)
        # aisle_meta trafia do strony jako json_script — width_m alei 01 = 3.0
        self.assertContains(r, '"width_m": 3.0')
