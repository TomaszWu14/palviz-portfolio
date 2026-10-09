"""Eksport modelu magazynu do animacji przepływów w Blenderze (tools/blender/)."""
import json
import math
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import (
    PickerActivity, PickerActivityBatch, WarehouseHallFeature, WarehouseModel,
    WarehouseModelRack,
)
from wh3d.blender_agents import CARRY_OFFSET
from wh3d.blender_route import FloorGrid, _inside, rack_axes
from wh3d.blender_scene import FORMAT, build_scene


def _rack(rid, x, y, angle=0, width=10.0, bays=5, levels=3):
    return {"id": rid, "zone": "B0", "rack_id": f"{rid:02d}", "x": x, "y": y, "angle": angle,
            "width": width, "depth": 1.1, "level_h": 1.5, "n_bays": bays, "n_levels": levels}


RACKS = [_rack(1, 3, 3), _rack(2, 3, 4.1), _rack(3, 3, 8), _rack(4, 18, 2, angle=90)]
FEATURES = [{"id": 1, "kind": "dock", "kind_label": "Dok", "label": "Dok 1", "x": 5, "y": 16,
             "width": 3, "depth": 2, "angle": 0, "color": "#0ea5e9"},
            {"id": 2, "kind": "station", "kind_label": "Stanowisko", "label": "Pakowanie",
             "x": 15, "y": 15, "width": 2, "depth": 1.5, "angle": 0, "color": "#22c55e"}]
FLOOR = {"width": 24, "depth": 19}


def _scene(**kw):
    return build_scene({"id": 7, "name": "Test"}, FLOOR, RACKS, FEATURES, **kw)


