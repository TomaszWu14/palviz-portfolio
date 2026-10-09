"""Generator adresów modelu magazynu: szablon gniazda + reguła rzędu + wyjątki (spec cz. 1)."""
from types import SimpleNamespace as NS

from django.test import SimpleTestCase

from wh3d.addressing import (
    expand_model, expand_row, format_bay_numbers, parse_bay_numbers, parse_code, row_bay_numbers,
)


def lvl(letter, h, typ, split=False):
    return {"letter": letter, "height_mm": h, "ewm_type": typ, "split": split, "max_kg": 1000}


UPPER = [lvl("X", 1800, "0010"), lvl("Y", 1800, "0010"), lvl("Z", 1800, "0010")]
PICK = NS(pallets_per_beam=3, levels=[lvl("B", 400, "0052"), lvl("C", 400, "0052", True),
                                      lvl("D", 400, "0052", True)] + UPPER)
FLOOR = NS(pallets_per_beam=3, levels=[lvl("A", 1500, "0052")] + UPPER)
PASSAGE = NS(pallets_per_beam=4, levels=[lvl("Y", 1800, "0010"), lvl("Z", 1800, "0010")])


def row(**kw):
    base = dict(zone="B0", rack_id="07", n_bays=2, bay_width_cm=270, bay_numbers="30-31", reverse=False)
    base.update(kw)
    return NS(**base)


def ov(bay, action, letter="", position=0, half=0, value="", template=None):
    return NS(bay=bay, action=action, letter=letter, position=position, half=half, value=value, template=template)


def codes(locs):
    return [loc["code"] for loc in locs]


class ParseTests(SimpleTestCase):
    def test_parse_code_with_half_and_lowercase(self):
        self.assertEqual(parse_code("B0-07-300C-1"), ("B0", "07", 30, 0, "C", 1))
        self.assertEqual(parse_code(" b0-07-302a "), ("B0", "07", 30, 2, "A", 0))
        self.assertIsNone(parse_code("0051ZONE"))
        self.assertIsNone(parse_code("04.01"))

    def test_bay_numbers_ranges_round_trip(self):
        nums = parse_bay_numbers("10-47, 50")
        self.assertEqual(nums, list(range(10, 48)) + [50])
        self.assertEqual(format_bay_numbers(nums), "10-47,50")
        self.assertEqual(parse_bay_numbers(""), [])
        self.assertEqual(format_bay_numbers([29, 30, 33, 34, 35]), "29-30,33-35")

    def test_bay_numbers_invalid(self):
        for bad in ("10-5", "a", "10,10", "3-", "1-5000", "1-999,1000-1999"):
            with self.assertRaises(ValueError, msg=bad):
                parse_bay_numbers(bad)

    def test_row_numbers_default_and_truncation(self):
        self.assertEqual(row_bay_numbers(row(bay_numbers="", n_bays=3)), [1, 2, 3])
        self.assertEqual(row_bay_numbers(row(bay_numbers="10-20", n_bays=2)), [10, 11])
        self.assertEqual(row_bay_numbers(row(bay_numbers="29-30,33", n_bays=3)), [29, 30, 33])


