# core/ — shared kernel split into layers (base ◅ figures/xlsx/helpers ◅ packing).
# Re-exported wholesale by core/__init__.py so `from .core import *` is unchanged.
from .base import Product, ShipmentLine, openpyxl
from .helpers_shipment_calc import _DEFAULT_PALLET_HEIGHT, _MAX_PALLET_WEIGHT_KG, _PAZ_BUILD_HEIGHT_CM, _settle_boxes  # noqa: F401
from .helpers_shipment_ffd_fit import (  # geometria/limity FFD (liść, bez star-eksportu)
    _FFD_BASE, _FFD_OVER, _FFD_PL, _FFD_PW, _ffd_layer_orientation, _ffd_limit_warnings, _ffd_num,
    _ffd_oversize, _ffd_pool_footprint,
)

def _build_shipment_three_data(calc, max_h=_DEFAULT_PALLET_HEIGHT, max_w=_MAX_PALLET_WEIGHT_KG,
                               target_bins=None, render_cap=200):
    """Build the 3D pallet layout for the given load HEIGHT (cm).

    FFD (neat, full single-SKU layers; heavy SKUs first; weight-capped) is the default —
    it stacks the way a person would, without the scattered, floating-carton look the
    py3dbp optimizer produced. py3dbp is kept only as a fallback. `target_bins` aims the
    layout at a given pallet count (the efficiency-calibrated estimate) so the drawing
    matches the headline; the returned n_pallets is whatever was actually drawn."""
    data = _build_shipment_three_data_ffd(calc, max_h, max_w, target_bins, render_cap=render_cap)
    if data:
        return data
    return _build_shipment_three_data_py3dbp(calc, max_h, max_w)


