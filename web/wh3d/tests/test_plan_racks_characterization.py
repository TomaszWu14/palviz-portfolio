"""Testy charakteryzujące model_from_layout.plan_racks (CODE-001) — przypinają OBECNE zachowanie przed podziałem.

Pełny wynik plan_racks() (regały + raport) dla zestawu scenariuszy porównywany ze snapshotem JSON
(data/plan_racks_snapshot.json). Regeneracja (tylko świadomie, gdy zmiana zachowania jest zamierzona):
    PLAN_RACKS_SNAPSHOT_UPDATE=1 python manage.py test wh3d.tests.test_plan_racks_characterization
"""
import json
import os
from pathlib import Path

from django.test import SimpleTestCase

from wh3d.model_from_layout import BACK_TO_BACK_M, CORRIDOR_M, plan_racks

SNAPSHOT = Path(__file__).with_name("data") / "plan_racks_snapshot.json"


def _row(aisle, row, stacks, col0=0, letters="ABC", step=4, zone="B0", level=1):
    """Alejka pozioma: każdy bok = len(letters) kolumn w wierszu `row`."""
    out, col = [], col0
    for st in stacks:
        for i, L in enumerate(letters):
            out.append((f"{zone}-{aisle}-{st}{L}", col + i, row, level))
        col += step
    return out


def _col(aisle, col, stacks, row0=0, step=3, zone="B0", letter="A", level=1):
    """Alejka pionowa: jeden bok = jedna komórka w kolumnie `col`."""
    return [(f"{zone}-{aisle}-{st}{letter}", col, row0 + i * step, level) for i, st in enumerate(stacks)]


def _m(w=900, d=1100, h=1500):
    return {"width_mm": w, "depth_mm": d, "height_mm": h}


