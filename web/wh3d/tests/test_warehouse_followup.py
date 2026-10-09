"""Warehouse followup (decyzje z przeglądu): parser 4-częściowych kodów (poziom!), heatmapa
godzi 3/4-częściowe kody kanonicznym kluczem, slotting savings = potencjał worst−optimized
(deterministyczny, bez fikcyjnej permutacji)."""
from django.test import SimpleTestCase

from ui.views.core.helpers import _parse_loc_code
from wh3d.views.warehouse_heatmap import _loc_canon
from ui.slotting import simulate_slotting


class ParseLocCodeTests(SimpleTestCase):
    def test_4part_level_and_col(self):
        self.assertEqual(_parse_loc_code("B0-01-100-2X"), ("B0-01", "100", "X", 0, 2))
        self.assertEqual(_parse_loc_code("B0-01-100-1A"), ("B0-01", "100", "A", 0, 1))

    def test_3part_still_parses(self):
        self.assertEqual(_parse_loc_code("B0-01-100A"), ("B0-01", "100", "A", 0, 1))

    def test_3part_and_4part_level1_reconcile(self):
        # 3-częściowy SAP i 4-częściowy buildera (poziom 1) → ta sama krotka → join heatmapy działa.
        self.assertEqual(_loc_canon("B0-01-100A"), _loc_canon("b0-01-100-1a"))


class SlottingSavingsTests(SimpleTestCase):
    def test_savings_is_worst_minus_optimized_deterministic(self):
        dem = {"a": 10, "b": 5, "c": 1}
        r1 = simulate_slotting(dem, [3, 1, 2])
        r2 = simulate_slotting(dem, [2, 3, 1])          # inna kolejność wejścia
        self.assertEqual(r1, r2)                        # deterministyczny (bez hash-order)
        # optimized=10*1+5*2+1*3=23 ; worst=10*3+5*2+1*1=41 ; savings=18
        self.assertEqual(r1["optimized"], 23.0)
        self.assertEqual(r1["worst"], 41.0)
        self.assertEqual(r1["savings"], 18.0)
        self.assertEqual(r1["baseline"], 41.0)          # punkt odniesienia = najgorsze

    def test_empty_safe(self):
        r = simulate_slotting({}, [])
        self.assertEqual(r["savings_pct"], 0.0)