def _build_shipment_three_data_py3dbp(calc, max_h=_DEFAULT_PALLET_HEIGHT, max_w=_MAX_PALLET_WEIGHT_KG):
    """Optimize the mixed pallet with py3dbp (rotation + per-pallet weight cap).

    Cartons are packed biggest-first into pallet bins; py3dbp tries rotations and
    respects each pallet's max weight. Returns the same structure as the FFD packer,
    or None to fall back (too many cartons, or nothing packs)."""
    import math, copy
    PL, PW, BASE = 120, 80, 14
    if not calc["lines"]:
        return None
    cargo_h = max(1, max_h - BASE)
    if sum(lc["n_cartons"] for lc in calc["lines"]) > 2000:  # fallback only; keep it responsive
        return None

    try:
        from ...vendor.py3dbp import Packer, Bin, Item
    except Exception:
        return None

    meta = {}
    _seq = [0]

    def _pack(specs, n_bins):
        """Pack (dims, wkg, color, label) specs into n_bins pallets; returns the Packer."""
        packer = Packer()
        for b in range(n_bins):
            packer.add_bin(Bin(f"P{b}", PL, cargo_h, PW, max_w))
        for (cl, cw, ch), wkg, color, label in specs:
            name = str(_seq[0]); _seq[0] += 1
            meta[name] = (color, label)
            packer.add_item(Item(name, cl, ch, cw, wkg))   # (w=x, h=y, d=z)
        # bigger_first: pack the largest cartons first so they form a dense, stable
        # base and the small ones fill the gaps on top — denser pallets, fewer of them,
        # and a more natural hand-stacked look.
        packer.pack(bigger_first=True, distribute_items=True, number_of_decimals=2)
        return packer

    def _settle(boxes):
        """Gravity-drop the py3dbp boxes (it packs against faces without gravity)."""
        return _settle_boxes(boxes, BASE)

    def _pallet(b):
        boxes = []
        for it in b.items:
            w, h, d = (float(x) for x in it.get_dimension())   # rotated dims
            px, py, pz = (float(x) for x in it.position)
            color, label = meta[it.name]
            boxes.append({
                "dims": [round(w, 1), round(d, 1), round(h, 1)],
                "color": color, "label": label,
                "pos": [round(px + w / 2 - PL / 2, 1), round(BASE + py + h / 2, 1),
                        round(pz + d / 2 - PW / 2, 1)]})
        _settle(boxes)
        # A pallet carrying >1 SKU is a "mix" pallet — combined leftovers that a person
        # would stack into a shared (mixed) pallet. Flag it so the 3D view can mark it.
        mixed = len({bx["label"] for bx in boxes}) > 1
        for bx in boxes:
            bx["mix"] = mixed
        top = max((bx["pos"][1] + bx["dims"][2] / 2 for bx in boxes), default=BASE)
        return {"boxes": boxes, "height_cm": round(top, 1), "mixed": mixed}

    def _group_vol(lc):
        cl, cw, ch = lc["carton_dims"]
        return lc["n_cartons"] * cl * cw * ch

    # Warehouse logic: build FULL single-SKU pallets first (a SKU is never scattered
    # across mixed pallets), then combine only the leftovers into shared mixed pallets.
    out, remainder = [], []
    for lc in sorted(calc["lines"], key=_group_vol, reverse=True):
        spec = (lc["carton_dims"], lc.get("carton_weight_kg") or 1.0,
                lc["color"], lc["product"].code)
        qty = lc["n_cartons"]
        if qty <= 0:
            continue
        cap = len(_pack([spec] * qty, 1).bins[0].items)   # how many of this SKU fit on one pallet
        if cap <= 0:
            return None
        # A SKU that fills at least one whole pallet keeps those as dedicated mono
        # pallets; only the sub-pallet remainder joins the shared mixed pool. `cap <= qty`
        # (not `<`) so a SKU that fills exactly one pallet still gets a clean mono pallet
        # instead of being scattered into a mix.
        full = qty // cap if cap <= qty else 0
        if full:
            mono = _pallet(_pack([spec] * cap, 1).bins[0])    # one mono pallet, replicated
            out.extend(copy.deepcopy(mono) for _ in range(full))
        remainder += [spec] * (qty - full * cap)              # leftover joins the mixed pool

    # Pack the mixed leftovers, growing the pallet count until everything fits.
    if remainder:
        pallet_vol = PL * PW * cargo_h / 1_000_000
        rem_vol = sum(s[0][0] * s[0][1] * s[0][2] for s in remainder) / 1_000_000
        rem_w = sum(s[1] for s in remainder)
        n_bins = max(1, math.ceil(rem_vol / (pallet_vol * 0.8)) if pallet_vol else 1,
                     math.ceil(rem_w / max_w) if max_w else 1)
        for _ in range(6):
            packer = _pack(remainder, n_bins)
            packed = sum(len(b.items) for b in packer.bins)
            if packed >= len(remainder):
                break
            n_bins += max(2, (len(remainder) - packed) // 10 + 1)
        else:
            return None   # couldn't fit the leftovers — let FFD handle it
        out.extend(_pallet(b) for b in packer.bins if b.items)

    if not out:
        return None
    return {
        "type": "shipment", "pallet_l": PL, "pallet_w": PW, "pallet_base": BASE,
        "max_h_cm": max_h, "n_pallets": len(out), "pallets": out,
    }


def _build_shipment_three_data_ffd(calc, max_h=_DEFAULT_PALLET_HEIGHT, max_w=_MAX_PALLET_WEIGHT_KG,
                                   target_bins=None, render_cap=200):
    """Pack the shipment into as few pallets as possible, the way a person would.

    Full single-SKU layers first (heaviest/biggest SKU first so it forms the base);
    cartons may be turned 90° in the layer to fit more per row. Each mono pallet is
    capped by BOTH the load height and the per-pallet weight limit. Only the sub-pallet
    remainders go into a shared pool packed in flat, full layers (cartons of the same
    height per layer) so the load rests on support — no floating — and stays neat;
    pallets keep their natural (unequal) height for the measure. Kartony rysowane mimo
    przekroczenia limitu wagi/wysokości palety → klucz "warnings" (lista komunikatów PL;
    obecny tylko, gdy są ostrzeżenia)."""
    if not calc["lines"]:
        return None
    mw = max_w or 0              # 0 → no weight limit
    pallets, cartons = _ffd_split_lines(calc["lines"], max_h, mw)
    if not cartons and not pallets:
        return None
    # Remainders: spread across a TARGET number of pallets (= the quoted count), so the
    # picture shows EXACTLY as many pallets as the headline. Each pallet is filled in flat,
    # full layers (cartons drop onto a level surface — no perching/floating); best-fit by
    # current fill balances the load across the target. A pallet is added only if a carton
    # fits in none, so the drawn count never silently disagrees with the layout.
    cartons.sort(key=lambda c: (-c[2], -c[6], c[4]))   # height, then weight, then SKU
    bins, repacked = _ffd_pack_pool(cartons, len(pallets), max_h, mw, target_bins)
    pallets.extend(b["boxes"] for b in bins if b["boxes"])
    # Preserve the efficiency-calibrated quote count: if dense packing needed fewer pallets
    # than the estimate, pad with EMPTY slack pallets so the headline (which carries the
    # hand-stacking buffer) is unchanged and the slack shows as loose pallets at the end.
    if target_bins:
        pallets.extend([] for _ in range(target_bins - len(pallets)))
    total_pallets = len(pallets)
    # Pathologically large loads: keep the pallet COUNT exact, but only the first
    # render_cap pallets carry full box geometry so the 3D payload stays bounded (the
    # view flags the truncation). Normal quotes (≪ cap) are unaffected — no behaviour change.
    # render_cap = None → bez przycięcia (generowanie HU MUSI pokryć każdą paletę, inaczej
    # ogon >200 palet zostaje bez HU i kontrola/gotowość go nie widzą).
    out = [_ffd_render_pallet(boxes) for boxes in pallets[:render_cap]]
    data = {
        "type": "shipment",
        "pallet_l": _FFD_PL,
        "pallet_w": _FFD_PW,
        "pallet_base": _FFD_BASE,
        "max_h_cm": max_h,
        "n_pallets": total_pallets,                      # exact count (may exceed rendered)
        "rendered_pallets": len(out),
        "truncated": total_pallets > len(out),
        "pallets": out,
    }
    warnings = _ffd_limit_warnings(calc["lines"], max_h, mw)
    if repacked:
        warnings += _ffd_repack_weight_warning(bins, mw)
    if warnings:
        data["warnings"] = warnings
    return data


def _ffd_split_lines(lines, max_h, mw):
    """Full mono pallets (box-lists) + the sub-pallet remainder cartons for the shared pool.

    SKUs go biggest first — by total volume, then carton count — so the dominant indexes
    get their pallets first and form the base of mixed ones. A SKU that fills whole
    pallets keeps them whole and dedicated; only the sub-pallet remainder is thrown into
    the shared FFD pool, so a full original pallet is never scattered to fill other gaps."""
    ordered = sorted(
        (lc for lc in lines if lc["n_cartons"] > 0),
        key=lambda lc: (lc["n_cartons"] * lc["carton_dims"][0] * lc["carton_dims"][1]
                        * lc["carton_dims"][2], lc["n_cartons"]),
        reverse=True)
    pallets = []        # finished pallets (list of box-lists)
    cartons = []        # (l, w, h, color, label, total_v, wkg) remainder → shared pool
    for lc in ordered:
        _ffd_split_line(lc, max_h, mw, pallets, cartons)
    return pallets, cartons


def _ffd_split_line(lc, max_h, mw, pallets, cartons):
    """One SKU → its full mono pallets (appended to `pallets`) + remainder (to `cartons`)."""
    l, w, h = lc["carton_dims"]
    wkg = lc.get("carton_weight_kg") or 1.0
    color, label = lc["color"], lc["product"].code
    n = lc["n_cartons"]
    total_v = n * l * w * h          # SKU total volume — drives the pool order
    # Karton większy niż stopa palety w OBU orientacjach — max(1,...) niżej udawałby,
    # że „mieści się 1/warstwę" i budował mono-palety wystające poza 120×80. Pomiń SKU
    # w 3D (dane opakowania do poprawy — sygnalizuje to MATinfo/master data).
    if _ffd_oversize(l, w):
        return
    # Orientacja warstwy (obrót o 90°) z większą liczbą kartonów/warstwę — tylko spośród
    # mieszczących się w obrysie + tolerancji (remis → wejściowa, jeśli się mieści).
    cl, cw = _ffd_layer_orientation(l, w)
    nx, nz = max(1, (_FFD_PL + 2 * _FFD_OVER) // cl), max(1, (_FFD_PW + 2 * _FFD_OVER) // cw)
    per_layer = nx * nz
    lmax_h = _ffd_height_layers(lc, h, max_h)
    # PAZ (ordered in whole pallets) at/above the warehouse build height stays a ready
    # full pallet — not decomposed and never mixed. Below the build height it falls
    # through to normal carton packing (n_cartons was already expanded via PAZ→KAR).
    if _ffd_is_ready_paz(lc, max_h):
        count = lc.get("cartons_per_pallet") or (per_layer * lmax_h)
        for _ in range(int(lc["paz_pallets"])):
            pallets.append(_mono_pallet_block(cl, cw, h, color, label, nx, nz,
                                              int(count), _FFD_PL, _FFD_PW, _FFD_BASE))
        return
    lmax = _ffd_weight_layers(per_layer, wkg, mw, lmax_h)
    cpp = per_layer * lmax
    if cpp >= 1:
        while n >= cpp:
            pallets.append(_solid_block(cl, cw, h, color, label, nx, nz, lmax,
                                        _FFD_PL, _FFD_PW, _FFD_BASE))
            n -= cpp
    cartons.extend((l, w, h, color, label, total_v, wkg) for _ in range(n))


def _ffd_weight_layers(per_layer, wkg, mw, lmax_h):
    """Cap layers by the per-pallet weight limit too (a full layer may be heavy)."""
    lmax_w = int(mw // (per_layer * wkg)) if (mw and per_layer * wkg > 0) else lmax_h
    return min(lmax_h, lmax_w)


def _ffd_height_layers(lc, h, max_h):
    """Layers that fit the load height, capped by the SKU's stackability (max_layers)."""
    lmax_h = max(1, int(max(1, max_h - _FFD_BASE) // h))
    sku_max_layers = lc.get("max_layers") or 0
    if sku_max_layers:
        lmax_h = min(lmax_h, sku_max_layers)
    return lmax_h


def _ffd_is_ready_paz(lc, max_h):
    return (lc.get("unit") == "pal" and max_h >= _PAZ_BUILD_HEIGHT_CM
            and (lc.get("paz_pallets") or 0) > 0)


def _ffd_pack_pool(cartons, n_mono, max_h, mw, target_bins):
    """Pack the remainder pool; returns the pool bins.

    Packs naturally within the load height first. If that would need MORE pallets than
    the quote budgeted after the mono pallets (loose shelf-flow can't reach the assumed
    density), re-packs everything into EXACTLY the budgeted number, spreading evenly and
    stacking taller (height-uncapped) so the picture matches the headline — „dociśnij do
    wyceny". → (bins, czy_przepakowano)."""
    pool_target = max(0, target_bins - n_mono) if target_bins else None
    bins = _ffd_fill(cartons, max_h, mw)
    if pool_target and sum(1 for b in bins if b["boxes"]) > pool_target:
        bins = [_ffd_new_bin() for _ in range(pool_target)]
        for (l, w, h, color, label, _v, wkg) in cartons:
            b = min(bins, key=lambda bb: bb["vol"])      # emptiest pallet → even towers
            _ffd_apply(b, l, w, h, color, label, wkg, *_ffd_coords(b, l, w))
        return bins, True
    return bins, False


def _ffd_repack_weight_warning(bins, mw):
    """„Dociśnij do wyceny” ignoruje limity palety (zamierzone dla wysokości); przekroczony
    limit WAGI to realne ryzyko przeładowania — scena to mówi (rozmieszczenie bez zmian)."""
    over = [b["wt"] for b in bins if b["boxes"] and mw and b["wt"] > mw]
    if not over:
        return []
    return [f"Dociśnięcie do wyceny ({len(bins)} palet): {len(over)} palet przekracza limit "
            f"wagi {_ffd_num(mw)} kg (maks. {_ffd_num(max(over))} kg)"]


def _ffd_new_bin():
    return {"boxes": [], "base": float(_FFD_BASE), "x": 0.0, "z": 0.0,
            "row_depth": 0.0, "layer_h": 0.0, "wt": 0.0, "vol": 0.0}


def _ffd_coords(b, l, w):
    """Where the carton lands in this pallet's layered flow (row → next row → next layer)."""
    base, x, z, rd, lh = b["base"], b["x"], b["z"], b["row_depth"], b["layer_h"]
    if x + l > _FFD_PL + _FFD_OVER:                      # current row full
        if z + rd + w > _FFD_PW + _FFD_OVER:             # footprint full → next layer
            base += lh
            x = z = rd = lh = 0.0
        else:                                            # next row in the same layer
            z += rd
            x = 0.0
            rd = 0.0
    return base, x, z, rd, lh


def _ffd_apply(b, l, w, h, color, label, wkg, base, x, z, rd, lh):
    b["base"], b["x"], b["z"] = base, x + l, z
    b["row_depth"], b["layer_h"] = max(rd, w), max(lh, h)
    b["wt"] += wkg
    b["vol"] += l * w * h
    b["boxes"].append({
        "dims": [l, w, h], "color": color, "label": label,
        "pos": [round(x + l / 2 - _FFD_PL / 2, 1),
                round(base + h / 2, 1),
                round(z + w / 2 - _FFD_PW / 2, 1)]})


def _ffd_best_bin(bins, l, w, h, wkg, usable_top, mw):
    """The FULLEST pallet the carton still fits (height + weight), as (bin, *coords)."""
    best = None
    for b in bins:
        base, x, z, rd, lh = _ffd_coords(b, l, w)
        if base + h <= usable_top + 0.1 and (not mw or b["wt"] + wkg <= mw + 1e-6):
            if best is None or b["vol"] > best[0]["vol"]:    # FULLEST fitting → dense
                best = (b, base, x, z, rd, lh)
    return best


def _ffd_fill(items, usable_top, mw):
    """Natural fullest-fit within the load height: open a new pallet only when a carton
    fits in none, so dense pallets come first and any slack lands at the end."""
    bins = []
    for (l, w, h, color, label, _v, wkg) in items:
        fp = _ffd_pool_footprint(l, w)
        if fp is None:
            continue
        l, w = fp
        best = _ffd_best_bin(bins, l, w, h, wkg, usable_top, mw)
        if best is None:                                         # fits nowhere → new pallet
            b = _ffd_new_bin()
            bins.append(b)
            best = (b,) + _ffd_coords(b, l, w)
        _ffd_apply(best[0], l, w, h, color, label, wkg, *best[1:])
    return bins


def _ffd_render_pallet(boxes):
    if not boxes:                                        # empty slack pallet (the buffer)
        return {"boxes": [], "height_cm": float(_FFD_BASE), "mixed": False, "empty": True}
    # Gravity-settle so mixed-height layers don't leave upper cartons floating: a
    # box advancing past a tall neighbour must drop onto whatever truly supports it.
    _settle_boxes(boxes, _FFD_BASE)
    top = max((bb["pos"][1] + bb["dims"][2] / 2) for bb in boxes)
    mixed = len({bb["label"] for bb in boxes}) > 1   # >1 SKU → mix pallet
    for bb in boxes:
        bb["mix"] = mixed
    return {"boxes": boxes, "height_cm": round(top, 1), "mixed": mixed, "empty": False}


def _mono_pallet_block(cl, cw, ch, color, label, nx, nz, count, PL, PW, base):
    """A single mono-SKU pallet holding exactly `count` cartons — full nx×nz layers plus a
    partial top layer. Used for ready PAZ pallets (built to the warehouse build height)."""
    import math
    boxes = []
    off_x = (PL - nx * cl) / 2
    off_z = (PW - nz * cw) / 2
    # Cross-stacking: shift every other layer by up to half a carton so the stack interlocks
    # like a hand-built pallet (denser, more stable look). Clamped to the centring margin so
    # nothing overhangs the footprint, and the layer still rests fully on the one below.
    brick = min(cl / 2, off_x)
    layers = max(1, math.ceil(count / max(1, nx * nz)))
    placed = 0
    for ly in range(layers):
        dx = brick if (ly % 2) else 0.0
        for xi in range(nx):
            for zi in range(nz):
                if placed >= count:
                    break
                boxes.append({
                    "dims": [cl, cw, ch], "color": color, "label": label,
                    "pos": [round(off_x + dx + xi * cl + cl / 2 - PL / 2, 1),
                            round(base + ly * ch + ch / 2, 1),
                            round(off_z + zi * cw + cw / 2 - PW / 2, 1)]})
                placed += 1
            if placed >= count:
                break
    return boxes


def _solid_block(cl, cw, ch, color, label, nx, nz, lmax, PL, PW, base):
    """A full uniform single-SKU pallet: nx×nz grid, lmax layers."""
    boxes = []
    off_x = (PL - nx * cl) / 2
    off_z = (PW - nz * cw) / 2
    # Cross-stacking: alternate layers shift by up to half a carton (clamped to the centring
    # margin → no overhang), so a mono pallet interlocks like a real hand-built load.
    brick = min(cl / 2, off_x)
    for ly in range(lmax):
        dx = brick if (ly % 2) else 0.0
        for xi in range(nx):
            for zi in range(nz):
                boxes.append({
                    "dims": [cl, cw, ch], "color": color, "label": label,
                    "pos": [round(off_x + dx + xi * cl + cl / 2 - PL / 2, 1),
                            round(base + ly * ch + ch / 2, 1),
                            round(off_z + zi * cw + cw / 2 - PW / 2, 1)]})
    return boxes


def _import_shipment_lines_excel(shipment, file_obj):
    """Import shipment lines from Excel: columns code, qty, unit (optional)."""
    try:
        wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return
        # Auto-detect header row
        header = [str(c).strip().lower() if c else "" for c in rows[0]]
        col_code = next((i for i, h in enumerate(header) if "kod" in h or "code" in h or "indeks" in h), 0)
        col_qty  = next((i for i, h in enumerate(header) if "il" in h or "qty" in h or "ilosc" in h or "ilość" in h), 1)
        col_unit = next((i for i, h in enumerate(header) if "jedn" in h or "unit" in h), None)

        order_start = shipment.lines.count()
        for ri, row in enumerate(rows[1:], start=order_start):
            try:
                code = str(row[col_code]).strip() if row[col_code] else ""
                qty_raw = row[col_qty]
                if not code or qty_raw is None:
                    continue
                qty = float(str(qty_raw).replace(",", "."))
                if qty <= 0:
                    continue
                unit = "kar"
                if col_unit is not None and row[col_unit]:
                    u = str(row[col_unit]).strip().lower()
                    if "pal" in u:
                        unit = "pal"
                    elif "szt" in u or "pcs" in u or "pc" in u:
                        unit = "szt"
                product = Product.objects.filter(code=code).first()
                if product:
                    ShipmentLine.objects.create(
                        shipment=shipment, product=product,
                        quantity=qty, unit=unit, order=ri,
                    )
            except (ValueError, IndexError, TypeError):
                continue
    except Exception as exc:
        raise ValueError(f"Nie można wczytać pliku Excel z liniami shipmentu: {exc}") from exc


__all__ = ['_build_shipment_three_data', '_import_shipment_lines_excel']
