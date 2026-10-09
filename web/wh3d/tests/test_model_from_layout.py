"""Model OBECNEGO magazynu z mapy lokalizacji + mastera — wh3d/model_from_layout.py."""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ui import models as m
from wh3d.blender_route import rack_corners
from wh3d.blender_stock import SlotLocator
from wh3d.model_from_layout import MARGIN_M, plan_racks, slot_pitch_mm


def _row(aisle, row, stacks, col0=0, letters="ABC", step=4, zone="B0"):
    """Komórki jednej alejki poziomej: każdy bok = len(letters) kolumn (poziom 1)."""
    out, col = [], col0
    for st in stacks:
        for i, L in enumerate(letters):
            out.append((f"{zone}-{aisle}-{st}{L}", col + i, row, 1))
        col += step
    return out


class PlanRacksTests(SimpleTestCase):
    def test_horizontal_rack_geometry_and_bay_order(self):
        cells = _row("01", 10, [100, 200, 300]) + [("B0-01-100X", 0, 10, 2)]
        master = {"B0-01-100A": {"width_mm": 900, "depth_mm": 1200, "height_mm": 1500},
                  "B0-01-100X": {"width_mm": 900, "depth_mm": 1200, "height_mm": 1300}}
        racks, rep = plan_racks(cells, master)
        r = racks[0]
        self.assertEqual((r["zone"], r["rack_id"], r["angle"]), ("B0", "01", 0.0))
        self.assertEqual((r["n_bays"], r["n_levels"]), (3, 2))
        self.assertAlmostEqual(r["width"], 11 * 0.9)            # kolumny 0..10
        self.assertEqual(r["depth_cm"], 120)
        self.assertEqual(r["level_height_cm"], 155)              # mediana(1500,1300)=1400 + 150
        self.assertEqual(rep["slot_mm"], 900)
        # margines: obrys zaczyna się w (2, 2)
        cs = rack_corners(r)
        self.assertAlmostEqual(min(p[0] for p in cs), MARGIN_M)
        self.assertAlmostEqual(min(p[1] for p in cs), MARGIN_M)
        # bok 100 leży przy lewym końcu (rosnąca numeracja wzdłuż +x)
        loc = SlotLocator(racks, [c[0] for c in cells])
        self.assertLess(loc.slot("B0-01-100A")["x"], loc.slot("B0-01-300A")["x"])

    def test_descending_numbering_flips_direction(self):
        cells = _row("02", 5, [300, 200, 100])                    # 100 po prawej stronie mapy
        racks, _ = plan_racks(cells, {}, slot_mm=1000)
        self.assertEqual(racks[0]["angle"], 180.0)
        loc = SlotLocator(racks, [c[0] for c in cells])
        self.assertGreater(loc.slot("B0-02-100A")["x"], loc.slot("B0-02-300A")["x"])

    def test_vertical_aisle(self):
        cells = [(f"B0-03-{st}A", 7, row, 1) for row, st in ((0, 100), (3, 200), (6, 300))]
        racks, _ = plan_racks(cells, {}, slot_mm=1000)
        self.assertEqual(racks[0]["angle"], -90.0)
        loc = SlotLocator(racks, [c[0] for c in cells])
        self.assertLess(loc.slot("B0-03-100A")["y"], loc.slot("B0-03-300A")["y"])

    def test_racks_keep_relative_layout_and_do_not_overlap(self):
        cells = _row("01", 0, [100, 200]) + _row("02", 3, [100, 200])
        racks, rep = plan_racks(cells, {}, slot_mm=1000)
        a, b = sorted(racks, key=lambda r: r["rack_id"])
        self.assertAlmostEqual(b["y"] - a["y"], 3.0)              # 3 rzędy mapy × 1 m
        self.assertLessEqual(a["depth"], 3.0)
        self.assertGreater(rep["floor"][0], a["width"])

    def test_tight_grid_gets_pairs_and_corridors(self):
        # rysunek B0: każda alejka to kolejny rząd arkusza (0,8 m) — ciaśniej niż głębokość
        cells = []
        for i, aisle in enumerate(["01", "02", "03", "04"]):
            cells += _row(aisle, i, [100, 200])
        racks, rep = plan_racks(cells, {}, slot_mm=800)
        ys = [r["y"] for r in sorted(racks, key=lambda r: r["rack_id"])]
        d = racks[0]["depth"]
        self.assertTrue(rep["assumed_spacing"])
        self.assertAlmostEqual(ys[1] - ys[0], d + 0.1)          # 01|02 plecami do siebie
        self.assertAlmostEqual(ys[2] - ys[1], d + 3.0)          # korytarz
        self.assertAlmostEqual(ys[3] - ys[2], d + 0.1)

    def test_aisle_config_sets_corridor_width(self):
        cells = _row("01", 0, [100]) + _row("02", 1, [100])
        racks, rep = plan_racks(cells, {}, slot_mm=800, aisle_cfg={"B0-01": 1.8})   # VNA
        a, b = sorted(racks, key=lambda r: r["rack_id"])
        self.assertAlmostEqual(b["y"] - a["y"], a["depth"] + 1.8)
        self.assertFalse(rep["assumed_spacing"])

    def test_non_rack_codes_counted_and_defaults(self):
        racks, rep = plan_racks([("DOK-1", 0, 0, 1), ("B0-01-100A", 0, 0, 1)], {})
        self.assertEqual(rep["skipped_codes"], 1)
        self.assertEqual(racks[0]["level_height_cm"], 200)       # brak mastera → 2000 mm
        self.assertEqual(racks[0]["depth_cm"], 110)

    def test_slot_pitch_is_median_width(self):
        self.assertEqual(slot_pitch_mm({"a": {"width_mm": 800}, "b": {"width_mm": 900},
                                        "c": {"width_mm": 1000}}), 900)
        self.assertEqual(slot_pitch_mm({}), 0)


class ModelFromLayoutViewTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="md", password="x")
        self.client.post("/login/", {"username": "md", "password": "x"})

    def test_creates_model_with_racks_and_features(self):
        layout = m.WarehouseLayout.objects.create(name="Hala B0", is_active=True)
        for code, col, row, lvl in _row("01", 4, [100, 200]) + _row("02", 8, [100, 200]):
            m.WarehouseLayoutCell.objects.create(layout=layout, location_code=code,
                                                 grid_col=col, grid_row=row, level=lvl)
        m.WarehouseLayoutCell.objects.create(layout=layout, location_code="B0-01-100X",
                                             grid_col=0, grid_row=4, level=2)
        batch = m.WarehouseLocationMasterBatch.objects.create(name="Master", is_active=True)
        m.WarehouseLocationMaster.objects.create(batch=batch, location_code="B0-01-100A",
                                                 width_mm=900, depth_mm=1100, height_mm=1600)
        m.WarehouseHallFeature.objects.create(layout=layout, kind="dock", label="Dok 1",
                                              x_m=1, y_m=1, width_m=3, depth_m=2)
        r = self.client.post(reverse("ui:warehouse_model_from_layout"), follow=True)
        self.assertEqual(r.status_code, 200)
        wm = m.WarehouseModel.objects.get()
        self.assertIn("Hala B0", wm.name)
        racks = {x.rack_id: x for x in wm.racks.all()}
        self.assertEqual(set(racks), {"01", "02"})
        self.assertEqual((racks["01"].n_bays, racks["01"].n_levels), (2, 2))
        self.assertEqual(racks["01"].level_height_cm, 175)
        self.assertAlmostEqual(racks["02"].y_m - racks["01"].y_m, 4 * 0.9, places=3)
        self.assertEqual(wm.features.get().label, "Dok 1")
        self.assertContains(r, "Utworzono model obecnego magazynu")

    def test_without_layout_shows_error(self):
        r = self.client.post(reverse("ui:warehouse_model_from_layout"), follow=True)
        self.assertContains(r, "Brak aktywnej mapy lokalizacji")
        self.assertFalse(m.WarehouseModel.objects.exists())

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(reverse("ui:warehouse_model_from_layout")).status_code, 405)

    def test_list_shows_button_only_with_active_layout(self):
        url = reverse("ui:warehouse_model_list")
        self.assertNotContains(self.client.get(url), "Model obecnego magazynu")
        m.WarehouseLayout.objects.create(name="L", is_active=True)
        self.assertContains(self.client.get(url), "Model obecnego magazynu")
