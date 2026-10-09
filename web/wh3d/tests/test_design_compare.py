"""Porównanie wariantów hali (plan 2026-10-02, etap 7)."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from wh3d.design_compare import capacity, comparison, required_fleet, variant_row
from wh3d.design_generator import generate
from wh3d.tests.test_design_sim import FEATURES, GEN, RACKS, SimulationViewTests, _tasks


class CompareTests(SimpleTestCase):
    def test_capacity_matches_generator(self):
        cap = capacity(RACKS, FEATURES, GEN["floor"])
        self.assertEqual(cap["pallets"], GEN["summary"]["pallet_positions"])
        self.assertEqual(cap["cartons"], GEN["summary"]["carton_locations"])
        self.assertEqual(cap["gates"], GEN["summary"]["docks_in"] + GEN["summary"]["docks_out"])

    def test_required_fleet_settles_on_suggestion(self):
        tasks = _tasks(scale=4)
        result, fleet = required_fleet(tasks, RACKS, FEATURES, {"agv": 1, "kombi": 1, "ept": 1})
        self.assertEqual(fleet, {f["kind"]: f["suggested"] for f in result["fleet"]})
        self.assertTrue(all(f["util_pct"] <= 95 for f in result["fleet"]))

    def test_hall_without_vna_keeps_capacity_only(self):
        low = [dict(r, level_h=1.5, n_levels=3) for r in RACKS if r["zone"] == "V"]
        result, fleet = required_fleet(_tasks(), low, FEATURES, {"agv": 2, "kombi": 2, "ept": 2})
        self.assertIsNone(result)
        row = variant_row(capacity(low, FEATURES, GEN["floor"]), result, fleet)
        self.assertNotIn("km", row)

    def test_best_value_per_row(self):
        table = comparison([{"area_m2": 100, "pallets": 50}, {"area_m2": 80, "pallets": 60}])
        rows = {r["label"]: r for r in table}
        self.assertEqual([c["best"] for c in rows["Powierzchnia hali"]["cells"]], [False, True])
        self.assertEqual([c["best"] for c in rows["Miejsca paletowe"]["cells"]], [False, True])
        self.assertEqual([c["best"] for c in rows["Bramy (doki + bramy)"]["cells"]], [False, False])


class CompareViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        SimulationViewTests.setUpTestData.__func__(cls)
        from wh3d.views.warehouse_generator import _save

        cls.wm2 = _save("Wyższa hala", generate(pallet_positions=6000, carton_locations=3000, clear_height_m=17.5))

    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser(username="c", password="x")
        self.client.post("/login/", {"username": "c", "password": "x"})

    def test_two_variants_side_by_side(self):
        r = self.client.get(f"/magazyn/zadania-ewm/{self.batch.pk}/porownanie/",
                            {"models": [self.wm.pk, self.wm2.pk], "kt": "1,2"})
        self.assertContains(r, "Wymagane: AGV")
        self.assertContains(r, "Wyższa hala")
        area = next(row for row in r.context["table"] if row["label"] == "Powierzchnia hali")
        self.assertEqual(len(area["cells"]), 2)
        self.assertLess(area["cells"][1]["value"], area["cells"][0]["value"])   # 17,5 m → mniejsza hala

    def test_links_from_profile(self):
        r = self.client.get(f"/magazyn/zadania-ewm/{self.batch.pk}/profil/")
        self.assertContains(r, f"/magazyn/zadania-ewm/{self.batch.pk}/porownanie/")
