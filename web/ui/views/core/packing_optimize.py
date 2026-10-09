# core/ — shared kernel split into layers (base ◅ figures/xlsx/helpers ◅ packing).
# Re-exported wholesale by core/__init__.py so `from .core import *` is unchanged.
from .base import (
    CartonVariant, Dimensions, PALLET_BASE_HEIGHT_CM, PalletCalculator, PalletType,
    WarehouseLocationType, _COL_IDX, _fig_json, get_pallet_preset,
)
from .figures import _fig_location_2d_front, _fig_location_3d
from .ewm_levels import letter_slot
from .helpers import _location_fit
from .packing_core import _layers_by_weight  # noqa: F401

def _optimize_packaging(cl, cw, ch, pallet_code, load_height_cm, max_weight_kg,
                        tolerance_cm, carton_weight_kg):
    """Fit a cl×cw×ch carton onto a pallet, allowing `tolerance_cm` of overhang.

    The layout is packed onto a footprint enlarged by the tolerance (so cartons may
    slightly overhang), while fill % is measured against the REAL pallet to a given
    load height. Returns a dict the template renders directly.
    """
    p = get_pallet_preset(pallet_code)
    PL, PW = p.length_cm, p.width_cm
    cargo_h = max(0, int(load_height_cm) - PALLET_BASE_HEIGHT_CM)
    tol = max(0.0, float(tolerance_cm or 0))

    # Pack on the enlarged (tolerant) footprint.
    pack_pallet = PalletType(
        code=p.code,
        dims=Dimensions(l_cm=round(PL + 2 * tol), w_cm=round(PW + 2 * tol), h_cm=max(1, cargo_h)),
        max_weight_kg=p.max_weight_kg,
    ).validate()
    carton = CartonVariant(
        sku="OPT", variant="v1", dims=Dimensions(l_cm=cl, w_cm=cw, h_cm=ch),
        unit_weight_kg=max(0.001, float(carton_weight_kg or 0) or 0.001),
        pieces_per_carton=1, carton_tare_kg=0.0, demand_pieces=1,
    )
    try:
        carton.validate()
        opts = PalletCalculator.generate_layout_options(carton, pack_pallet)
    except Exception:
        opts = []
    if not opts:
        return {"ok": False, "PL": PL, "PW": PW, "cargo_h": cargo_h}

    best = opts[0]
    per_layer = best.cartons_per_layer
    layers_by_h = cargo_h // ch if ch else 0
    cw_kg = float(carton_weight_kg or 0)
    layers_by_w = _layers_by_weight(per_layer, cw_kg, max_weight_kg, layers_by_h)
    layers = max(0, min(layers_by_h, layers_by_w))
    cpp = per_layer * layers

    carton_vol = cl * cw * ch
    cargo_height_used = layers * ch
    pallet_usable_vol = PL * PW * cargo_h
    used_vol = cpp * carton_vol
    fill_volume = round(used_vol / pallet_usable_vol * 100, 1) if pallet_usable_vol else 0.0
    base_pct = round(per_layer * cl * cw / (PL * PW) * 100, 1) if PL * PW else 0.0
    height_pct = round(cargo_height_used / cargo_h * 100, 1) if cargo_h else 0.0

    # Overhang actually used by the chosen layout — po WYCENTROWANIU układu na palecie.
    # Packing biegnie od rogu ramki PL+2·tol, więc bez centrowania cały 2·tol potrafił
    # wylądować po JEDNEJ stronie (spec: tolerancja PER strona).
    max_x = max((pl.x + pl.dx for pl in best.placements), default=0)
    max_y = max((pl.y + pl.dy for pl in best.placements), default=0)
    # Po centrowaniu zwis rozkłada się symetrycznie: (szerokość układu − paleta)/2 na stronę.
    overhang_x = max(0.0, (max_x - PL) / 2.0)
    overhang_y = max(0.0, (max_y - PW) / 2.0)

    frame_w = PL + 2 * tol
    frame_h = PW + 2 * tol
    return {
        "ok": cpp > 0,
        "PL": PL, "PW": PW, "cargo_h": cargo_h, "tol": round(tol, 1),
        "frame_w": round(frame_w, 1), "frame_h": round(frame_h, 1),
        "vb": f"-6 -6 {round(frame_w + 12, 1)} {round(frame_h + 12, 1)}",
        "carton": {"l": cl, "w": cw, "h": ch},
        "per_layer": per_layer, "layers": layers, "cartons_per_pallet": cpp,
        "best_name": best.name.split("_")[0],
        # Wycentrowane w ramce (spójnie z symetrycznym zwisem wyżej).
        "placements": [{"x": round(pl.x + (frame_w - max_x) / 2.0, 1),
                        "y": round(pl.y + (frame_h - max_y) / 2.0, 1),
                        "dx": pl.dx, "dy": pl.dy, "rot": pl.rotated} for pl in best.placements],
        "fill_volume": fill_volume, "base_pct": base_pct, "height_pct": height_pct,
        "overhang_x": overhang_x, "overhang_y": overhang_y,
        "weight_per_pallet": round(cpp * cw_kg, 1),
        "carton_weight_kg": round(float(carton_weight_kg or 0), 2),
        "n_options": len(opts),
    }

