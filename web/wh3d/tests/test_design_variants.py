"""Warianty projektu magazynu: wskaźniki (wh3d/design_kpi.py) i widoki porównania/importu."""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ui import models as m
from wh3d.design_catalog import params_for
from wh3d.design_kpi import (
    clean_elements, compute_kpi, equipment_capacity, rack_to_element, storage_faces,
    travel_stats,
)
from wh3d.models import WarehouseDesignVariant

DOCK = [{"kind": "dock", "x": 0, "y": 30, "width": 4, "depth": 2, "angle": 0}]


def _el(kind, x, y, angle=0, **p):
    return {"kind": kind, "label": f"{kind}-{x}-{y}", "x": x, "y": y, "angle": angle,
            "params": params_for(kind, **p)}


class KpiTests(SimpleTestCase):
    def test_storage_faces_per_bay(self):
        faces = storage_faces([_el("rack_std", 0, 0, bays=4, levels=5, pallets_per_bay=3)])
        self.assertEqual(len(faces), 4)
        self.assertEqual({n for _, n in faces}, {15})
        (x, y), _ = faces[0]
        self.assertAlmostEqual(x, 1.35)
        self.assertAlmostEqual(y, -0.9)                     # front regału po stronie −u_d

    def test_travel_near_rack_beats_far_and_a_zone_is_shorter(self):
        near = [_el("rack_std", 0, 25, bays=4)]
        far = [_el("rack_std", 0, 2, bays=4)]
        t_near = travel_stats(near, DOCK, 40, 32)
        t_far = travel_stats(far, DOCK, 40, 32)
        self.assertLess(t_near["avg_m"], t_far["avg_m"])
        both = travel_stats(near + far, DOCK, 40, 32)
        self.assertLess(both["a_zone_avg_m"], both["avg_m"])
        self.assertEqual(both["anchors"], 1)

    def test_amr_station_is_an_anchor_and_no_anchor_falls_back(self):
        els = [_el("rack_std", 0, 0), _el("amr_station", 0, 5)]
        self.assertEqual(travel_stats(els, [], 40, 30)["anchors"], 1)
        self.assertEqual(travel_stats([_el("rack_std", 0, 0)], [], 40, 30)["anchors"], 0)   # front hali
        self.assertIsNone(travel_stats([_el("conveyor", 0, 0)], [], 40, 30)["avg_m"])

    def test_equipment_capacity_sums(self):
        cap = equipment_capacity([_el("amr", 0, 0), _el("amr", 2, 0), _el("sorter", 0, 5)])
        self.assertEqual(cap["amr"]["count"], 2)
        self.assertEqual(cap["amr"]["throughput_h"], 40)
        self.assertEqual(cap["sorter"]["throughput_h"], 3000)

    def test_rack_to_element(self):
        e = rack_to_element({"zone": "B0", "rack_id": "01", "x": 1, "y": 2, "angle": 180,
                             "width": 27.0, "depth": 1.1, "level_h": 1.8, "n_bays": 10, "n_levels": 5})
        self.assertEqual((e["label"], e["kind"], e["angle"]), ("B0-01", "rack_std", 180))
        self.assertEqual(e["params"]["pallets_per_bay"], 3)          # 2,7 m / 0,9
        self.assertEqual(compute_kpi([e], [], 40, 30)["pallet_positions"], 150)

    def test_clean_elements(self):
        ok, errors = clean_elements([
            {"kind": "rack_vna", "x": 1, "y": 2, "params": {"bays": 5, "nonsense": 1}},
            {"kind": "teleport", "x": 0, "y": 0},
            {"kind": "rack_std", "y": 1},
            {"kind": "amr", "x": 0, "y": 0, "params": {"speed": -3}},
        ])
        self.assertEqual(len(ok), 1)
        self.assertEqual(ok[0]["params"]["bays"], 5)
        self.assertEqual(ok[0]["params"]["levels"], 8)                # domyślne z katalogu
        self.assertEqual(len(errors), 3)
        self.assertEqual(clean_elements("zły")[0], [])