def scenarios():
    """nazwa → (cells, master, slot_mm, aisle_cfg)."""
    s = {}
    s["empty"] = ([], {}, None, None)
    s["only_unparsable"] = (
        [("DOK-1", 0, 0, 1), ("", 1, 0, 1), ("B0", 2, 0, 1), ("B0-01-ABC", 3, 0, 1)], {}, None, None)
    s["horizontal_ascending_master_levels"] = (
        _row("01", 10, [100, 200, 300]) + _row("01", 10, [100, 200, 300], letters="XJK", level=2),
        {"B0-01-100A": _m(900, 1200, 1500), "B0-01-200A": _m(800, 1000, 1400),
         "B0-01-100X": _m(900, 1200, 1300), "B0-01-300X": _m(1000, 1300, 1700)},
        None, None)
    s["horizontal_descending"] = (_row("02", 5, [300, 200, 100]), {}, 1000, None)
    s["vertical_ascending"] = (_col("03", 7, [100, 200, 300]), {}, 1000, None)
    s["vertical_descending"] = (_col("03", 7, [300, 200, 100]), {}, 1000, None)
    s["single_cell_tie_is_horizontal"] = ([("B0-09-100A", 4, 4, 1)], {}, None, None)
    s["single_stack_many_levels"] = (
        [("B0-01-100A", 0, 0, 1), ("B0-01-100X", 0, 0, 2), ("B0-01-100Y", 0, 0, 3),
         ("B0-01-100Z", 0, 0, None)], {}, 1200, None)
    s["multi_row_aisle_majority_line"] = (
        _row("01", 2, [100, 200, 300]) + [("B0-01-400A", 12, 3, 1)], {}, 1000, None)
    # Remis wierszy → pierwszy napotkany (Counter.most_common).
    s["multi_row_tie_first_seen"] = ([("B0-01-100A", 0, 5, 1), ("B0-01-200A", 4, 2, 1)], {}, 1000, None)
    s["no_level1_uses_all_cells"] = (_row("01", 0, [100, 200], letters="XJ", level=2), {}, 1000, None)
    s["level_none_and_zero_from_code"] = (
        [("B0-01-100A", 0, 0, None), ("B0-01-100X", 0, 0, 0), ("B0-01-200Y", 4, 0, None),
         ("B0-01-200A", 4, 0, 1)], {}, 1000, None)
    s["four_part_codes"] = (
        [("B0-01-100-1A", 0, 0, None), ("B0-01-100-3X", 0, 0, None), ("B0-01-200-1A", 3, 0, None)],
        {}, 1000, None)
    s["lowercase_and_whitespace_codes"] = (
        [(" b0-01-100a ", 0, 0, 1), ("b0-01-200a", 3, 0, 1)], {"B0-01-100A": _m(900, 1250, 1700)}, None, None)
    s["master_zero_and_none_ignored"] = (
        _row("01", 0, [100, 200, 300]),
        {"B0-01-100A": _m(0, 0, 0), "B0-01-100B": {"width_mm": None, "depth_mm": None, "height_mm": None},
         "B0-01-200A": _m(1000, 900, 1100), "B0-01-300A": {}},
        None, None)
    s["slot_default_900_without_widths"] = (
        _row("01", 0, [100, 200]), {"B0-01-100A": _m(0, 1000, 1000)}, None, None)
    s["slot_explicit_overrides_master"] = (
        _row("01", 0, [100, 200]), {"B0-01-100A": _m(1200, 1000, 1000)}, 500, None)
    s["tight_grid_pairs_and_corridors"] = (
        _row("01", 0, [100, 200]) + _row("02", 1, [100, 200]) + _row("03", 2, [100, 200])
        + _row("04", 3, [100, 200]), {}, 800, None)
    # Parzystość para/korytarz liczona od ostatniego znanego odstępu: po realnym odstępie mapy
    # (0 → 5) ciasne linie 5|6 to nowa para plecami do siebie, 6 → 7 korytarz.
    s["tight_parity_after_real_gap"] = (
        _row("01", 0, [100]) + _row("02", 5, [100]) + _row("03", 6, [100]) + _row("04", 7, [100]),
        {}, 800, None)
    s["aisle_cfg_partial"] = (
        _row("01", 0, [100]) + _row("02", 1, [100]) + _row("03", 2, [100]), {}, 800, {"B0-02": 2.5})
    s["aisle_cfg_overrides_real_gap"] = (_row("01", 0, [100]) + _row("02", 9, [100]), {}, 800, {"B0-01": 1.8})
    s["aisle_cfg_max_of_line"] = (
        _row("01", 0, [100]) + _row("02", 0, [100], col0=20) + _row("03", 1, [100]), {}, 800,
        {"B0-01": 1.8, "B0-02": 3.5})
    s["two_aisles_same_line"] = (
        _row("01", 3, [100, 200]) + _row("02", 3, [100, 200], col0=20), {}, 1000, None)
    s["zones_same_rack_id"] = (
        _row("01", 0, [100, 200]) + _row("01", 6, [100, 200], zone="C1"), {}, 1000, None)
    s["vertical_tight_lines"] = (
        _col("01", 0, [100, 200, 300]) + _col("02", 1, [100, 200, 300]) + _col("03", 2, [100, 200, 300]),
        {}, 900, None)
    s["mixed_horizontal_and_vertical"] = (
        _row("01", 0, [100, 200, 300]) + _col("05", 20, [100, 200, 300], row0=5)
        + _row("02", 1, [100, 200, 300]), {}, 1000, None)
    s["far_offset_map"] = (_row("01", 200, [100, 200], col0=150), {}, 1000, None)
    s["mixed_depths_line_uses_max"] = (
        _row("01", 0, [100]) + _row("02", 0, [100], col0=10) + _row("03", 1, [100]),
        {"B0-01-100A": _m(800, 1500, 1500), "B0-02-100A": _m(800, 900, 1500)}, None, None)
    # Duplikat kodu liczony raz (pierwsze wystąpienie wygrywa) — w „cells” i w medianie głębokości.
    s["duplicate_codes"] = (
        [("B0-01-100A", 0, 0, 1), ("B0-01-100A", 0, 0, 1), ("B0-01-200A", 3, 0, 1)],
        {"B0-01-100A": _m(900, 1000, 1000), "B0-01-200A": _m(900, 1400, 2000)}, None, None)
    s["stack_gaps_n_bays_counts_existing"] = (_row("01", 0, [100, 300, 700], step=8), {}, 1000, None)
    return s


