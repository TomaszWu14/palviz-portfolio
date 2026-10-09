"""Kalibracja symulacji na obecnej hali (plan 2026-10-02, etap 6)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from ui.models import WarehouseHallFeature, WarehouseModel, WarehouseModelRack
from wh3d.blender_stock import SlotLocator
from wh3d.design_calibration import GAP_MAX_S, calibrate, ideal_cycle
from wh3d.design_sim import simulate
from wh3d.models import WarehouseTask, WarehouseTaskBatch
from wh3d.tests.test_design_sim import FEATURES as NEW_FEATURES, RACKS as NEW_RACKS, _tasks
from wh3d.tests.test_ewm_tasks_flow import T0, _racks

CODES = ["B0-01-100A", "B0-01-300A", "B0-02-100A", "B0-02-300C"]
FEATURES = [{"kind": "dock", "label": "Dok", "x": 0, "y": 5, "width": 2, "depth": 2, "angle": 0},
            {"kind": "station", "label": "Pakowanie", "x": 18, "y": 5, "width": 2, "depth": 2, "angle": 0}]


def _rows(gap_s, n=30, resource="WOZEK01", kind="move"):
    """Wózek przewozi palety między dwoma gniazdami co `gap_s` sekund."""
    locs = [("B0-01-100A", "B0-02-300C"), ("B0-02-300C", "B0-01-100A")]
    return [(T0 + timedelta(seconds=i * gap_s), resource, "U", kind, *locs[i % 2], "M1") for i in range(n)]


class CalibrationTests(SimpleTestCase):
    def setUp(self):
        self.loc = SlotLocator(_racks(), CODES)

    def test_ratio_tracks_real_pace(self):
        """Ten sam ruch co 2 min vs co 4 min → współczynnik rośnie dwukrotnie."""
        fast = calibrate(_rows(120), self.loc, FEATURES)
        slow = calibrate(_rows(240), self.loc, FEATURES)
        kf = next(g["k"] for g in fast["groups"] if g["key"] == "trucks")
        ks = next(g["k"] for g in slow["groups"] if g["key"] == "trucks")
        self.assertAlmostEqual(ks / kf, 2.0, places=1)
        self.assertGreater(kf, 1.0)                      # realnie wolniej niż katalog

    def test_breaks_longer_than_limit_are_not_cycles(self):
        r = calibrate(_rows(GAP_MAX_S + 60), self.loc, FEATURES)
        self.assertEqual(next(g["pairs"] for g in r["groups"] if g["key"] == "trucks"), 0)

    def test_pairs_only_within_one_resource(self):
        rows = sorted(_rows(120, n=10, resource="A") + _rows(120, n=10, resource="B"))
        r = calibrate(rows, self.loc, FEATURES)
        self.assertEqual(r["resources"], 2)
        self.assertEqual(next(g["pairs"] for g in r["groups"] if g["key"] == "trucks"), 18)

    def test_picking_is_its_own_group(self):
        rows = [(T0 + timedelta(seconds=60 * i), "P1", "U", "picking", "B0-01-300A", "PACK", "M1")
                for i in range(25)]
        r = calibrate(rows, self.loc, FEATURES)
        g = {x["key"]: x for x in r["groups"]}
        self.assertEqual((g["picking"]["pairs"], g["trucks"]["pairs"]), (24, 0))
        self.assertTrue(g["picking"]["reliable"])

    def test_unmapped_locations_are_counted(self):
        rows = _rows(120, n=5) + [(T0, "W", "U", "move", "X9-99-999Z", "B0-01-100A", "M1")]
        r = calibrate(sorted(rows), self.loc, FEATURES)
        self.assertEqual(r["skipped"], 1)

    def test_ideal_cycle_includes_lift(self):
        low = ideal_cycle((0, 0), ((5, 0), 0.0), ((10, 0), 0.0), picking=False)
        high = ideal_cycle((0, 0), ((5, 0), 0.0), ((10, 0), 6.0), picking=False)
        self.assertGreater(high, low)

    def test_calibration_slows_down_the_new_hall(self):
        fleet = {"agv": 3, "kombi": 3, "ept": 3}
        base = simulate(_tasks(), NEW_RACKS, NEW_FEATURES, fleet)
        slow = simulate(_tasks(), NEW_RACKS, NEW_FEATURES, fleet, calib={"trucks": 1.5, "picking": 2.0})
        for a, b in zip(base["fleet"], slow["fleet"], strict=True):
            self.assertGreater(b["busy_h"], a["busy_h"], a["kind"])


class CalibrationViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.wm = WarehouseModel.objects.create(name="Logistyczna")
        for r in _racks():
            WarehouseModelRack.objects.create(model=cls.wm, zone=r["zone"], rack_id=r["rack_id"], n_bays=4,
                                              n_levels=3, bay_width_cm=250, depth_cm=110, level_height_cm=150,
                                              x_m=r["x"], y_m=r["y"])
        WarehouseHallFeature.objects.create(model=cls.wm, kind="dock", label="Dok", x_m=0, y_m=5)
        cls.batch = WarehouseTaskBatch.objects.create(name="Wrzesień", status="done")
        WarehouseTask.objects.bulk_create([
            WarehouseTask(batch=cls.batch, kind="move", src_location=s, dst_location=d, resource="W1",
                          confirmed_at=at, material="M1") for at, _r, _u, _k, s, d, _m in _rows(150)])

    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser(username="k", password="x")
        self.client.post("/login/", {"username": "k", "password": "x"})

    def test_page_shows_factor_and_links_to_simulation(self):
        url = f"/magazyn/zadania-ewm/{self.batch.pk}/kalibracja/"
        self.assertContains(self.client.get(url), "Policz kalibrację")
        r = self.client.get(url, {"model": self.wm.pk})
        self.assertContains(r, "Współczynniki korekty")
        self.assertContains(r, f"/magazyn/zadania-ewm/{self.batch.pk}/symulacja/?p=95&amp;kt=")
        self.assertEqual(next(g for g in r.context["result"]["groups"] if g["key"] == "trucks")["pairs"], 29)

    def test_simulation_form_carries_factors(self):
        r = self.client.get(f"/magazyn/zadania-ewm/{self.batch.pk}/symulacja/", {"kt": "1,7", "kp": "2"})
        self.assertContains(r, 'name="kt" value="1.7"')
        self.assertContains(r, 'name="kp" value="2"')