class RouteGeometryTests(SimpleTestCase):
    def test_axes_match_threejs_rotation_y(self):
        # three.js rotation.y=90°: lokalne +x → świat (0,0,−1) → hala (0,−1)
        u_w, u_d = rack_axes(90)
        self.assertAlmostEqual(u_w[0], 0, places=6)
        self.assertAlmostEqual(u_w[1], -1, places=6)
        self.assertAlmostEqual(u_d[0], 1, places=6)

    def test_route_never_crosses_a_rack(self):
        grid = FloorGrid(FLOOR["width"], FLOOR["depth"], RACKS)
        pts = grid.route((1.0, 1.0), (8.0, 11.0))     # z przodu regałów za trzeci rząd
        self.assertGreater(len(pts), 2)                # musiał objechać regały
        for a, b in zip(pts, pts[1:], strict=False):
            n = max(2, int(math.dist(a, b) / 0.1))
            for k in range(n + 1):
                p = (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
                self.assertFalse(any(_inside(p, r, 0.0) for r in RACKS), p)

    def test_unreachable_target_falls_back_to_straight_line(self):
        wall = [_rack(9, 0, 5, width=24, bays=10)]
        grid = FloorGrid(24, 12, wall)
        self.assertEqual(grid.route((2, 2), (2, 9)), [(2, 2), (2, 9)])


class BuildSceneTests(SimpleTestCase):
    def test_scene_shape_and_json_roundtrip(self):
        sc = _scene()
        self.assertEqual(sc["format"], FORMAT)
        self.assertEqual(sc["source"]["picking"], "demo")
        kinds = {a["kind"] for a in sc["agents"]}
        self.assertEqual(kinds, {"forklift", "person"})
        self.assertEqual({f["kind"] for f in sc["flows"]}, {"inbound", "outbound", "picking"})
        self.assertEqual(json.loads(json.dumps(sc)), sc)
        self.assertGreater(sc["duration"], 0)

    def test_deterministic_for_same_model(self):
        self.assertEqual(_scene(), _scene())

    def test_keyframes_monotonic_and_items_visible_before_moving(self):
        sc = _scene()
        for a in sc["agents"]:
            ts = [k["t"] for k in a["keyframes"]]
            self.assertEqual(ts, sorted(ts), a["id"])
        for it in sc["items"]:
            ts = [k["t"] for k in it["keyframes"]]
            self.assertEqual(ts, sorted(ts), it["id"])
            self.assertLessEqual(it["appear"], ts[0] + 1e-6)

    def test_carried_pallet_rides_on_forks(self):
        sc = _scene()
        fl = next(a for a in sc["agents"] if a["kind"] == "forklift")
        pal = next(i for i in sc["items"] if i["id"].startswith(f"paleta-{fl['id']}-"))
        by_t = {k["t"]: k for k in fl["keyframes"]}
        fwd, up = CARRY_OFFSET["forklift"]
        riding = [k for k in pal["keyframes"][1:-1] if k["t"] in by_t]
        self.assertTrue(riding)
        for k in riding:
            c = by_t[k["t"]]
            h = math.radians(c["heading"])
            self.assertAlmostEqual(k["x"], c["x"] + fwd * math.cos(h), delta=0.01)
            self.assertAlmostEqual(k["y"], c["y"] + fwd * math.sin(h), delta=0.01)
            self.assertAlmostEqual(k["z"], c["lift"] + up, delta=0.01)

    def test_inbound_pallet_ends_in_rack_outbound_vanishes_at_dock(self):
        sc = _scene()
        vanished = [i for i in sc["items"] if i["kind"] == "pallet" and i["vanish"] is not None]
        stored = [i for i in sc["items"] if i["kind"] == "pallet" and i["vanish"] is None]
        self.assertTrue(vanished and stored)
        for it in stored:            # przyjęta paleta kończy w obrysie regału
            last = it["keyframes"][-1]
            self.assertTrue(any(_inside((last["x"], last["y"]), r, 0.01) for r in RACKS), it["id"])

    def test_no_racks_gives_static_scene(self):
        sc = build_scene({"id": 1, "name": "Pusty"}, FLOOR, [], [])
        self.assertEqual(sc["agents"], [])
        self.assertEqual(sc["items"], [])


class BlenderExportViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("bl", password="x")
        cls.user.groups.add(Group.objects.get_or_create(name="Podgląd")[0])  # _planner wymaga roli
        cls.wm = WarehouseModel.objects.create(name="Hala", floor_width_m=30, floor_depth_m=20)
        for i, y in enumerate((3, 8), start=1):
            WarehouseModelRack.objects.create(model=cls.wm, zone="B0", rack_id=f"0{i}",
                                              n_bays=4, n_levels=3, x_m=4, y_m=y)
        WarehouseHallFeature.objects.create(model=cls.wm, kind="dock", label="Dok A",
                                            x_m=5, y_m=17, width_m=3, depth_m=2)
        cls.batch = PickerActivityBatch.objects.create(name="Zmiana 1")
        now = timezone.now()
        for n, code in enumerate(["B0-01-100A", "B0-02-300B", "B0-01-200A", "X9-99-999Z"]):
            PickerActivity.objects.create(batch=cls.batch, location_code=code, picker_name="Anna",
                                          confirmed_at=now + timedelta(minutes=n), material_code="M1")

    def setUp(self):
        self.client.force_login(self.user)

    def _get(self, **params):
        return self.client.get(reverse("ui:warehouse_model_blender_json", args=[self.wm.pk]), params)

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self._get().status_code, 302)

    def test_demo_export_is_downloadable_json(self):
        r = self._get()
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        sc = r.json()
        self.assertEqual(sc["format"], FORMAT)
        self.assertEqual(len(sc["racks"]), 2)
        self.assertEqual(sc["features"][0]["label"], "Dok A")
        self.assertEqual(sc["source"]["picking"], "demo")

    def test_latest_batch_drives_picker_route(self):
        sc = self._get(batch="latest", forklifts=0).json()
        self.assertEqual(sc["source"], {"picking": "picker_activity", "forklifts": "demo",
                                        "batch": "Zmiana 1", "pallets": "stan_hu"})
        self.assertEqual([a["label"] for a in sc["agents"]], ["Anna"])
        cartons = [i for i in sc["items"] if i["kind"] == "carton"]
        self.assertEqual(len(cartons), 3)              # nieznana lokalizacja pominięta
        self.assertEqual({c["sku"] for c in cartons}, {"M1"})

    def test_bad_forklift_param_falls_back(self):
        self.assertEqual(self._get(forklifts="abc").status_code, 200)

    def test_model_view_links_export(self):
        r = self.client.get(reverse("ui:warehouse_model_view", args=[self.wm.pk]))
        self.assertContains(r, reverse("ui:warehouse_model_blender_json", args=[self.wm.pk]))
