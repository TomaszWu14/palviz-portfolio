"""Przyjęcie kontenera w animacji (plan 2026-10-02, etap 2b): kontener przy doku → kartony
na przenośniku teleskopowym → paletyzacja → AGV/kombi; wydanie przez owijarkę."""
import math

from django.test import SimpleTestCase

from wh3d.blender_route import _inside
from wh3d.blender_containers import CARTONS_PER_PALLET, CONVEYOR_H, outward
from wh3d.blender_scene import WRAP_S, build_scene
from wh3d.tests.test_equipment_agents import FLOOR, HIGH


def _f(fid, kind, label, x, y, w=4, d=3.5):
    return {"id": fid, "kind": kind, "label": label, "x": x, "y": y, "width": w, "depth": d, "angle": 0}


FEATURES = [_f(1, "dock", "Dok kontenerowy (przenośnik teleskopowy) 1", 0, 6),
            _f(2, "station", "Paletyzacja 1", 6, 6, 4, 3.5),
            _f(3, "dock", "Dok FTL 1", 36, 12),
            _f(4, "station", "Owijarka 1", 30, 18, 4, 4)]


def _scene(features=FEATURES, **kw):
    return build_scene({"id": 1, "name": "T"}, FLOOR, HIGH, features, seed=5,
                       forklifts=1, forklift_tasks=4, **kw)


def _items(sc, kind):
    return [i for i in sc["items"] if i["kind"] == kind]


class ContainerInboundTests(SimpleTestCase):
    def test_outward_is_nearest_wall_normal(self):
        self.assertEqual(outward((2, 8), FLOOR), (-1, 0))
        self.assertEqual(outward((38, 8), FLOOR), (1, 0))
        self.assertEqual(outward((20, 23), FLOOR), (0, 1))

    def test_container_stands_outside_the_hall(self):
        sc = _scene()
        (box,) = _items(sc, "container")
        self.assertLess(box["keyframes"][0]["x"], 0)           # dok na ścianie x = 0
        self.assertEqual(len(sc["conveyors"]), 1)

    def test_cartons_ride_the_conveyor_into_palletizing(self):
        sc = _scene()
        cartons = [c for c in _items(sc, "carton") if c["id"].startswith("karton-k")]
        self.assertEqual(len(cartons) % CARTONS_PER_PALLET, 0)
        self.assertTrue(all(kf["z"] == CONVEYOR_H for c in cartons for kf in c["keyframes"]))
        end = sc["conveyors"][0]["points"][-1]
        self.assertTrue(all(math.dist((c["keyframes"][-1]["x"], c["keyframes"][-1]["y"]), end) < 1e-3
                            for c in cartons))

    def test_agv_takes_built_pallet_only_after_last_carton(self):
        sc = _scene()
        for pal in (p for p in _items(sc, "pallet") if p["id"].startswith("paleta-k")):
            n = pal["id"].split("-")[-1]
            last_carton = max(c["keyframes"][-1]["t"] for c in _items(sc, "carton")
                              if c["id"].startswith("karton-k1-")
                              and (int(c["id"].split("-")[-1]) - 1) // CARTONS_PER_PALLET == int(n) - 1)
            moved = [kf for kf in pal["keyframes"] if (kf["x"], kf["y"]) != (pal["keyframes"][0]["x"],
                                                                            pal["keyframes"][0]["y"])]
            self.assertTrue(moved, pal["id"])                  # paleta w końcu odjeżdża
            self.assertGreaterEqual(moved[0]["t"], last_carton)

    def test_container_pallets_end_in_vna_slots(self):
        sc = _scene()
        built = [p for p in _items(sc, "pallet") if p["id"].startswith("paleta-k")]
        self.assertTrue(built)
        for p in built:
            last = p["keyframes"][-1]
            self.assertTrue(any(_inside((last["x"], last["y"]), r, 0.01) for r in HIGH), p["id"])

    def test_outbound_goes_through_wrapper(self):
        sc = _scene()
        agv = next(a for a in sc["agents"] if a["kind"] == "agv")
        wrap = (32, 20)
        at_wrap = [kf for kf in agv["keyframes"] if math.dist((kf["x"], kf["y"]), wrap) < 1.0]
        self.assertTrue(at_wrap)
        self.assertGreaterEqual(at_wrap[-1]["t"] - at_wrap[0]["t"], WRAP_S - 1e-6)

    def test_palletizing_worker_is_animated(self):
        sc = _scene()
        self.assertTrue(any(a["id"].startswith("paletyzacja-") for a in sc["agents"]))

    def test_without_container_docks_nothing_changes(self):
        plain = [_f(1, "dock", "Dok 1", 0, 6)]
        sc = _scene(plain)
        self.assertEqual((_items(sc, "container"), sc["conveyors"]), ([], []))
        self.assertFalse([p for p in _items(sc, "pallet") if p["id"].startswith("paleta-k")])
