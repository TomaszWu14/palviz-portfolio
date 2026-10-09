"""Animacja godziny z symulacji dnia (plan 2026-10-02, etap 3b)."""
from collections import Counter

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from wh3d.blender_route import _inside
from wh3d.design_sim import DAY_START_H, simulate
from wh3d.design_sim_scene import CorridorRouter, build_sim_scene, window_legs
from wh3d.tests.test_design_sim import FEATURES, GEN, RACKS, SimulationViewTests, _tasks

FLEET = {"agv": 3, "kombi": 3, "ept": 3}
FLOOR = GEN["floor"]


def _trace():
    tr = []
    simulate(_tasks(), RACKS, FEATURES, FLEET, trace=tr)
    return tr


def _busiest_hour(trace):
    return DAY_START_H + Counter(int(t["depart"] // 3600) for t in trace).most_common(1)[0][0]


class SimSceneTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.trace = _trace()
        cls.hour = _busiest_hour(cls.trace)
        cls.scene = build_sim_scene({"id": 1, "name": "T"}, FLOOR, RACKS, FEATURES, cls.trace, cls.hour)

    def test_trace_has_every_fleet_kind(self):
        self.assertEqual({t["kind"] for t in self.trace}, {"agv", "kombi", "ept"})
        self.assertTrue(all(t["steps"] and t["steps"][0][0] == "go" for t in self.trace))

    def test_scene_uses_fleet_agents_only(self):
        kinds = {a["kind"] for a in self.scene["agents"]}
        self.assertTrue(kinds and kinds <= {"agv", "kombi", "ept"})
        self.assertLessEqual(sum(a["kind"] == "agv" for a in self.scene["agents"]), FLEET["agv"])
        self.assertEqual(self.scene["source"]["simulation"]["hour"], self.hour)

    def test_item_keyframes_never_go_back_in_time(self):
        for it in self.scene["items"]:
            ts = [kf["t"] for kf in it["keyframes"]]
            self.assertEqual(ts, sorted(ts), it["id"])

    def test_agent_keyframes_never_go_back_in_time(self):
        for a in self.scene["agents"]:
            ts = [kf["t"] for kf in a["keyframes"]]
            self.assertEqual(ts, sorted(ts), a["id"])

    def test_stored_pallets_end_inside_vna_racks(self):
        stored = [it for it in self.scene["items"] if it["kind"] == "pallet" and it["vanish"] is None
                  and it["keyframes"][-1]["z"] > 0]
        self.assertTrue(stored)
        for it in stored:
            kf = it["keyframes"][-1]
            self.assertTrue(any(_inside((kf["x"], kf["y"]), r, 0.01) for r in RACKS), it["id"])

    def test_scene_starts_at_window_start(self):
        first = min(a["keyframes"][0]["t"] for a in self.scene["agents"])
        self.assertLess(first, 3600)

    def test_truncation_is_reported(self):
        legs, total, truncated = window_legs(self.trace, self.hour, limit=5)
        self.assertEqual(len(legs), 5)
        self.assertTrue(truncated and total > 5)

    def test_corridor_route_is_axis_aligned_through_corridor(self):
        router = CorridorRouter([{"kind": "corridor", "x": 20, "y": 0, "width": 4, "depth": 100}])
        pts = router.route((5, 10), (60, 80))
        self.assertEqual(pts, [(5, 10), (22.0, 10), (22.0, 80), (60, 80)])
        self.assertEqual(router.route((5, 10), (60, 10.2)), [(5, 10), (60, 10.2)])

    def test_empty_trace_gives_empty_scene(self):
        sc = build_sim_scene({"id": 1}, FLOOR, RACKS, FEATURES, [], 8)
        self.assertEqual((sc["agents"], sc["items"]), ([], []))


class SimSceneViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        SimulationViewTests.setUpTestData.__func__(cls)     # ten sam model z generatora + dzień zadań

    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser(username="a", password="x")
        self.client.post("/login/", {"username": "a", "password": "x"})

    def test_flow_json_with_sim_param(self):
        r = self.client.get(f"/magazyn/model/{self.wm.pk}/przeplywy.json",
                            {"sim": self.batch.pk, "sim_h": 8, "agv": 2, "kombi": 2, "ept": 2})
        sc = r.json()
        self.assertEqual(sc["source"]["picking"], "simulation")
        self.assertTrue(sc["agents"])

    def test_simulation_page_links_hours_to_3d(self):
        r = self.client.get(f"/magazyn/zadania-ewm/{self.batch.pk}/symulacja/",
                            {"run": 1, "model": self.wm.pk})
        self.assertContains(r, "Animuj w 3D")
        self.assertContains(r, f"/magazyn/model/{self.wm.pk}/view/?sim={self.batch.pk}")
