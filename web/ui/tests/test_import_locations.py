"""Unit tests for the location CSV importer (pure parsing — no DB)."""
from django.test import SimpleTestCase
from ui.management.commands.import_locations import (
    parse_rows, build_grid, resolve_positions, SUFFIX_LEVEL,
)

SAMPLE = (
    "Adres lokalizacji;Poziom;Typ magazynu;Wysokość [cm];Max objętość [m³];Max waga [kg];;;10\r\n"
    "B0-01-100A;1;0052;235;2,1;500;;;\r\n"
    "B0-01-100X;2;0010;226;0;500;;;\r\n"
    "B0-01-100Z;4;0010;255;0;500;;;\r\n"
    "B0-07-300C-1;603;0052;52;0,22;500;;;\r\n"   # corrupted "Poziom"=603 → level from suffix
    "GARBAGE-ROW;;;;;\r\n"
)


class ImporterTests(SimpleTestCase):
    def test_parsing_and_levels(self):
        rows = list(parse_rows(SAMPLE))
        self.assertEqual(len(rows), 4)                 # garbage row skipped
        by = {r["code"]: r for r in rows}
        self.assertEqual(by["B0-01-100A"]["level"], 1)
        self.assertEqual(by["B0-01-100X"]["level"], 2)
        self.assertEqual(by["B0-01-100Z"]["level"], 4)
        # corrupted Poziom column ignored — level derived from suffix C-1 → 1
        self.assertEqual(by["B0-07-300C-1"]["level"], 1)
        # units + decimal comma
        self.assertEqual(by["B0-01-100A"]["height_mm"], 2350)   # 235 cm → mm
        self.assertEqual(by["B0-07-300C-1"]["max_volume_m3"], 0.22)
        self.assertEqual(by["B0-01-100A"]["wh_type"], "0052")

    def test_grid_assignment(self):
        rows = build_grid(list(parse_rows(SAMPLE)))
        by = {r["code"]: r for r in rows}
        # A/X/Z share a footprint column (slot 0); different levels stack
        self.assertEqual(by["B0-01-100A"]["grid_col"], by["B0-01-100X"]["grid_col"])
        self.assertEqual(by["B0-01-100A"]["grid_row"], 0)        # aisle 01 → first band
        self.assertNotEqual(by["B0-07-300C-1"]["grid_row"], 0)   # aisle 07 → different band

    def test_suffix_level_complete(self):
        for suf in ("A","B","C","D","C-1","C-2","D-1","D-2","S","T","U","V","W","G","H","X","Y","Z"):
            self.assertIn(suf, SUFFIX_LEVEL)

    def test_resolve_positions_no_mixed_space_in_aisle(self):
        # aisle 01: stack 100 mapped (Excel row 50), stack 101 NOT mapped.
        # The unmapped stack must land next to its neighbour in PLAN space (row ~50),
        # never keep the tiny computed grid_row (regression: mixed coordinate spaces).
        rows = build_grid(list(parse_rows(
            "h\r\nB0-01-100A;1;0052;235;2,1;500\r\nB0-01-101A;1;0052;235;2,1;500\r\n"
        )))
        anchors = {("01", "100"): (50, 200)}     # only stack 100 is in the plan
        placed = resolve_positions(rows, anchors)
        by = {r["code"]: r for r in rows}
        self.assertEqual(placed, 2)   # both the mapped stack and its interpolated neighbour
        self.assertEqual(by["B0-01-100A"]["grid_row"], 50)
        self.assertEqual(by["B0-01-101A"]["grid_row"], 50)   # same plan row, not band 0
        self.assertEqual(by["B0-01-101A"]["grid_col"], 201)  # neighbour col + 1 stack

    def test_resolve_positions_unmapped_aisle_keeps_computed(self):
        rows = build_grid(list(parse_rows("h\r\nB0-09-100A;1;0052;235;2,1;500\r\n")))
        before = rows[0]["grid_row"]
        resolve_positions(rows, {("01", "100"): (50, 200)})   # nothing for aisle 09
        self.assertEqual(rows[0]["grid_row"], before)         # unchanged (computed)
