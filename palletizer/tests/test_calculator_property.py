"""Property-based tests (Hypothesis) dla całego silnika PalletCalculator.calculate —
przypadki brzegowe z audytu (.scratch/concerns-cleanup, ticket 10):

  • karton większy niż paleta → kontrolowany ValueError (nigdy cichy 0-wynik),
  • ułamkowe dopasowanie warstw → inwarianty wysokości/wagi/ilości zawsze spójne,
  • mieszana orientacja (allow_rotation) → rotacja nigdy nie pogarsza wyniku.

Realistyczne rozkłady: wymiary/wagi typowe dla kartonów zbiorczych ACME.
Pomijane bez `hypothesis` (requirements-dev), jak test_maxrects_property.
"""
import unittest

try:
    from hypothesis import given, settings, strategies as st
    _HAS_HYP = True
except Exception:
    _HAS_HYP = False

from palletizer.domain.carton import CartonVariant
from palletizer.domain.pallet import PalletType
from palletizer.domain.types import Dimensions
from palletizer.services.pallet_calculator import PalletCalculator


def _carton(cl, cw, ch, weight=0.5, pcs=10, demand=1000, rotation=True):
    return CartonVariant(sku="P", variant="V", dims=Dimensions(l_cm=cl, w_cm=cw, h_cm=ch),
                         unit_weight_kg=weight, pieces_per_carton=pcs,
                         demand_pieces=demand, allow_rotation=rotation).validate()


def _pallet(max_h=200, max_w=1000):
    return PalletType(code="EU", dims=Dimensions(l_cm=120, w_cm=80, h_cm=max_h),
                      max_weight_kg=max_w).validate()


if _HAS_HYP:
    # Realistyczny rozkład kartonów zbiorczych: 15–80 cm podstawa, 10–60 cm wysokość.
    _dim = st.integers(min_value=15, max_value=80)
    _height = st.integers(min_value=10, max_value=60)
    _weight = st.floats(min_value=0.05, max_value=5.0, allow_nan=False, allow_infinity=False)

    class CalculatorInvariantTests(unittest.TestCase):
        @settings(max_examples=120, deadline=None)
        @given(cl=_dim, cw=_dim, ch=_height, w=_weight)
        def test_result_invariants_hold(self, cl, cw, ch, w):
            """Dla każdego sensownego kartonu: warstwy×wysokość ≤ limit, waga ≤ limit,
            pełne palety × cartons_per_pallet + remainder == zapotrzebowanie."""
            carton = _carton(cl, cw, ch, weight=w)
            res = PalletCalculator.calculate(carton, _pallet())
            self.assertGreater(res.best_layout.cartons_per_layer, 0)
            # Ułamkowe dopasowanie warstw: nigdy nie przekraczamy wysokości ładunku.
            self.assertLessEqual(res.layers_used * ch, 200)
            # Ani wagi palety.
            self.assertLessEqual(res.weight_per_pallet_kg, 1000 + 1e-6)
            # Bilans ilości: pallets_full×cpp + remainder == cartons_needed.
            if res.cartons_per_pallet > 0:
                self.assertEqual(res.pallets_full * res.cartons_per_pallet
                                 + res.remainder_cartons, carton.cartons_needed)
                self.assertLess(res.remainder_cartons, res.cartons_per_pallet)

        @settings(max_examples=60, deadline=None)
        @given(cl=st.integers(min_value=121, max_value=200),
               cw=st.integers(min_value=81, max_value=200))
        def test_oversized_base_raises_not_silent_zero(self, cl, cw):
            """Karton szerszy niż paleta w OBU orientacjach → ValueError, nie 0-wynik."""
            with self.assertRaises(ValueError):
                PalletCalculator.calculate(_carton(cl, cw, 20), _pallet())

        @settings(max_examples=60, deadline=None)
        @given(ch=st.integers(min_value=201, max_value=400))
        def test_too_tall_raises(self, ch):
            with self.assertRaises(ValueError):
                PalletCalculator.calculate(_carton(40, 30, ch), _pallet(max_h=200))

        @settings(max_examples=80, deadline=None)
        @given(cl=_dim, cw=_dim, ch=_height)
        def test_rotation_never_hurts(self, cl, cw, ch):
            """Mieszana orientacja: włączenie rotacji nigdy nie zmniejsza najlepszego
            wyniku kartonów/warstwę (może tylko dodać ułożenia)."""
            base = dict(weight=0.2, pcs=5, demand=500)
            try:
                no_rot = PalletCalculator.calculate(_carton(cl, cw, ch, rotation=False, **base),
                                                    _pallet())
            except ValueError:
                return  # bez rotacji może się w ogóle nie mieścić — poza zakresem property
            with_rot = PalletCalculator.calculate(_carton(cl, cw, ch, rotation=True, **base),
                                                  _pallet())
            self.assertGreaterEqual(with_rot.best_layout.cartons_per_layer,
                                    no_rot.best_layout.cartons_per_layer)

        def test_fractional_layer_fit_exact(self):
            """Deterministyczny przykład ułamkowego dopasowania: 200/60 = 3.33 → 3 warstwy."""
            res = PalletCalculator.calculate(_carton(40, 30, 60), _pallet(max_h=200))
            self.assertEqual(res.layers_used, 3)


if __name__ == "__main__":
    unittest.main()