class VariantViewTests(TestCase):
    def setUp(self):
        self.md = get_user_model().objects.create_superuser(username="md", password="x")
        self.client.force_login(self.md)
        self.wm = m.WarehouseModel.objects.create(name="Obecny B0", floor_width_m=40, floor_depth_m=30)
        m.WarehouseModelRack.objects.create(model=self.wm, zone="B0", rack_id="01", n_bays=10,
                                            n_levels=5, bay_width_cm=270, x_m=2, y_m=5)
        m.WarehouseHallFeature.objects.create(model=self.wm, kind="dock", label="Dok 1",
                                              x_m=2, y_m=26, width_m=4, depth_m=2)

    def _variant_file(self, elements, name="Wariant VNA", **extra):
        data = {"format": "palviz.design-variant", "version": 1, "name": name,
                "base_model": {"id": self.wm.pk}, "floor": {"width": 40, "depth": 30},
                "features": [], "elements": elements, "summary": {"pallet_positions": 999999}}
        data.update(extra)
        return SimpleUploadedFile("wariant.json", json.dumps(data).encode(), "application/json")

    def test_baseline_from_model(self):
        r = self.client.post(reverse("ui:warehouse_variant_from_model"), {"model_id": self.wm.pk}, follow=True)
        v = WarehouseDesignVariant.objects.get()
        self.assertEqual((v.source, v.base_model, v.kpi["pallet_positions"]), ("model", self.wm, 150))
        self.assertIsNotNone(v.kpi["travel"]["avg_m"])
        self.assertContains(r, "Obecny: Obecny B0")

    def test_import_recomputes_kpi_and_compares(self):
        self.client.post(reverse("ui:warehouse_variant_from_model"), {"model_id": self.wm.pk})
        f = self._variant_file([_el("rack_vna", 2, 3, bays=10), _el("rack_vna", 2, 3 + 1.1 + 1.8, bays=10),
                                _el("amr", 20, 20)])
        r = self.client.post(reverse("ui:warehouse_variant_import"), {"variant_file": f}, follow=True)
        v = WarehouseDesignVariant.objects.get(source="blender")
        self.assertEqual(v.kpi["pallet_positions"], 2 * 10 * 3 * 8)   # nie 999999 z pliku
        self.assertEqual(v.base_model, self.wm)
        self.assertEqual(v.kpi["aisle_issue_count"], 0)
        self.assertContains(r, "Wariant VNA")
        self.assertContains(r, "Miejsca paletowe")
        self.assertContains(r, "+220%")                               # 480 vs 150
        self.assertContains(r, 'class="best"')

    def test_import_rejects_wrong_format(self):
        bad = SimpleUploadedFile("x.json", b'{"format": "palviz.blender-flow"}', "application/json")
        r = self.client.post(reverse("ui:warehouse_variant_import"), {"variant_file": bad}, follow=True)
        self.assertContains(r, "To nie jest plik wariantu")
        garbage = SimpleUploadedFile("x.json", b"{nie json", "application/json")
        r = self.client.post(reverse("ui:warehouse_variant_import"), {"variant_file": garbage}, follow=True)
        self.assertContains(r, "nie jest poprawnym JSON")
        self.assertFalse(WarehouseDesignVariant.objects.exists())

    def test_json_roundtrip(self):
        f = self._variant_file([_el("shuttle", 5, 5)], name="Shuttle")
        self.client.post(reverse("ui:warehouse_variant_import"), {"variant_file": f})
        v = WarehouseDesignVariant.objects.get()
        data = self.client.get(reverse("ui:warehouse_variant_json", args=[v.pk])).json()
        self.assertEqual(data["format"], "palviz.design-variant")
        again = SimpleUploadedFile("w.json", json.dumps(data).encode(), "application/json")
        self.client.post(reverse("ui:warehouse_variant_import"), {"variant_file": again, "name": "Kopia"})
        copy = WarehouseDesignVariant.objects.get(name="Kopia")
        self.assertEqual(copy.kpi["pallet_positions"], v.kpi["pallet_positions"])

    def test_delete(self):
        self.client.post(reverse("ui:warehouse_variant_from_model"), {"model_id": self.wm.pk})
        v = WarehouseDesignVariant.objects.get()
        self.client.post(reverse("ui:warehouse_variant_delete", args=[v.pk]))
        self.assertFalse(WarehouseDesignVariant.objects.exists())

    def test_viewer_can_compare_but_not_import(self):
        viewer = get_user_model().objects.create_user("podglad", password="x")
        viewer.groups.add(Group.objects.get_or_create(name="Podgląd")[0])
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("ui:warehouse_variants")).status_code, 200)
        f = self._variant_file([_el("rack_std", 0, 0)])
        self.client.post(reverse("ui:warehouse_variant_import"), {"variant_file": f})
        self.assertFalse(WarehouseDesignVariant.objects.exists())
        self.assertNotContains(self.client.get(reverse("ui:warehouse_variants")), "Importuj wariant")

    def test_empty_state_and_model_list_link(self):
        self.assertContains(self.client.get(reverse("ui:warehouse_variants")), "Brak wariantów")
        self.assertContains(self.client.get(reverse("ui:warehouse_model_list")),
                            reverse("ui:warehouse_variants"))