def _factorizations(n):
    """All (a, b, c) with a*b*c == n (used to shape a carton holding n units)."""
    out = []
    for a in range(1, n + 1):
        if n % a:
            continue
        m = n // a
        for b in range(1, m + 1):
            if m % b:
                continue
            out.append((a, b, m // b))
    return out

def _best_box(count, ul, uw, uh):
    """Most compact box holding `count` units (min volume, avoid thin shapes)."""
    units = {(ul, uw, uh), (ul, uh, uw), (uw, ul, uh), (uw, uh, ul), (uh, ul, uw), (uh, uw, ul)}
    best = None
    for a, b, c in _factorizations(int(count)):
        for du, dv, dw in units:
            d = (a * du, b * dv, c * dw)
            key = (max(d) > 6 * min(d), d[0] * d[1] * d[2], max(d) / min(d))
            if best is None or key < best[0]:
                best = (key, d)
    return best[1] if best else (ul, uw, uh)

def _suggest_packaging(ul, uw, uh, pallet_code, height_cm, max_weight_kg, tolerance_cm,
                       unit_weight_kg, target_pct, max_units, median_qty, units_multiplier=1):
    """Reverse mode: search how many base units per carton (and the carton shape that
    holds them) best fills the pallet — optionally toward a target % and toward whole
    cartons per a typical (median) order."""
    # distinct base-unit orientations (the unit may sit any way up in the carton)
    unit_orients = {(ul, uw, uh), (ul, uh, uw), (uw, ul, uh),
                    (uw, uh, ul), (uh, ul, uw), (uh, uw, ul)}

    results = []
    seen = set()
    evaluated = 0
    for n in range(1, int(max_units) + 1):
        for a, b, c in _factorizations(n):
            for du, dv, dw in unit_orients:
                cl, cw, ch = a * du, b * dv, c * dw
                # Footprint rotation (L↔W) packs identically on the pallet — dedup it,
                # keeping height distinct, so 15×36×20 and 36×15×20 aren't both solved.
                key = (n, tuple(sorted((cl, cw))), ch)
                if key in seen:
                    continue
                seen.add(key)
                # skip impractically thin/long cartons (e.g. 100×8×6 sticks)
                if max(cl, cw, ch) > 6 * min(cl, cw, ch):
                    continue
                if evaluated >= 600:                 # hard cap — bound worst-case cost
                    break
                evaluated += 1
                r = _optimize_packaging(cl, cw, ch, pallet_code, height_cm, max_weight_kg,
                                        tolerance_cm, n * float(unit_weight_kg or 0))
                if not r["ok"]:
                    continue
                pc = n * units_multiplier            # products per carton (× inner-pack size)
                r["units_per_carton"] = n            # base units (inner-packs) per carton
                r["products_per_carton"] = pc
                r["arrange"] = f"{a}×{b}×{c}"
                r["whole_cartons"] = bool(median_qty and pc and median_qty % pc == 0)
                r["units_per_pallet"] = r["cartons_per_pallet"] * pc
                results.append(r)

    tgt = float(target_pct) if target_pct else None
    def _score(r):
        key = abs(r["fill_volume"] - tgt) if tgt else -r["fill_volume"]
        return (key, 0 if r["whole_cartons"] else 1, -r["fill_volume"])
    results.sort(key=_score)
    return results[:8]

def _build_stack_base(layout):
    """Return dict (aisle, stack) -> (base_grid_col, base_grid_row).

    Supports both formats:
      3-part: B0-01-100A  → aisle=01, stack=100, col=A, level from COL_MAP
      4-part: B0-01-100-1A → aisle=01, stack=100, level=1, col=A (explicit level)
    base_col = grid_col of col_idx=0 cell.
    base_row = grid_row of any level-1 cell in that stack.
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for c in layout.cells.all():
        parts = c.location_code.split('-')
        if len(parts) < 3:
            continue
        aisle_p = parts[1].zfill(2)
        raw2 = parts[2]
        if raw2 and raw2[-1].isalpha():
            # 'B0-01-100A' or sub-shelf 'B0-07-300C-1' (parts[2] == '300C', parts[3]
            # is just the sub-index) — stack is the digits, col is the trailing letter.
            col_code = raw2[-1].upper()
            stack_p = raw2[:-1]
            # Litera EWM = poziom/półka jednego stosu → kolumna 0 (jak przy imporcie mapy).
            ewm_col = 0 if letter_slot(parts[0], col_code) else None
        elif len(parts) >= 4:
            # generator 4-part 'B0-01-100-1A': stack is parts[2], col from parts[3].
            import re as _re
            m = _re.match(r'^\d*([A-Za-z]+)$', parts[3])
            col_code = m.group(1)[-1].upper() if m else 'A'
            stack_p = raw2
            ewm_col = None                 # generator: litera = kolumna w boku
        else:
            col_code = 'A'
            stack_p = raw2
            ewm_col = 0
        col_idx = ewm_col if ewm_col is not None else _COL_IDX.get(col_code, 0)
        groups[(aisle_p, stack_p)].append({
            'grid_col': c.grid_col,
            'grid_row': c.grid_row,
            'col_code': col_code,
            'col_idx': col_idx,
            'level': c.level,
        })

    base = {}
    for key, cells in groups.items():
        lvl1 = [c for c in cells if c['level'] == 1] or cells
        base_row = lvl1[0]['grid_row']
        a_cells = [c for c in lvl1 if c['col_code'] == 'A']
        if a_cells:
            base_col = a_cells[0]['grid_col']
        else:
            s = sorted(lvl1, key=lambda c: c['col_idx'])
            base_col = s[0]['grid_col'] - s[0]['col_idx']
        base[key] = (base_col, base_row)
    return base

def _build_loc_fits(cl, cw, ch, uw, ppc, tare, fit_ids):
    """For each ticked location id: verdict (_location_fit) + 3D slot and 2D front-view
    figures. Shared by the calculator and the variant-switch panel so the inline location
    visualisation stays consistent and survives layout switching."""
    out = []
    for loc in WarehouseLocationType.objects.filter(pk__in=fit_ids, is_active=True).order_by("location_class", "name"):
        f = _location_fit(loc, cl, cw, ch, uw, ppc, tare)
        fig3d, _ = _fig_location_3d(loc, cl, cw, ch, uw, ppc, tare, with_pallet=f["with_pallet"])
        fig2d = _fig_location_2d_front(loc, cw, ch, f)
        f["fig3d_json"] = _fig_json(fig3d)
        f["fig2d_json"] = _fig_json(fig2d)
        out.append(f)
    return out



__all__ = [
    '_best_box', '_build_loc_fits', '_build_stack_base', '_factorizations',
    '_optimize_packaging', '_suggest_packaging',
]
