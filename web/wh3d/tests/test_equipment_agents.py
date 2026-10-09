"""Sprzęt nowego magazynu w animacji (plan 2026-10-02, etap 2): AGV + kombi w wysokim
składowaniu (sztafeta przez czoło rzędu), kompletacja z półek K1 na wózkach EPT."""
import math

from django.test import SimpleTestCase

from wh3d.blender_route import _inside
from wh3d.blender_scene import _vna_racks, build_scene


def _rack(rid, x, y, *, levels, level_h, depth=1.1, bays=8, width=21.6):
    return {"id": rid, "zone": "V", "rack_id": f"{rid:03d}", "x": x, "y": y, "angle": 0,
            "width": width, "depth": depth, "level_h": level_h, "n_bays": bays, "n_levels": levels}


# dwie pary plecami do siebie, między nimi korytarz VNA 1,8 m (jak z generatora hali)
HIGH = [_rack(1, 12, 4.0, levels=5, level_h=2.6), _rack(2, 12, 5.2, levels=5, level_h=2.6),
        _rack(3, 12, 8.1, levels=5, level_h=2.6), _rack(4, 12, 9.3, levels=5, level_h=2.6)]
SHELF = [_rack(4, 12, 16, levels=5, level_h=0.45, depth=0.6, bays=10, width=10),
         _rack(5, 12, 16.7, levels=5, level_h=0.45, depth=0.6, bays=10, width=10)]
FEATURES = [{"id": 1, "kind": "dock", "label": "Dok 1", "x": 0, "y": 8, "width": 4, "depth": 3.5, "angle": 0},
            {"id": 2, "kind": "station", "label": "Pakowanie", "x": 26, "y": 20, "width": 4, "depth": 3, "angle": 0}]
FLOOR = {"width": 40, "depth": 24}


def _scene(racks, **kw):
    return build_scene({"id": 1, "name": "T"}, FLOOR, racks, FEATURES, seed=3, **kw)


class EquipmentAgentsTests(SimpleTestCase):
    def test_high_bay_uses_agv_and_kombi_instead_of_forklifts(self):
        sc = _scene(HIGH + SHELF, forklifts=2, forklift_tasks=3)
        kinds = [a["kind"] for a in sc["agents"]]
        self.assertEqual((kinds.count("agv"), kinds.count("kombi"), kinds.count("forklift")), (2, 2, 0))
        self.assertEqual(sc["source"]["equipment"], "agv_kombi")

    def test_agv_never_lifts(self):
        sc = _scene(HIGH, forklifts=1, forklift_tasks=4)
        agv = next(a for a in sc["agents"] if a["kind"] == "agv")
        self.assertTrue(all(kf["lift"] == 0 for kf in agv["keyframes"]))

    def test_pallet_handover_has_no_teleport(self):
        """Paleta przechodzi AGV → kombi w jednym miejscu: między klatkami nie skacze
        szybciej niż sprzęt jeździ (z zapasem na obrót z ładunkiem na widłach)."""
        sc = _scene(HIGH, forklifts=1, forklift_tasks=4)
        pallets = [i for i in sc["items"] if i["kind"] == "pallet"]
        self.assertEqual(len(pallets), 4)
        for it in pallets:
            for a, b in zip(it["keyframes"], it["keyframes"][1:], strict=False):
                d = math.dist((a["x"], a["y"]), (b["x"], b["y"]))
                dt = b["t"] - a["t"]
                self.assertLessEqual(d, 6.0 * dt + 0.05, (it["id"], a, b))

    def test_inbound_pallet_ends_in_high_rack_slot(self):
        sc = _scene(HIGH, forklifts=1, forklift_tasks=1)       # zadanie 0 = przyjęcie
        last = sc["items"][0]["keyframes"][-1]
        self.assertTrue(any(_inside((last["x"], last["y"]), r, 0.01) for r in HIGH))
        kombi = next(a for a in sc["agents"] if a["kind"] == "kombi")
        self.assertGreater(max(kf["lift"] for kf in kombi["keyframes"]), 0)

    def test_kombi_never_takes_pallet_before_agv_drops_it(self):
        """Czas klatek palety rośnie: kombi czeka (wait_until), zamiast „cofać" paletę w czasie."""
        sc = _scene(HIGH, forklifts=2, forklift_tasks=4)
        for it in (i for i in sc["items"] if i["kind"] == "pallet"):
            ts = [kf["t"] for kf in it["keyframes"]]
            self.assertEqual(ts, sorted(ts), it["id"])

    def test_shelves_are_picked_from_ept(self):
        sc = _scene(HIGH + SHELF, demo_pickers=2, demo_picks=3)
        pickers = [a for a in sc["agents"] if a["id"].startswith("picker-")]
        self.assertEqual({a["kind"] for a in pickers}, {"ept"})
        cartons = [i for i in sc["items"] if i["kind"] == "carton"]
        first = [c["keyframes"][0] for c in cartons]
        self.assertTrue(all(any(_inside((k["x"], k["y"]), r, 0.3) for r in SHELF) for k in first))

    def test_vna_detection_needs_narrow_aisle(self):
        self.assertEqual([r["id"] for r in _vna_racks(HIGH)], [2, 3])     # 1 i 4 bez korytarza
        wide = [dict(r, y=r["y"] + (1.5 if r["id"] > 2 else 0)) for r in HIGH]   # korytarz 3,3 m
        self.assertEqual(_vna_racks(wide), [])
        sc = _scene(wide, forklifts=1, forklift_tasks=1)                   # wysoka, ale szeroka
        self.assertEqual({a["kind"] for a in sc["agents"]}, {"forklift", "person"})

    def test_classic_racks_keep_forklifts_and_walking_pickers(self):
        low = [_rack(1, 12, 4, levels=3, level_h=1.5), _rack(2, 12, 9, levels=3, level_h=1.5)]
        sc = _scene(low, forklifts=2, forklift_tasks=2)
        self.assertEqual({a["kind"] for a in sc["agents"]}, {"forklift", "person"})
        self.assertNotIn("equipment", sc["source"])
