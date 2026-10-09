"""Generator hali od parametrów (plan 2026-10-02, etap 1): pojemność, geometria, zapis modelu."""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from ui.models import WarehouseModel
from wh3d.design_generator import PRESET, VNA_AISLE, generate, vna_levels


def _rect(r):
    w = r["n_bays"] * r["bay_width_cm"] / 100
    return r["x_m"], r["y_m"], r["x_m"] + w, r["y_m"] + r["depth_cm"] / 100


class GeneratorTests(SimpleTestCase):
    def setUp(self):
        self.g = generate()

    def test_levels_from_height(self):
        self.assertEqual(vna_levels(16, 2.35), 5)      # 4 × 2,6 m + 2,35 = 12,75 m ≤ 15 m
        self.assertEqual(vna_levels(17.5, 2.35), 6)
        self.assertEqual(vna_levels(3, 2.35), 0)

    def test_capacity_meets_targets(self):
        s = self.g["summary"]
        self.assertGreaterEqual(s["pallet_positions"], PRESET["pallet_positions"])
        self.assertGreaterEqual(s["carton_locations"], PRESET["carton_locations"])
        self.assertLess(s["pallet_positions"], PRESET["pallet_positions"] * 1.05)   # bez rozrzutności
        self.assertEqual(s["docks_in"], 7)
        self.assertEqual(s["docks_out"], 4)

    def test_hall_size_and_aspect(self):
        s = self.g["summary"]
        self.assertAlmostEqual(s["floor_w"] / s["floor_d"], PRESET["aspect"], delta=0.1)
        self.assertTrue(50_000 < s["area_m2"] < 60_000, s["area_m2"])   # plan: ~54–57 tys. m²

    def test_racks_inside_hall_and_not_overlapping(self):
        W, D = self.g["floor"]["width"], self.g["floor"]["depth"]
        rects = [_rect(r) for r in self.g["racks"]]
        for x0, y0, x1, y1 in rects:
            self.assertTrue(0 <= x0 and 0 <= y0 and x1 <= W and y1 <= D)
        rects.sort(key=lambda r: (r[0], r[1]))
        for a, b in zip(rects, rects[1:], strict=False):
            if a[0] == b[0]:                                # ten sam blok, kolejne rzędy w Y
                self.assertGreaterEqual(b[1], a[3] - 1e-6)

    def test_vna_aisles_wide_enough(self):
        ys = sorted((r["y_m"], r["y_m"] + r["depth_cm"] / 100) for r in self.g["racks"] if r["zone"] == "V")
        gaps = [b[0] - a[1] for a, b in zip(ys, ys[1:], strict=False)]
        self.assertTrue(all(g < 0.2 or g >= VNA_AISLE - 1e-6 for g in gaps))
        self.assertEqual(sum(g >= VNA_AISLE - 1e-6 for g in gaps), self.g["summary"]["vna_aisles"] - 2)

    def test_docks_on_opposite_walls(self):
        W = self.g["floor"]["width"]
        docks = [f for f in self.g["features"] if f["kind"] in ("dock", "gate")]
        left = [f for f in docks if f["x_m"] == 0]
        right = [f for f in docks if f["x_m"] + f["width_m"] == W]
        self.assertEqual((len(left), len(right)), (7, 4))

    def test_pallet_too_tall_raises(self):
        with self.assertRaises(ValueError):
            generate(clear_height_m=3)


class GeneratorViewTests(TestCase):
    URL = "/magazyn/model/generator/"

    def setUp(self):
        get_user_model().objects.create_superuser(username="g", password="x")
        self.client.post("/login/", {"username": "g", "password": "x"})

    def _post(self, **extra):
        data = {"name": "Nowy", **PRESET, **extra}
        return self.client.post(self.URL, data)

    def test_get_shows_preset_summary(self):
        r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Generator hali")
        self.assertContains(r, "Lokalizacje kartonowe K1")

    def test_preview_does_not_save(self):
        r = self._post(preview="1", pallet_positions=50_000)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(WarehouseModel.objects.exists())

    def test_create_saves_model_and_opens_3d(self):
        r = self._post(create="1", pallet_positions=5_000, carton_locations=1_000)
        wm = WarehouseModel.objects.get()
        self.assertRedirects(r, f"/magazyn/model/{wm.pk}/view/", fetch_redirect_response=False)
        self.assertEqual(set(wm.racks.values_list("zone", flat=True)), {"V", "K1"})
        self.assertTrue(wm.features.filter(kind="dock").exists())
        self.assertEqual(self.client.get(f"/magazyn/model/{wm.pk}/view/").status_code, 200)

    def test_invalid_input_shows_errors(self):
        r = self._post(create="1", pallet_positions=10)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(WarehouseModel.objects.exists())
        self.assertContains(r, "error-list")
