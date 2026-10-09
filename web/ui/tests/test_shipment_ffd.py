"""Shipment 3D packing (FFD, now the default builder): neat full single-SKU layers,
per-pallet weight cap respected, 90° turn used to fill a layer, no floating cartons."""
from django.test import SimpleTestCase

from ui.views.core.helpers import _build_shipment_three_data, _build_shipment_three_data_ffd

BASE = 14


def _prod(code):
    return type("P", (), {"code": code})()


def _line(code, n, dims=(40, 30, 20), w=5.0, color="#abcdef"):
    return {"product": _prod(code), "n_cartons": n, "carton_dims": dims,
            "carton_weight_kg": w, "color": color}


def _paz_line(code, pallets, cpp, dims=(40, 30, 20), w=5.0, color="#abcdef"):
    """A line ordered in PAZ (whole pallets): `pallets` × `cpp` cartons per pallet."""
    return {"product": _prod(code), "n_cartons": pallets * cpp, "carton_dims": dims,
            "carton_weight_kg": w, "color": color, "unit": "pal",
            "paz_pallets": pallets, "cartons_per_pallet": cpp}


def _labels(p):
    return {b["label"] for b in p["boxes"]}


def _box_weight(pallet, lines):
    wmap = {l["product"].code: l["carton_weight_kg"] for l in lines}
    return sum(wmap[b["label"]] for b in pallet["boxes"])


def _max_gap(pallet):
    """Largest air gap beneath any box (0 = everything rests flat on the base or a box
    below it). Small gaps from mixed-height layers are fine; big gaps = floating."""
    boxes = pallet["boxes"]
    worst = 0.0
    for b in boxes:
        bx, by, bz = b["pos"]; dl, dw, dh = b["dims"]
        bottom = by - dh / 2
        support = BASE
        for s in boxes:
            if s is b:
                continue
            stop = s["pos"][1] + s["dims"][2] / 2
            if (stop <= bottom + 0.6
                    and abs(bx - s["pos"][0]) < (dl + s["dims"][0]) / 2 - 0.1
                    and abs(bz - s["pos"][2]) < (dw + s["dims"][1]) / 2 - 0.1):
                support = max(support, stop)
        worst = max(worst, bottom - support)
    return worst


class FfdShipmentPackingTests(SimpleTestCase):
    def _pack(self, lines, max_h=200, max_w=1000):
        return _build_shipment_three_data_ffd({"lines": lines}, max_h=max_h, max_w=max_w)

    def test_dispatcher_uses_ffd(self):
        # The default builder is FFD now (not the scattering py3dbp optimizer).
        data = _build_shipment_three_data({"lines": [_line("A", 10)]}, max_h=200, max_w=1000)
        self.assertIsNotNone(data)
        self.assertEqual(sum(len(p["boxes"]) for p in data["pallets"]), 10)

    def test_all_cartons_placed(self):
        data = self._pack([_line("A", 30), _line("B", 12, color="#f59e0b")])
        self.assertEqual(sum(len(p["boxes"]) for p in data["pallets"]), 42)

    def test_no_floating_boxes(self):
        data = self._pack([_line("A", 50), _line("B", 7, dims=(50, 40, 25))])
        for p in data["pallets"]:
            self.assertLessEqual(_max_gap(p), 8.0, "a carton is floating mid-air")

    def test_mixed_height_no_perching(self):
        # Tall (30 cm) + short (10 cm) SKUs share pool pallets. Without gravity-settling the
        # cartons above the short ones float by ~20 cm; settling must drop them onto support.
        data = self._pack([_line("T", 24, dims=(40, 30, 30)),
                           _line("S", 24, dims=(40, 30, 10), color="#f59e0b")])
        for p in data["pallets"]:
            self.assertLessEqual(_max_gap(p), 2.0, "carton floats above a shorter neighbour")

    def test_weight_cap_respected(self):
        # 50 kg cartons, 1000 kg cap → at most 20 per pallet regardless of height.
        lines = [_line("HEAVY", 100, dims=(40, 30, 20), w=50.0)]
        data = self._pack(lines, max_h=240, max_w=1000)
        for p in data["pallets"]:
            self.assertLessEqual(_box_weight(p, lines), 1000 + 1e-6)

    def test_layer_uses_90_turn(self):
        # 40×30 carton: straight = 3×2 = 6 per layer, turned 90° = 4×2 = 8 per layer.
        # With room for exactly ONE layer (max_h=34 → 20 cm cargo), eight cartons fit on a
        # single mono pallet only because of the 90° turn (without it, 6 fit + 2 spill to a
        # second pallet).
        data = self._pack([_line("T", 8, dims=(40, 30, 20))], max_h=34)
        self.assertEqual(len(data["pallets"]), 1)
        p = data["pallets"][0]
        self.assertEqual(len(p["boxes"]), 8)
        self.assertEqual(len({round(b["pos"][1], 1) for b in p["boxes"]}), 1)  # one layer

    def test_paz_stays_whole_at_build_height(self):
        # 2 PAZ × 30 cartons/pallet, scenario = build height (2.25 m): two ready full
        # pallets of exactly 30 cartons each (NOT the geometric ~80), single SKU.
        data = self._pack([_paz_line("PAZ", 2, 30)], max_h=225)
        self.assertEqual(len(data["pallets"]), 2)
        for p in data["pallets"]:
            self.assertEqual(len(p["boxes"]), 30)
            self.assertEqual(_labels(p), {"PAZ"})
            self.assertFalse(p["mixed"])

    def test_paz_broken_down_below_build_height(self):
        # Same PAZ line at a 1.8 m truck: the original pallet must be broken into cartons
        # and repacked — not kept as 2×30.
        data = self._pack([_paz_line("PAZ", 2, 30)], max_h=180)
        self.assertEqual(sum(len(p["boxes"]) for p in data["pallets"]), 60)  # all cartons
        kept_whole = (len(data["pallets"]) == 2
                      and all(len(p["boxes"]) == 30 for p in data["pallets"]))
        self.assertFalse(kept_whole)                 # decomposed, not the ready 2×30

    def test_dominant_sku_not_scattered(self):
        data = self._pack([
            _line("DOM", 60, color="#3b82f6"),
            _line("X", 4, dims=(40, 30, 25), color="#ef4444"),
            _line("Y", 3, dims=(40, 30, 25), color="#10b981"),
        ])
        pallets = data["pallets"]
        mono = [p for p in pallets if _labels(p) == {"DOM"}]
        mixed_with_dom = [p for p in pallets if "DOM" in _labels(p) and len(_labels(p)) > 1]
        self.assertGreaterEqual(len(mono), 1)        # clean mono pallets for the big SKU
        self.assertLessEqual(len(mixed_with_dom), 1)  # at most one leftover mix


