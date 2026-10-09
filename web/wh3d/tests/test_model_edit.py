"""Edycja wariantu hali blokami (plan 2026-10-02, etap 4)."""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from ui.models import WarehouseModel
from wh3d.design_generator import generate
from wh3d.model_edit import apply_zone_edit, collisions, fit_floor, zone_summary
from wh3d.tests.test_design_sim import RACKS


def _copy():
    return [dict(r) for r in RACKS]


class ModelEditTests(SimpleTestCase):
    def test_generated_hall_has_no_collisions(self):
        self.assertEqual(collisions(_copy()), [])

    def test_zone_summary(self):
        z = {s["zone"]: s for s in zone_summary(RACKS)}
        self.assertEqual(set(z), {"V", "K1"})
        self.assertEqual(z["V"]["rows"], sum(r["zone"] == "V" for r in RACKS))

    def test_shift_moves_only_that_zone(self):
        racks = _copy()
        changed = apply_zone_edit(racks, "K1", dx=5, dy=-1)
        self.assertTrue(changed and all(r["zone"] == "K1" for r in changed))
        before = {(r["zone"], r["rack_id"]): r for r in RACKS}
        for r in racks:
            b = before[(r["zone"], r["rack_id"])]
            self.assertEqual((r["x"], r["y"]), (round(b["x"] + 5, 2), round(b["y"] - 1, 2)) if r["zone"] == "K1"
                             else (b["x"], b["y"]))

    def test_longer_rows_keep_bay_width_and_can_collide(self):
        racks = _copy()
        v = next(r for r in racks if r["zone"] == "V")
        bay_w = v["width"] / v["n_bays"]
        apply_zone_edit(racks, "V", n_bays=v["n_bays"] + 40)
        self.assertAlmostEqual(v["width"] / v["n_bays"], bay_w, places=3)
        self.assertTrue(collisions(racks))                  # rzędy V wjeżdżają w półki K1

    def test_fit_floor_grows_with_racks(self):
        racks = _copy()
        apply_zone_edit(racks, "K1", dx=50)
        w, _ = fit_floor(racks, 10, 10)
        self.assertGreater(w, max(r["x"] + r["width"] for r in racks))


class VariantEditViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from wh3d.views.warehouse_generator import _save

        cls.wm = _save("Bazowy", generate(pallet_positions=6000, carton_locations=3000))

    def setUp(self):
        get_user_model().objects.create_superuser(username="e", password="x")
        self.client.post("/login/", {"username": "e", "password": "x"})

    def test_copy_keeps_original(self):
        n = self.wm.racks.count()
        r = self.client.post(f"/magazyn/model/{self.wm.pk}/kopia/")
        copy = WarehouseModel.objects.exclude(pk=self.wm.pk).get()
        self.assertRedirects(r, f"/magazyn/model/{copy.pk}/strefy/", fetch_redirect_response=False)
        self.assertEqual((copy.racks.count(), self.wm.racks.count()), (n, n))
        self.assertEqual(copy.features.count(), self.wm.features.count())
        self.assertIn("wariant", copy.name)

    def test_zone_edit_saves_and_deletes(self):
        k1_x = self.wm.racks.filter(zone="K1").first().x_m
        self.assertContains(self.client.get(f"/magazyn/model/{self.wm.pk}/strefy/"), "Edycja stref regałów")
        r = self.client.post(f"/magazyn/model/{self.wm.pk}/strefy/", {
            "dx_K1": "2,5", "dy_K1": "0", "bays_K1": "", "levels_K1": "4",
            "dx_V": "0", "dy_V": "0", "bays_V": "", "levels_V": "", "del_V": "1",
            "floor_w": "", "floor_d": ""})
        self.assertRedirects(r, f"/magazyn/model/{self.wm.pk}/view/", fetch_redirect_response=False)
        self.assertFalse(self.wm.racks.filter(zone="V").exists())
        k1 = self.wm.racks.filter(zone="K1")
        self.assertEqual({x.n_levels for x in k1}, {4})
        self.assertAlmostEqual(k1.first().x_m, k1_x + 2.5)

    def test_negative_shift_is_rejected(self):
        before = list(self.wm.racks.values_list("x_m", flat=True))
        self.client.post(f"/magazyn/model/{self.wm.pk}/strefy/", {"dx_V": "-9999", "dx_K1": "0"})
        self.client.post(f"/magazyn/model/{self.wm.pk}/strefy/", {"dx_V": "-400", "dx_K1": "0"})
        self.assertEqual(list(self.wm.racks.values_list("x_m", flat=True)), before)

    def test_view_has_variant_buttons(self):
        r = self.client.get(f"/magazyn/model/{self.wm.pk}/view/")
        self.assertContains(r, "Kopiuj jako wariant")
        self.assertContains(r, f"/magazyn/model/{self.wm.pk}/strefy/")
