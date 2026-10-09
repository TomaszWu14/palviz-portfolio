"""OR-Tools optimal layer wired into instruction layouts via _eval_layouts(extra_layouts=)."""
from django.test import SimpleTestCase

from ui.views.core.packing import _eval_layouts, _build_pallet
from palletizer.domain.carton import CartonVariant
from palletizer.domain.types import Dimensions
from palletizer.services.pallet_calculator import PalletCalculator, LayoutOption, Placement2D


class ExtraLayoutsTests(SimpleTestCase):
    def _setup(self):
        pallet, meta = _build_pallet("EU", 180, 1000)
        carton = CartonVariant(sku="S", variant="v",
                               dims=Dimensions(l_cm=30, w_cm=40, h_cm=20),
                               unit_weight_kg=1, pieces_per_carton=1, demand_pieces=100,
                               carton_tare_kg=0, allow_rotation=True).validate()
        res = PalletCalculator.calculate(carton, pallet)
        return carton, pallet, meta, res

    def test_extra_optimal_layout_ranks_first(self):
        carton, pallet, meta, res = self._setup()
        best = max(o.cartons_per_layer for o in res.all_layouts)
        # Inject a (synthetic) layout that fits more per layer → must become the selected best.
        extra = LayoutOption("Optymalny (OR-Tools)", best + 3, 99.0,
                             tuple(Placement2D(0, 0, 30, 40, False) for _ in range(best + 3)))
        layouts = _eval_layouts(carton, pallet, meta, res, extra_layouts=[extra])
        self.assertEqual(layouts[0]["name"], "Optymalny (OR-Tools)")
        self.assertTrue(layouts[0]["is_best"])
        self.assertEqual(layouts[0]["cartons_per_layer"], best + 3)

    def test_no_extra_is_unchanged(self):
        carton, pallet, meta, res = self._setup()
        a = _eval_layouts(carton, pallet, meta, res)
        b = _eval_layouts(carton, pallet, meta, res, extra_layouts=None)
        self.assertEqual([l["name"] for l in a], [l["name"] for l in b])