def _run(args):
    cells, master, slot_mm, aisle_cfg = args
    racks, report = plan_racks(cells, master, slot_mm=slot_mm, aisle_cfg=aisle_cfg)
    return json.loads(json.dumps({"racks": racks, "report": report}, ensure_ascii=False))


def run_all():
    return {name: _run(args) for name, args in scenarios().items()}


class PlanRacksCharacterizationTests(SimpleTestCase):
    maxDiff = None

    def test_snapshot(self):
        got = run_all()
        if os.environ.get("PLAN_RACKS_SNAPSHOT_UPDATE"):
            SNAPSHOT.write_text(json.dumps(got, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                encoding="utf-8")
        want = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(sorted(got), sorted(want))
        for name in want:
            with self.subTest(scenario=name):
                self.assertEqual(got[name], want[name])

    def test_empty_defaults(self):
        racks, rep = plan_racks([], {})
        self.assertEqual(racks, [])
        self.assertEqual(rep, {"assumed_spacing": False, "racks": 0, "cells": 0, "skipped_codes": 0,
                               "multi_row_aisles": [], "slot_mm": 900, "offset": (0.0, 0.0),
                               "floor": (50.0, 30.0)})

    def test_report_key_order_and_rack_keys(self):
        racks, rep = plan_racks(scenarios()["two_aisles_same_line"][0], {}, slot_mm=1000)
        self.assertEqual(list(rep), ["assumed_spacing", "racks", "cells", "skipped_codes",
                                     "multi_row_aisles", "slot_mm", "offset", "floor"])
        self.assertEqual(list(racks[0]), ["zone", "rack_id", "angle", "width", "level_h", "depth", "n_bays",
                                          "n_levels", "bay_width_cm", "depth_cm", "level_height_cm", "x", "y"])

    def test_input_master_not_mutated(self):
        master = {"B0-01-100A": _m()}
        plan_racks(_row("01", 0, [100, 200]), master)
        self.assertEqual(master, {"B0-01-100A": _m()})

    def test_parity_resets_after_real_gap(self):
        # Po realnym odstępie mapy (linie 0 → 5) ciasne linie zaczynają nową parę:
        # 5|6 plecami do siebie, 6 → 7 korytarz (więcej przypadków: test_plan_racks_fixes).
        racks, rep = plan_racks(*scenarios()["tight_parity_after_real_gap"][:3])
        ys = [r["y"] for r in sorted(racks, key=lambda r: r["rack_id"])]
        d = racks[0]["depth"]
        self.assertTrue(rep["assumed_spacing"])
        self.assertAlmostEqual(ys[1] - ys[0], 5 * 0.8)
        self.assertAlmostEqual(ys[2] - ys[1], d + BACK_TO_BACK_M)
        self.assertAlmostEqual(ys[3] - ys[2], d + CORRIDOR_M)

    def test_aisle_cfg_wins_over_real_map_gap(self):
        racks, rep = plan_racks(*scenarios()["aisle_cfg_overrides_real_gap"][:3], aisle_cfg={"B0-01": 1.8})
        a, b = sorted(racks, key=lambda r: r["rack_id"])
        self.assertAlmostEqual(b["y"] - a["y"], a["depth"] + 1.8)   # mapa miała 9 × 0,8 m
        self.assertFalse(rep["assumed_spacing"])