class ExpandRowTests(SimpleTestCase):
    def test_pick_template_with_halves(self):
        locs = expand_row(row(), PICK)
        # na gniazdo: B 3 + C½ 6 + D½ 6 + X/Y/Z 9 = 24; dwa gniazda = 48
        self.assertEqual(len(locs), 48)
        c = codes(locs)
        self.assertEqual(c[:4], ["B0-07-300B", "B0-07-301B", "B0-07-302B", "B0-07-300C-1"])
        for code in ("B0-07-300C-2", "B0-07-302D-2", "B0-07-302Z", "B0-07-310B", "B0-07-312Z"):
            self.assertIn(code, c)
        self.assertNotIn("B0-07-300C", c)
        self.assertEqual(len(set(c)), 48)

    def test_geometry_along_and_z(self):
        by = {loc["code"]: loc for loc in expand_row(row(), PICK)}
        self.assertEqual(by["B0-07-300B"]["along_m"], 0.45)          # belka 2,7 m / 3 palety
        self.assertEqual(by["B0-07-300C-1"]["along_m"], 0.225)       # połówka = pół pozycji
        self.assertEqual(by["B0-07-300C-2"]["along_m"], 0.675)
        self.assertEqual(by["B0-07-312X"]["along_m"], 4.95)          # 2. gniazdo, pozycja 2
        self.assertEqual(by["B0-07-300X"]["z_m"], 1.2)               # B+C+D = 3 × 0,4 m
        self.assertEqual(by["B0-07-300X"]["level_index"], 3)
        self.assertEqual(by["B0-07-300C-1"]["ewm_type"], "0052")
        self.assertFalse(by["B0-07-300B"]["blocked"])

    def test_reverse_mirrors_along(self):
        by = {loc["code"]: loc for loc in expand_row(row(reverse=True), PICK)}
        self.assertEqual(by["B0-07-300B"]["along_m"], 4.95)          # 5,4 m − 0,45 m
        self.assertEqual(by["B0-07-312X"]["along_m"], 0.45)

    def test_numbering_with_gaps(self):
        c = codes(expand_row(row(n_bays=3, bay_numbers="29-30,33"), FLOOR))
        self.assertEqual(sorted({code[6:8] for code in c}), ["29", "30", "33"])

    def test_bay_skip_and_bay_template(self):
        locs = expand_row(row(n_bays=4, bay_numbers="29-32"), FLOOR,
                          [ov(31, "skip"), ov(32, "skip"), ov(30, "template", template=PASSAGE)])
        c = codes(locs)
        self.assertFalse([x for x in c if x[6:8] in ("31", "32")])
        self.assertIn("B0-07-303Y", c)                               # przejazd: 4 palety, tylko Y/Z
        self.assertNotIn("B0-07-300A", c)
        self.assertIn("B0-07-290A", c)
        self.assertEqual(len(c), 12 + 8)

    def test_template_none_gives_nothing(self):
        self.assertEqual(expand_row(row(), None), [])

    def test_location_overrides(self):
        locs = expand_row(row(n_bays=1, bay_numbers="30"), FLOOR, [
            ov(30, "skip", "A", 1),
            ov(30, "add", "A", 3, value="0050"),
            ov(30, "rename", "X", 0, value="B0-07-SPEC1"),
            ov(30, "ewm_type", "Y", 0, value="0011"),
            ov(30, "block", "Z", 2),
        ])
        by = {loc["code"]: loc for loc in locs}
        self.assertNotIn("B0-07-301A", by)
        self.assertEqual(by["B0-07-303A"]["ewm_type"], "0050")
        self.assertIn("B0-07-SPEC1", by)
        self.assertNotIn("B0-07-300X", by)
        self.assertEqual(by["B0-07-300Y"]["ewm_type"], "0011")
        self.assertTrue(by["B0-07-302Z"]["blocked"])
        self.assertEqual(len(locs), 12)                              # 12 − skip + add

    def test_split_and_unsplit(self):
        c = codes(expand_row(row(n_bays=1, bay_numbers="30"), FLOOR, [ov(30, "split", "A", 0)]))
        self.assertIn("B0-07-300A-1", c)
        self.assertIn("B0-07-300A-2", c)
        self.assertNotIn("B0-07-300A", c)
        c = codes(expand_row(row(n_bays=1, bay_numbers="30"), PICK, [ov(30, "unsplit", "C", 1)]))
        self.assertIn("B0-07-301C", c)
        self.assertNotIn("B0-07-301C-1", c)


class ExpandModelTests(SimpleTestCase):
    def test_duplicates_between_rows(self):
        a, b = row(), row(rack_id="08")
        locs, dups = expand_model([(a, FLOOR, []), (b, FLOOR, [])])
        self.assertEqual(dups, {})
        self.assertEqual(locs[0]["zone"], "B0")
        self.assertEqual(locs[0]["aisle"], "07")
        _, dups = expand_model([(a, FLOOR, []), (row(), FLOOR, [])])
        self.assertEqual(dups["B0-07-300A"], ["B0-07", "B0-07"])
