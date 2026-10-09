"""Katalog elementów projektowania wariantów — wh3d/design_catalog.py (wspólny z Blenderem)."""
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

from wh3d.design_catalog import (
    ELEMENTS, block_rows, check_aisles, element_summary, footprint, params_for,
    pallet_positions, variant_summary,
)

WEB = Path(__file__).resolve().parents[2]


def _rack(kind, x, y, angle=0, label=None, **params):
    return {"kind": kind, "x": x, "y": y, "angle": angle, "label": label or f"{kind}@{y}",
            "params": params_for(kind, **params)}


class CatalogTests(SimpleTestCase):
    def test_every_element_has_label_and_params(self):
        for kind, spec in ELEMENTS.items():
            self.assertTrue(spec["label"], kind)
            s = element_summary(kind, params_for(kind))
            self.assertGreater(s["area_m2"], 0, kind)

    def test_params_override_and_unknown(self):
        self.assertEqual(params_for("rack_vna", levels=10)["levels"], 10)
        with self.assertRaises(ValueError):
            params_for("rack_vna", colour="red")
        with self.assertRaises(ValueError):
            params_for("teleport")

    def test_pallet_positions(self):
        self.assertEqual(pallet_positions("rack_std", params_for("rack_std")), 10 * 3 * 5)
        self.assertEqual(pallet_positions("rack_vna", params_for("rack_vna")), 20 * 3 * 8)
        self.assertEqual(pallet_positions("shuttle", params_for("shuttle")), 6 * 12 * 5)
        self.assertEqual(pallet_positions("conveyor", params_for("conveyor")), 0)

    def test_footprint(self):
        self.assertEqual(footprint("rack_std", params_for("rack_std", bays=4)), (4 * 2.7, 1.1))
        w, d = footprint("sorter", params_for("sorter", length=12))
        self.assertEqual((w, d), (12, 1.2 + 1.2))

    def test_block_rows_pairs_and_equipment_aisle(self):
        offs = block_rows("rack_vna", 4)
        self.assertEqual(offs, [0.0, 1.2, 4.1, 5.3])            # 1,1+0,1 | 1,1+1,8 | 1,1+0,1
        self.assertEqual(block_rows("rack_std", 3, back_to_back=False), [0.0, 4.1, 8.2])


class AisleCheckTests(SimpleTestCase):
    def test_too_narrow_aisle_for_reach_truck(self):
        a, b = _rack("rack_std", 0, 0), _rack("rack_std", 0, 1.1 + 2.0)
        issues = check_aisles([a, b])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "za wąska alejka")
        self.assertEqual(issues[0]["need_m"], 3.0)

    def test_vna_aisle_ok_and_back_to_back_ok(self):
        rows = [_rack("rack_vna", 0, off) for off in block_rows("rack_vna", 6)]
        self.assertEqual(check_aisles(rows), [])

    def test_collision(self):
        issues = check_aisles([_rack("rack_std", 0, 0), _rack("rack_std", 1, 0.5)])
        self.assertEqual(issues[0]["type"], "kolizja")

    def test_not_facing_or_rotated_is_ignored(self):
        a = _rack("rack_std", 0, 0, bays=2)
        far = _rack("rack_std", 50, 2.0, bays=2)                  # nie naprzeciw siebie
        rot = _rack("rack_std", 0, 2.0, angle=90, bays=2)
        self.assertEqual(check_aisles([a, far, rot]), [])

    def test_variant_summary(self):
        els = [_rack("rack_vna", 0, 0), _rack("rack_std", 0, 10),
               {"kind": "amr", "x": 0, "y": 20, "angle": 0, "label": "AMR", "params": params_for("amr")}]
        s = variant_summary(els, 100, 50)
        self.assertEqual(s["pallet_positions"], 480 + 150)
        self.assertEqual(s["by_kind"]["amr"]["count"], 1)
        self.assertEqual(s["floor_area_m2"], 5000)


class BlenderImportGuardTests(SimpleTestCase):
    def test_catalog_and_geometry_import_without_django(self):
        """Blender importuje te moduły bez Django — żadnego importu Django na poziomie modułu."""
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        code = ("import sys; sys.path.insert(0, %r); import wh3d.design_catalog, wh3d.blender_route; "
                "assert 'django' not in sys.modules, 'django zaimportowane'" % str(WEB))
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
