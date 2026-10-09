"""Silnik wypełnienia modułu „Optymalizacja kartonów" — czyste funkcje pakowania.

Wydzielone z carton_opt_variants (CODE-001, limit 500 linii): packer poziomu hierarchii
(pack_into), wypełnienie palety dla wymiarów wariantu (_variant_fill) i auto-sugestie
wymiarów (_suggest_dims). Reużywają silnika PalletCalculator — zero nowej geometrii.
carton_opt_variants re-eksportuje je bez zmian (__all__, importy w carton_opt_redesign).
"""
from .core import (
    PalletType, Dimensions, CartonVariant, PalletCalculator, _eval_layouts, _build_pallet,
)
from .carton_opt_issues import _vol_fill


def pack_into(container_lwh, unit_lwh, unit_weight_kg=0.001):
    """Bezwymiarowy packer poziomu hierarchii: ile jednostek unit_lwh mieści się
    w kontenerze container_lwh. Reużywa PalletCalculator — kontener = „paleta" o
    footprincie L×W i limicie wysokości H, waga praktycznie nieograniczona — zero
    nowej geometrii. Fundament kaskady sztuka→OPZ→karton→paleta.
    Zwraca {count, per_layer, layers, fill, placements, error}."""
    cl, cw, ch = container_lwh
    ul, uw, uh = unit_lwh
    empty = {"count": None, "per_layer": None, "layers": None, "fill": None, "placements": []}
    if min(cl, cw, ch, ul, uw, uh) <= 0:
        return {**empty, "error": "Zerowy lub ujemny wymiar"}
    try:
        pallet = PalletType(code="LEVEL", dims=Dimensions(l_cm=int(cl), w_cm=int(cw), h_cm=int(ch)),
                            max_weight_kg=1_000_000).validate()
        unit = CartonVariant(
            sku="UNIT", variant="LVL",
            dims=Dimensions(l_cm=int(ul), w_cm=int(uw), h_cm=int(uh)),
            unit_weight_kg=max(0.001, float(unit_weight_kg)),
            pieces_per_carton=1, demand_pieces=100000, allow_rotation=True,
        ).validate()
        res = PalletCalculator.calculate(unit, pallet)
        layouts = _eval_layouts(unit, pallet,
                                {"length_cm": cl, "width_cm": cw, "base_height_cm": 0}, res)
        best = max(layouts, key=lambda lay: lay["cartons_per_pallet"])
        count = best["cartons_per_pallet"]
        if not count:
            return {**empty, "error": "Nie mieści się"}
        fill = min(100, round(100 * count * ul * uw * uh / (cl * cw * ch)))
        return {"count": count, "per_layer": best["cartons_per_layer"],
                "layers": best["layers_used"], "fill": fill,
                "placements": best["placements"], "error": ""}
    except Exception as exc:
        return {**empty, "error": f"Błąd pakowania: {exc}"[:120]}


# ── Warianty kartonu — porównanie wypełnienia (Faza 3) ──────────────────────────

def _variant_fill(l, w, h, instr):
    """Wypełnienie palety (%) dla wymiarów kartonu l×w×h, licząc na konfiguracji palety
    z instrukcji bazowej (waga/szt/paleta). Reużywa silnik pakowania — zero nowej
    geometrii. Zwraca {fill, cartons_per_pallet, fits, error}."""
    if not instr:
        return {"fill": None, "cartons_per_pallet": None, "fits": False,
                "error": "Brak instrukcji bazowej materiału"}
    try:
        pallet, meta = _build_pallet(instr.pallet_code or "EU",
                                     instr.max_height_total_cm, instr.max_weight_kg)
        carton = CartonVariant(
            sku=instr.product.code, variant="ALT",
            dims=Dimensions(l_cm=int(l), w_cm=int(w), h_cm=int(h)),
            unit_weight_kg=max(0.001, float(instr.unit_weight or 0)),   # 0 → validate rzuca; nie blokuj na braku wagi
            pieces_per_carton=int(instr.pcs_per_carton or 1),
            demand_pieces=int(instr.demand_pcs or 1000),
            carton_tare_kg=float(instr.carton_tare or 0.0),
            allow_rotation=True,
        ).validate()
        res = PalletCalculator.calculate(carton, pallet)
        best = _eval_layouts(carton, pallet, meta, res)[0]
        # Ta sama metryka co dashboard (objętość vs dostępna wysokość) — spójne porównanie.
        usable_h = (instr.max_height_total_cm or 0) - (instr.pallet_base_height_cm or 0)
        fill = _vol_fill(best["cartons_per_pallet"], instr.pallet_length_cm,
                         instr.pallet_width_cm, usable_h, int(l), int(w), int(h))
        if fill is None:                       # brak snapshotu palety → fallback na cube
            fill = min(100, round(best["cube_used_pct"]))
        return {"fill": fill,
                "cartons_per_pallet": best["cartons_per_pallet"], "fits": True, "error": ""}
    except Exception as exc:
        return {"fill": None, "cartons_per_pallet": None, "fits": False,
                "error": f"Nie mieści się / błąd: {exc}"[:120]}


def _suggest_dims(instr, down_pct, up_pct, top_k=5):
    """Zaproponuj wymiary kartonu lepiej wypełniające paletę, o objętości w paśmie
    [V0·(1−down%), V0·(1+up%)]. Reużywa _variant_fill (realne pakowanie z rotacją) —
    zero nowej geometrii. Zwraca top-K dictów posortowanych malejąco po wypełnieniu,
    tylko lepszych od obecnego."""
    if not instr:
        return []
    base = _variant_fill(instr.carton_l, instr.carton_w, instr.carton_h, instr)
    base_fill = base["fill"]
    v0 = (instr.carton_l or 0) * (instr.carton_w or 0) * (instr.carton_h or 0)
    if base_fill is None or v0 <= 0:
        return []
    vmin, vmax = v0 * (1 - down_pct / 100), v0 * (1 + up_pct / 100)
    pl, pw = int(instr.pallet_length_cm or 120), int(instr.pallet_width_cm or 80)
    usable_h = int((instr.max_height_total_cm or 0) - (instr.pallet_base_height_cm or 0)) or 200

    # ponytail: O(kandydaci × silnik) — footprint co 10 cm, ≤3 wysokości/footprint, cap 80.
    # Jeśli za wolne → coarser siatka albo Celery.
    seen, cands = set(), []
    for l in range(10, pl + 1, 10):
        for w in range(10, pw + 1, 10):
            area = l * w
            h_vol = round(v0 / area)                       # wysokość dająca ~V0
            hs = {h_vol}
            for layers in (1, 2, 3, 4):                    # wysokości dzielące pełne warstwy
                hs.add(usable_h // layers)
            for h in sorted(hs, key=lambda x: abs(x - h_vol))[:3]:
                if not (5 <= h <= usable_h) or not (vmin <= area * h <= vmax):
                    continue
                if (l, w, h) not in seen:
                    seen.add((l, w, h))
                    cands.append((l, w, h))
    rows = []
    for (l, w, h) in cands[:80]:
        r = _variant_fill(l, w, h, instr)
        if r["fits"] and r["fill"] is not None and r["fill"] > base_fill:
            rows.append({"l": l, "w": w, "h": h, "fill": r["fill"],
                         "cartons_per_pallet": r["cartons_per_pallet"],
                         "delta_pp": r["fill"] - base_fill})
    rows.sort(key=lambda r: -r["fill"])
    return rows[:top_k]


__all__ = ['pack_into', '_variant_fill', '_suggest_dims']