class DensePackSlackTests(SimpleTestCase):
    """Dense packing (fullest-fit) + the efficiency buffer shown as EMPTY slack pallets."""

    def test_dense_fill_with_empty_slack(self):
        lines = [_line("A", 18, dims=(40, 30, 30)),
                 _line("B", 14, dims=(60, 40, 20), color="#f59e0b")]
        # Estimate 5 pallets but the goods pack densely into fewer → rest are empty slack.
        data = _build_shipment_three_data_ffd({"lines": lines}, max_h=220, max_w=1000, target_bins=5)
        self.assertEqual(data["n_pallets"], 5)                       # quote count preserved
        empties = [p for p in data["pallets"] if p.get("empty")]
        filled = [p for p in data["pallets"] if not p.get("empty")]
        self.assertTrue(empties, "efficiency buffer should appear as empty slack pallets")
        self.assertLess(len(filled), 5, "dense packing uses fewer filled pallets")
        self.assertGreater(filled[0]["height_cm"], 150)             # built up, not spread thin
        # Slack pallets are the trailing ones (empty space at the end, not inside).
        self.assertTrue(data["pallets"][-1].get("empty"))

    def test_no_target_no_empty_padding(self):
        data = _build_shipment_three_data_ffd({"lines": [_line("A", 10)]}, max_h=200, max_w=1000)
        self.assertFalse(any(p.get("empty") for p in data["pallets"]))


class StackabilityCapTests(SimpleTestCase):
    """A non-stackable / layer-limited SKU must build more (shorter) pallets — the count
    can only go UP, so the quote is never under-estimated for fragile goods."""

    def test_max_layers_cap_increases_pallet_count(self):
        base = _line("X", 160, dims=(40, 30, 20), w=1.0)   # 20 cm tall → many layers fit
        unlimited = _build_shipment_three_data_ffd({"lines": [dict(base)]}, max_h=225, max_w=1000)
        capped_line = dict(base); capped_line["max_layers"] = 1   # single layer only
        capped = _build_shipment_three_data_ffd({"lines": [capped_line]}, max_h=225, max_w=1000)
        self.assertGreater(capped["n_pallets"], unlimited["n_pallets"])


class RenderCapTests(SimpleTestCase):
    """Huge loads keep an EXACT pallet count but cap rendered geometry (responsiveness)."""

    def test_large_load_count_exact_geometry_capped(self):
        line = _line("BIG", 250, dims=(115, 75, 200), w=1.0)   # 1 carton per pallet → 250 pallets
        d = _build_shipment_three_data_ffd({"lines": [line]}, max_h=225, max_w=1000)
        self.assertEqual(d["n_pallets"], 250)          # count stays exact
        self.assertEqual(d["rendered_pallets"], 200)   # geometry bounded
        self.assertTrue(d["truncated"])
        self.assertEqual(len(d["pallets"]), 200)

    def test_normal_load_not_truncated(self):
        d = _build_shipment_three_data_ffd({"lines": [_line("X", 20, dims=(40, 30, 20), w=2.0)]},
                                           max_h=225, max_w=1000)
        self.assertFalse(d["truncated"])
        self.assertEqual(d["rendered_pallets"], d["n_pallets"])


