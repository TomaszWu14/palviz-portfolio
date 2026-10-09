"""Packaging optimizer: fill %, overhang tolerance, layout."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from ui.views import _optimize_packaging


class OptimizerTests(TestCase):
    def test_fit_and_fill(self):
        r = _optimize_packaging(40, 30, 22, "EU", load_height_cm=200,
                                max_weight_kg=1000, tolerance_cm=0, carton_weight_kg=10)
        self.assertTrue(r["ok"])
        self.assertGreater(r["cartons_per_pallet"], 0)
        self.assertEqual(r["per_layer"], 8)            # 120x80 / 40x30 rotated = 8
        self.assertGreaterEqual(r["fill_volume"], 90)  # near-perfect tiling
        self.assertEqual(r["overhang_x"], 0)

    def test_tolerance_allows_overhang(self):
        # a 41-wide carton doesn't tile 120 cleanly; tolerance lets one more column overhang
        tight = _optimize_packaging(41, 30, 22, "EU", 200, 1000, 0, 10)
        loose = _optimize_packaging(41, 30, 22, "EU", 200, 1000, 4, 10)
        self.assertGreaterEqual(loose["per_layer"], tight["per_layer"])

    def test_oversized_carton_not_ok(self):
        r = _optimize_packaging(200, 200, 22, "EU", 200, 1000, 1, 10)
        self.assertFalse(r["ok"])

    def test_page_renders(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        r = self.client.get("/planner/optimizer/?carton_l=40&carton_w=30&carton_h=22&pallet=EU"
                            "&max_height_total=200&max_weight=1000&tolerance_cm=1&carton_weight=10")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Wypełnienie objętości")
        self.assertContains(r, "Kartonów / paleta")


from ui.views import _suggest_packaging


class SuggestTests(TestCase):
    def test_suggest_returns_ranked_options(self):
        sg = _suggest_packaging(20, 15, 12, "EU", height_cm=200, max_weight_kg=1000,
                                tolerance_cm=1, unit_weight_kg=0.4, target_pct=95,
                                max_units=48, median_qty=24)
        self.assertTrue(sg)
        self.assertLessEqual(len(sg), 8)
        # every option fits and carries units
        for s in sg:
            self.assertTrue(s["ok"])
            self.assertGreater(s["cartons_per_pallet"], 0)
            self.assertEqual(s["units_per_pallet"], s["cartons_per_pallet"] * s["units_per_carton"])
        # top result is at least as close to target as the rest
        tgt = 95
        self.assertLessEqual(abs(sg[0]["fill_volume"] - tgt), abs(sg[-1]["fill_volume"] - tgt) + 0.01)

    def test_whole_cartons_preferred_on_ties(self):
        # median 24: among equal-fill options, one dividing 24 (whole cartons) should rank first
        sg = _suggest_packaging(20, 15, 12, "EU", 200, 1000, 1, 0.4, 95, 48, median_qty=24)
        self.assertTrue(sg[0]["whole_cartons"])

    def test_suggest_page_renders(self):
        get_user_model().objects.create_superuser(username="s", password="x")
        self.client.post("/login/", {"username": "s", "password": "x"})
        r = self.client.get("/planner/optimizer/?mode=suggest&unit_l=20&unit_w=15&unit_h=12"
                            "&unit_weight=0.4&target_pct=95&max_units=48&median_qty=24"
                            "&pallet=EU&max_height_total=200&max_weight=1000&tolerance_cm=1")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Propozycje opakowania")




class SuggestProductTests(TestCase):
    def test_no_thin_cartons_suggested(self):
        sg = _suggest_packaging(20, 15, 12, "EU", 200, 1000, 1, 0.4, 95, 48, 24)
        for s in sg:
            d = s["carton"]
            self.assertLessEqual(max(d.values()), 6 * min(d.values()))   # not a thin stick


from ui.views import _best_box


class ChainTests(TestCase):
    def test_best_box_is_compact(self):
        d = _best_box(6, 10, 8, 6)
        self.assertEqual(d[0] * d[1] * d[2], 6 * 10 * 8 * 6)        # holds exactly 6 units, no void
        self.assertLessEqual(max(d), 6 * min(d))                    # not a thin stick

    def test_three_level_multiplier(self):
        inner = _best_box(6, 10, 8, 6)
        sg = _suggest_packaging(inner[0], inner[1], inner[2], "EU", 200, 1000, 1, 2.4,
                                95, 24, median_qty=144, units_multiplier=6)
        self.assertTrue(sg)
        for s in sg:
            self.assertEqual(s["products_per_carton"], s["units_per_carton"] * 6)
        # 144 divisible by products_per_carton → whole cartons
        self.assertTrue(any(s["whole_cartons"] for s in sg))

    def test_chain_page_renders(self):
        get_user_model().objects.create_superuser(username="c", password="x")
        self.client.post("/login/", {"username": "c", "password": "x"})
        r = self.client.get("/planner/optimizer/?mode=suggest&unit_l=10&unit_w=8&unit_h=6"
                            "&unit_weight=0.4&products_per_pack=6&target_pct=95&max_units=24"
                            "&median_qty=144&pallet=EU&max_height_total=200&max_weight=1000&tolerance_cm=1")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Łańcuch: produkt")
