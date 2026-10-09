"""3D pallet packing: a dominant SKU must build full mono-SKU pallets and never be
scattered across several mixed pallets (warehouse logic). Only leftovers may mix."""
from django.test import SimpleTestCase

from ui.views.core.helpers import _build_shipment_three_data_py3dbp


def _prod(code):
    return type("P", (), {"code": code})()


def _line(code, n, dims=(40, 30, 20), w=5.0, color="#abcdef"):
    return {"product": _prod(code), "n_cartons": n, "carton_dims": dims,
            "carton_weight_kg": w, "color": color}


def _labels(pallet):
    return {b["label"] for b in pallet["boxes"]}


class MonoSkuPackingTests(SimpleTestCase):
    def _pack(self, lines, max_h=200):
        vol = sum(l["n_cartons"] * l["carton_dims"][0] * l["carton_dims"][1]
                  * l["carton_dims"][2] for l in lines) / 1_000_000
        wt = sum(l["n_cartons"] * l["carton_weight_kg"] for l in lines)
        calc = {"lines": lines, "total_vol_m3": vol, "total_weight_kg": wt}
        return _build_shipment_three_data_py3dbp(calc, max_h=max_h, max_w=1000)

    def test_dominant_sku_not_scattered(self):
        # One big SKU spanning >1 pallet + a few small SKUs.
        data = self._pack([
            _line("DMOM10001", 60, color="#3b82f6"),
            _line("IS-RP-BF", 4, dims=(40, 30, 25), color="#ef4444"),
            _line("612009", 3, dims=(40, 30, 25), color="#10b981"),
        ])
        self.assertIsNotNone(data)
        pallets = data["pallets"]
        mono_dmom = [p for p in pallets if _labels(p) == {"DMOM10001"}]
        mixed_dmom = [p for p in pallets if "DMOM10001" in _labels(p) and len(_labels(p)) > 1]
        # The dominant SKU forms at least one clean mono pallet…
        self.assertGreaterEqual(len(mono_dmom), 1)
        # …and is mixed with other SKUs on at most one (the leftover) pallet.
        self.assertLessEqual(len(mixed_dmom), 1)

    def test_all_cartons_are_placed(self):
        data = self._pack([_line("A", 30), _line("B", 12, color="#f59e0b")])
        self.assertIsNotNone(data)
        placed = sum(len(p["boxes"]) for p in data["pallets"])
        self.assertEqual(placed, 42)