class ContainerLoadTests(SimpleTestCase):
    """Loose-into-container packing (no pallets) for the TIR/container view."""
    def _veh(self, key):
        from palletizer.services.vehicle_load import VEHICLES
        return next(v for v in VEHICLES if v["key"] == key)

    def test_loose_pack_valid_and_settled(self):
        from ui.views.core.helpers import _build_container_load
        calc = {"lines": [_line("A", 120, dims=(40, 30, 30)),
                          _line("B", 80, dims=(60, 40, 20), color="#f59e0b")]}
        v = self._veh("cont40")
        d = _build_container_load(calc, v)
        self.assertEqual(d["type"], "container")
        self.assertGreaterEqual(d["n_containers"], 1)
        self.assertEqual(d["total_cartons"], 200)
        self.assertGreater(d["fill_pct"], 0)
        # Every carton sits inside the container and rests on support (settled, no floating).
        for c in d["containers"]:
            for b in c["boxes"]:
                bx, by, bz = b["pos"]; dl, dw, dh = b["dims"]
                self.assertLessEqual(by + dh / 2, d["H"] + 1)
                self.assertLessEqual(abs(bx) + dl / 2, d["L"] / 2 + 1)
                self.assertLessEqual(abs(bz) + dw / 2, d["W"] / 2 + 1)

    def test_oversize_carton_skipped(self):
        from ui.views.core.helpers import _build_container_load
        calc = {"lines": [_line("HUGE", 3, dims=(900, 900, 900))]}   # bigger than any container
        self.assertIsNone(_build_container_load(calc, self._veh("cont20")))

    def test_empty_returns_none(self):
        from ui.views.core.helpers import _build_container_load
        self.assertIsNone(_build_container_load({"lines": []}, self._veh("naczepa")))

    def test_pallets_arranged_in_vehicle(self):
        from ui.views.core.helpers import _build_vehicle_pallet_load
        v = self._veh("naczepa")
        calc = {"lines": [_line("A", 60, dims=(40, 30, 30)),
                          _line("B", 40, dims=(60, 40, 20), color="#f59e0b")]}
        d = _build_vehicle_pallet_load(calc, v, 180)
        self.assertEqual(d["mode"], "pallets")
        self.assertGreaterEqual(d["n_pallets"], 1)
        self.assertEqual(d["slots_per_vehicle"], 33)        # 245//80=3 × 1360//120=11
        for c in d["containers"]:
            self.assertTrue(c["decks"])                     # pallet decks present
            self.assertEqual(len(c["decks"]) <= 33, True)
            for b in c["boxes"]:                            # boxes within vehicle footprint
                self.assertLessEqual(abs(b["pos"][0]), d["L"] / 2 + 60)
                self.assertLessEqual(abs(b["pos"][2]), d["W"] / 2 + 40)


class InterlockLayerTests(SimpleTestCase):
    """#5: uniform mono/solid blocks cross-stack — alternate layers are shifted by up to
    half a carton (clamped to the centring margin) for a denser, stable hand-built look,
    without changing the carton count or letting a box overhang the footprint."""

    def test_solid_block_alternate_layers_offset(self):
        from ui.views.core.helpers import _solid_block
        # cl=30 in PL=120 → nx=3 (90 cm), centring margin off_x=15 → brick shift = 15.
        boxes = _solid_block(30, 30, 25, "#f00", "A", nx=3, nz=2, lmax=2, PL=120, PW=80, base=BASE)
        self.assertEqual(len(boxes), 3 * 2 * 2)             # count unchanged
        ys = sorted({b["pos"][1] for b in boxes})
        l0 = sorted({b["pos"][0] for b in boxes if abs(b["pos"][1] - ys[0]) < 0.1})
        l1 = sorted({b["pos"][0] for b in boxes if abs(b["pos"][1] - ys[1]) < 0.1})
        self.assertNotEqual(l0, l1)                         # layers interlock (X offset)
        for b in boxes:                                     # never overhangs the 120×80 deck
            self.assertLessEqual(b["pos"][0] + b["dims"][0] / 2, 60 + 0.1)
            self.assertGreaterEqual(b["pos"][0] - b["dims"][0] / 2, -60 - 0.1)

    def test_no_margin_no_overhang(self):
        from ui.views.core.helpers import _solid_block
        # Tight grid (cl=40 → nx=3 = 120, off_x=0): brick clamps to 0, no shift, no overhang.
        boxes = _solid_block(40, 40, 25, "#f00", "A", nx=3, nz=2, lmax=2, PL=120, PW=80, base=BASE)
        for b in boxes:
            self.assertLessEqual(b["pos"][0] + b["dims"][0] / 2, 60 + 0.1)
