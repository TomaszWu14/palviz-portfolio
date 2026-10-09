"""Property-based test (Hypothesis) for the MaxRects layer packer.

Skipped automatically when `hypothesis` is not installed (it lives in requirements-dev,
not the CI runtime set), so the standard suite still runs everywhere; developers who
`pip install -r requirements-dev.txt` get the fuzzing.
"""
import unittest

try:
    from hypothesis import given, settings, strategies as st
    _HAS_HYP = True
except Exception:
    _HAS_HYP = False

from palletizer.services.pallet_calculator import _maxrects_layer


if _HAS_HYP:
    class MaxRectsPropertyTests(unittest.TestCase):
        @settings(max_examples=150, deadline=None)
        @given(
            pl=st.integers(min_value=20, max_value=130),
            pw=st.integers(min_value=20, max_value=100),
            cl=st.integers(min_value=1, max_value=140),
            cw=st.integers(min_value=1, max_value=110),
        )
        def test_placements_are_valid(self, pl, pw, cl, cw):
            placements = _maxrects_layer(pl, pw, cl, cw, allow_rotation=True)
            for p in placements:
                # within the pallet bounds
                self.assertGreaterEqual(p["x"], 0)
                self.assertGreaterEqual(p["y"], 0)
                self.assertLessEqual(p["x"] + p["dx"], pl)
                self.assertLessEqual(p["y"] + p["dy"], pw)
            # pairwise non-overlapping
            for i in range(len(placements)):
                a = placements[i]
                for j in range(i + 1, len(placements)):
                    b = placements[j]
                    disjoint = (a["x"] + a["dx"] <= b["x"] or b["x"] + b["dx"] <= a["x"] or
                                a["y"] + a["dy"] <= b["y"] or b["y"] + b["dy"] <= a["y"])
                    self.assertTrue(disjoint)
