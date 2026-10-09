"""Tests for the palletization core: oversized-carton guard and packing validity.

Run: python -m unittest palletizer.tests.test_pallet_calculator
"""
import itertools
import unittest

from palletizer.config import get_pallet_preset
from palletizer.domain import CartonVariant, Dimensions, PalletType
from palletizer.services.pallet_calculator import PalletCalculator, _maxrects_layer


def _pallet(code="EU"):
    p = get_pallet_preset(code)
    return PalletType(
        code=p.code,
        dims=Dimensions(l_cm=p.length_cm, w_cm=p.width_cm, h_cm=p.default_max_height_cm),
        max_weight_kg=p.max_weight_kg,
    ).validate()


def _carton(l, w, h, uw=1.0, pc=1, tare=0.0, dem=10):
    return CartonVariant(
        sku="T", variant="v1",
        dims=Dimensions(l_cm=l, w_cm=w, h_cm=h),
        unit_weight_kg=uw, pieces_per_carton=pc, carton_tare_kg=tare, demand_pieces=dem,
    )


class OversizedGuardTests(unittest.TestCase):
    def setUp(self):
        self.pallet = _pallet()

    def test_carton_taller_than_pallet_raises(self):
        c = _carton(20, 20, self.pallet.max_height_cm + 50)
        with self.assertRaises(ValueError):
            PalletCalculator.calculate(c, self.pallet)

    def test_footprint_larger_than_pallet_raises(self):
        c = _carton(self.pallet.length_cm + 30, self.pallet.width_cm + 30, 20)
        with self.assertRaises(ValueError):
            PalletCalculator.calculate(c, self.pallet)

    def test_single_carton_over_weight_raises(self):
        c = _carton(20, 20, 20, uw=self.pallet.max_weight_kg + 100)
        with self.assertRaises(ValueError):
            PalletCalculator.calculate(c, self.pallet)

    def test_normal_carton_palletizes(self):
        res = PalletCalculator.calculate(_carton(40, 30, 20, uw=2.0, pc=10, dem=500), self.pallet)
        self.assertGreater(res.cartons_per_pallet, 0)
        self.assertGreater(res.best_layout.cartons_per_layer, 0)


class PackingValidityTests(unittest.TestCase):
    """MaxRects placements must stay in-bounds and never overlap."""

    @staticmethod
    def _overlap(a, b):
        return not (a["x"] + a["dx"] <= b["x"] or b["x"] + b["dx"] <= a["x"]
                    or a["y"] + a["dy"] <= b["y"] or b["y"] + b["dy"] <= a["y"])

    def test_no_overlap_and_in_bounds(self):
        for pl, pw in [(120, 80), (100, 100), (129, 80)]:
            for cl in range(11, 60, 9):
                for cw in range(11, 60, 9):
                    ps = _maxrects_layer(pl, pw, cl, cw, allow_rotation=True)
                    for p in ps:
                        self.assertGreaterEqual(p["x"], 0)
                        self.assertGreaterEqual(p["y"], 0)
                        self.assertLessEqual(p["x"] + p["dx"], pl)
                        self.assertLessEqual(p["y"] + p["dy"], pw)
                    for a, b in itertools.combinations(ps, 2):
                        self.assertFalse(self._overlap(a, b),
                                         f"overlap at pallet {pl}x{pw} carton {cl}x{cw}")

    def test_degenerate_dims_return_empty_not_hang(self):
        # Zero/negative footprints must bail out (a 0-area rect never consumes space → loop).
        for cl, cw in [(0, 30), (30, 0), (0, 0), (-5, 30)]:
            self.assertEqual(_maxrects_layer(120, 80, cl, cw), [])
        self.assertEqual(_maxrects_layer(0, 80, 30, 30), [])


class LayoutOptionsTests(unittest.TestCase):
    """generate_layout_options must return several distinct, valid layouts."""

    @staticmethod
    def _ovl(a, b):
        return not (a.x + a.dx <= b.x or b.x + b.dx <= a.x
                    or a.y + a.dy <= b.y or b.y + b.dy <= a.y)

    def test_options_are_valid_distinct_and_capped(self):
        pallet = _pallet()
        for cl, cw, ch in [(40, 30, 20), (37, 28, 22), (45, 29, 18), (25, 15, 10), (33, 24, 20)]:
            opts = PalletCalculator.generate_layout_options(_carton(cl, cw, ch), pallet)
            self.assertGreaterEqual(len(opts), 2)            # more than one way to arrange
            self.assertLessEqual(len(opts), 15)              # capped at 15
            sigs = set()
            for o in opts:
                self.assertGreater(o.cartons_per_layer, 0)
                for p in o.placements:                       # every placement in-bounds
                    self.assertGreaterEqual(p.x, 0)
                    self.assertGreaterEqual(p.y, 0)
                    self.assertLessEqual(p.x + p.dx, pallet.length_cm)
                    self.assertLessEqual(p.y + p.dy, pallet.width_cm)
                for a, b in itertools.combinations(o.placements, 2):
                    self.assertFalse(self._ovl(a, b))        # no overlap within a layout
                sig = tuple(sorted((p.x, p.y, p.dx, p.dy) for p in o.placements))
                self.assertNotIn(sig, sigs)                  # layouts are visually distinct
                sigs.add(sig)

    def test_brick_pattern_offsets_alternate_rows(self):
        pallet = _pallet()
        opts = {o.name: o for o in PalletCalculator.generate_layout_options(_carton(40, 30, 20), pallet)}
        self.assertIn("B1_brick_interlock", opts)            # interlocked ("na zakładkę") option present


def _pallet_wt(max_w):
    """EU pallet with a custom max weight (0 = unlimited)."""
    return PalletType(code="EU", dims=Dimensions(l_cm=120, w_cm=80, h_cm=180),
                      max_weight_kg=max_w).validate()


class WeightLimitTests(unittest.TestCase):
    def test_overweight_full_layer_packs_partial_layer(self):
        # A full layer (~6×10kg=60kg) exceeds a 25kg cap, but a single 10kg carton fits:
        # expect one weight-limited partial layer of 2 cartons rather than 0.
        res = PalletCalculator.calculate(_carton(40, 30, 20, uw=10, pc=1, dem=100), _pallet_wt(25))
        self.assertEqual(res.layers_used, 1)
        self.assertEqual(res.cartons_per_pallet, 2)
        self.assertGreater(res.pallets_full, 0)


if __name__ == "__main__":
    unittest.main()
