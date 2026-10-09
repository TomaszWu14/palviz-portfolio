"""Symulacja dnia projektowego na hali z generatora (plan 2026-10-02, etap 3a)."""
import random
from datetime import datetime, timedelta
from datetime import timezone as dt_tz

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from wh3d.design_generator import generate
from wh3d.design_sim import HORIZON_S, Layout, _manh, load_day_tasks, scale_tasks, simulate
from wh3d.models_tasks import WarehouseTask, WarehouseTaskBatch

GEN = generate(pallet_positions=6000, carton_locations=3000)
RACKS = [{"id": i, "zone": r["zone"], "rack_id": r["rack_id"], "x": r["x_m"], "y": r["y_m"], "angle": 0,
          "width": r["n_bays"] * r["bay_width_cm"] / 100, "depth": r["depth_cm"] / 100,
          "level_h": r["level_height_cm"] / 100, "n_bays": r["n_bays"], "n_levels": r["n_levels"]}
         for i, r in enumerate(GEN["racks"])]
FEATURES = [{"kind": f["kind"], "label": f["label"], "x": f["x_m"], "y": f["y_m"], "width": f["width_m"],
             "depth": f["depth_m"], "angle": 0} for f in GEN["features"]]


def _tasks(seed=1, scale=1):
    rng = random.Random(seed)
    out = []
    for kind, n in (("putaway", 120), ("outbound", 80), ("picking", 600), ("replenishment", 30), ("move", 10)):
        for _ in range(n * scale):
            doc = f"D{rng.randint(1, 150)}" if kind == "picking" else ""
            out.append((rng.uniform(0, 14 * 3600), kind, f"M{int(rng.paretovariate(1.2)) % 400}", doc))
    return sorted(out)


def _fleet(r, kind):
    return next(f for f in r["fleet"] if f["kind"] == kind)


class SimulationTests(SimpleTestCase):
    def test_every_task_is_served(self):
        tasks = _tasks()
        r = simulate(tasks, RACKS, FEATURES, {"agv": 3, "kombi": 3, "ept": 3})
        self.assertEqual(sum(w["n"] for w in r["waits"].values()), len(tasks))
        self.assertEqual(sum(h["done"] for h in r["hours"]), len(tasks))

    def test_bigger_fleet_waits_less(self):
        tasks = _tasks(scale=4)
        small = simulate(tasks, RACKS, FEATURES, {"agv": 1, "kombi": 1, "ept": 1})
        big = simulate(tasks, RACKS, FEATURES, {"agv": 6, "kombi": 6, "ept": 6})
        for kind in ("putaway", "picking"):
            self.assertLess(big["waits"][kind]["p95_min"], small["waits"][kind]["p95_min"])
        self.assertLessEqual(big["late"], small["late"])

    def test_suggested_fleet_is_not_overloaded(self):
        tasks = _tasks(scale=4)
        first = simulate(tasks, RACKS, FEATURES, {"agv": 1, "kombi": 1, "ept": 1})
        fleet = {f["kind"]: f["suggested"] for f in first["fleet"]}
        again = simulate(tasks, RACKS, FEATURES, fleet)
        self.assertTrue(all(f["util_pct"] <= 95 for f in again["fleet"]), again["fleet"])

    def test_multiplier_scales_task_count(self):
        tasks = _tasks()
        self.assertEqual(len(scale_tasks(tasks, 2.0)), 2 * len(tasks))
        half = len(scale_tasks(tasks, 0.5))
        self.assertAlmostEqual(half / len(tasks), 0.5, delta=0.08)
        r = simulate(tasks, RACKS, FEATURES, {"agv": 3, "kombi": 3, "ept": 3}, multiplier=1.5)
        self.assertGreater(sum(r["tasks"].values()), len(tasks))

    def test_abc_puts_frequent_material_closer_to_packing(self):
        lay = Layout(RACKS, FEATURES)
        dist = lambda rank: min(_manh(lay.shelf(rank), p) for p in lay.pack)  # noqa: E731
        self.assertLess(dist(0), dist(len(lay.shelf_slots) - 1))
        rng = random.Random(0)
        a = [lay.vna_slot(0.0, rng, lay.in_points[0])[1] for _ in range(200)]
        c = [lay.vna_slot(0.9, rng, lay.in_points[0])[1] for _ in range(200)]
        self.assertLess(sum(a) / len(a), sum(c) / len(c))        # A niżej niż C

    def test_agents_busy_within_physical_limits(self):
        r = simulate(_tasks(), RACKS, FEATURES, {"agv": 3, "kombi": 3, "ept": 3})
        for f in r["fleet"]:
            self.assertGreater(f["busy_h"], 0)
            self.assertGreater(f["km"], 0)
        self.assertEqual(r["horizon_h"], HORIZON_S / 3600)

    def test_hall_without_vna_is_not_simulated(self):
        low = [dict(r, level_h=1.5, n_levels=3) for r in RACKS if r["zone"] == "V"]
        self.assertIsNone(simulate(_tasks(), low, FEATURES, {"agv": 1, "kombi": 1, "ept": 1}))


class SimulationViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from wh3d.views.warehouse_generator import _save

        cls.wm = _save("Nowy", GEN)
        cls.batch = WarehouseTaskBatch.objects.create(name="Wrzesień", status="done")
        t0 = datetime(2026, 9, 1, 6, 0, tzinfo=dt_tz.utc)                     # 08:00 w Warszawie
        WarehouseTask.objects.bulk_create([
            WarehouseTask(batch=cls.batch, kind=k, material=f"M{i % 7}", document=f"D{i % 5}",
                          confirmed_at=t0 + timedelta(minutes=3 * i))
            for i, k in enumerate(["putaway", "picking", "picking", "outbound", "replenishment"] * 8)])

    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser(username="s", password="x")
        self.client.post("/login/", {"username": "s", "password": "x"})

    def test_day_tasks_are_seconds_from_shift_start(self):
        tasks = load_day_tasks(self.batch, datetime(2026, 9, 1).date())
        self.assertEqual(len(tasks), 40)
        self.assertEqual(tasks[0][0], 3 * 3600)                               # 08:00 − 05:00

    def test_form_then_results(self):
        url = f"/magazyn/zadania-ewm/{self.batch.pk}/symulacja/"
        r = self.client.get(url)
        self.assertContains(r, "Symuluj dzień")
        self.assertNotContains(r, "Sugerowane szt.")
        r = self.client.get(url, {"run": 1, "model": self.wm.pk, "mult": "1,0", "agv": 2, "kombi": 2, "ept": 2})
        self.assertContains(r, "Sugerowane szt.")
        self.assertContains(r, "Przyjęcie / odłożenie")

    def test_profile_links_to_simulation(self):
        r = self.client.get(f"/magazyn/zadania-ewm/{self.batch.pk}/profil/")
        self.assertContains(r, f"/magazyn/zadania-ewm/{self.batch.pk}/symulacja/")
