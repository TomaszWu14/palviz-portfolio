"""Tests for the optional OR-Tools optimal single-layer packer.

Skipped automatically when `ortools` is not installed so the framework-free suite still
runs everywhere; exercised in CI (ortools is in requirements.txt)."""
import unittest

try:
    from ortools.sat.python import cp_model  # noqa: F401
    _HAS_ORTOOLS = True
except Exception:
    _HAS_ORTOOLS = False

from palletizer.services.ortools_layer import optimal_uniform_layer


@unittest.skipUnless(_HAS_ORTOOLS, "ortools not installed")
class OptimalLayerTests(unittest.TestCase):
    def _no_overlap(self, placements, L, W):
        for (x, y, w, h) in placements:
            self.assertGreaterEqual(x, 0); self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + w, L); self.assertLessEqual(y + h, W)
        for i in range(len(placements)):
            ax, ay, aw, ah = placements[i]
            for j in range(i + 1, len(placements)):
                bx, by, bw, bh = placements[j]
                disjoint = (ax + aw <= bx or bx + bw <= ax or
                            ay + ah <= by or by + bh <= ay)
                self.assertTrue(disjoint, f"overlap {placements[i]} & {placements[j]}")

    def test_exact_grid_fill_is_optimal(self):
        # 30×40 cartons into a 120×80 pallet → exactly 8 (perfect tiling), provably optimal.
        res = optimal_uniform_layer(120, 80, 30, 40, allow_rotation=True, time_limit_s=10)
        self.assertIsNotNone(res)
        self.assertEqual(len(res), 8)
        self._no_overlap(res, 120, 80)

    def test_rotation_beats_single_orientation(self):
        # 50×40 into 100×80: 4 fit either way; result must be valid and == area bound.
        res = optimal_uniform_layer(100, 80, 50, 40, allow_rotation=True, time_limit_s=10)
        self.assertIsNotNone(res)
        self.assertEqual(len(res), 4)
        self._no_overlap(res, 100, 80)

    def test_too_big_returns_none(self):
        self.assertIsNone(optimal_uniform_layer(100, 80, 200, 200))


class CompactLayerTests(unittest.TestCase):
    """Kompaktowanie warstwy — czysty Python, bez OR-Tools (zawsze uruchamiane)."""

    def test_pulls_floating_cartons_to_corner(self):
        from palletizer.services.ortools_layer import _compact_layer
        # Dwa 'plywajace' kartony 10×10 z dala od rogu — powinny dojechac do (0,0)/(10,0).
        out = _compact_layer([(20, 30, 10, 10), (40, 30, 10, 10)])
        floors = {(x, y) for (x, y, w, h) in out}
        self.assertIn((0, 0), floors)                 # jeden dociety do rogu
        self.assertTrue(any(y == 0 for (x, y, w, h) in out))
        # Liczba niezmieniona i nadal spójne (bez kolizji).
        self.assertEqual(len(out), 2)
        for i, (ax, ay, aw, ah) in enumerate(out):
            for bx, by, bw, bh in out[i + 1:]:
                self.assertTrue(ax + aw <= bx or bx + bw <= ax or
                                ay + ah <= by or by + bh <= ay)

    def test_already_compact_is_stable(self):
        from palletizer.services.ortools_layer import _compact_layer
        grid = [(0, 0, 10, 10), (10, 0, 10, 10), (0, 10, 10, 10)]
        self.assertEqual(set(_compact_layer(grid)), set(grid))
