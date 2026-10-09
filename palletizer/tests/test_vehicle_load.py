"""Whole-vehicle load planning (truck/container fit, vehicles needed, LDM, balance)."""
import unittest

from palletizer.services.vehicle_load import floor_positions, plan_vehicle, plan_all, VEHICLES


class FloorPositionsTests(unittest.TestCase):
    def test_trailer_fits_33_to_34_eur_pallets(self):
        # 13.6 m × 2.45 m trailer holds 33–34 EUR pallets on the floor.
        n = floor_positions(1360, 245, 120, 80)
        self.assertIn(n, (33, 34))

    def test_degenerate_pallet_zero(self):
        self.assertEqual(floor_positions(1360, 245, 0, 80), 0)


class PlanTests(unittest.TestCase):
    def _naczepa(self):
        return next(v for v in VEHICLES if v["key"] == "naczepa")

    def test_single_truck_for_small_load(self):
        p = plan_vehicle(self._naczepa(), n_pallets=20, pallet_height_cm=180,
                         avg_pallet_weight_kg=300, total_weight_kg=6000)
        self.assertEqual(p["vehicles"], 1)
        self.assertGreater(p["space_util"], 0)
        self.assertEqual(p["limited_by"], "przestrzeń")

    def test_two_trucks_when_over_capacity(self):
        p = plan_vehicle(self._naczepa(), n_pallets=50, pallet_height_cm=180,
                         avg_pallet_weight_kg=300, total_weight_kg=15000)
        self.assertEqual(p["vehicles"], 2)   # 50 > 34 floor → 2 trucks

    def test_weight_limited_load(self):
        # Heavy pallets: payload (24 t) caps before floor space does.
        p = plan_vehicle(self._naczepa(), n_pallets=34, pallet_height_cm=180,
                         avg_pallet_weight_kg=1000, total_weight_kg=34000)
        self.assertEqual(p["limited_by"], "waga")
        self.assertGreaterEqual(p["vehicles"], 2)

    def test_double_stacking_reduces_vehicles(self):
        single = plan_vehicle(self._naczepa(), 60, 110, 200, 12000, double_stack=False)
        double = plan_vehicle(self._naczepa(), 60, 110, 200, 12000, double_stack=True)
        self.assertEqual(double["layers"], 2)
        self.assertLess(double["vehicles"], single["vehicles"])

    def test_plan_all_ranks_recommended_first(self):
        plans = plan_all(n_pallets=20, pallet_height_cm=180, total_weight_kg=6000)
        self.assertTrue(plans[0]["recommended"])
        # Recommended uses the fewest vehicles.
        self.assertEqual(plans[0]["vehicles"], min(p["vehicles"] for p in plans))

    def test_zero_pallets_safe(self):
        self.assertEqual(plan_all(0, 180, 0), [])
