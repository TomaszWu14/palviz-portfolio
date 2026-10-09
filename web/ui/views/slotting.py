# Slotting & symulacja (digital twin): ABC/XYZ analysis of product demand + a what-if
# simulation that quantifies the picking-travel saved by demand-driven slotting.
from .core import (_md_or_tr, render)

from django.db.models import Sum, Count

from ..models import (ShipmentLine, WarehouseLayout, WarehouseLayoutCell,
                      PalletizationInstruction)
from ..slotting import abc_classify, simulate_slotting, slot_distance


def _demand_by_product():
    """Carton-equivalent demand + line frequency per product (demand profile for ABC).
    Linie mają różne jednostki (szt/kar/pal) — sumowanie surowych `quantity` zniekształca
    ranking (2 pal + 100 szt ≠ 102). Konwertujemy do wspólnej jednostki (kartonów) tą samą
    logiką co pakowanie shipmentu: szt→qty/ppc, pal→qty·cpp, kar→qty."""
    instr_map = {}
    for instr in PalletizationInstruction.objects.filter(is_active=True):
        layout = instr.get_selected_layout() or {}
        instr_map[instr.product_id] = (instr.pcs_per_carton or 1,
                                       (layout.get("cartons_per_pallet") or 1))
    rows = (ShipmentLine.objects
            .values("product_id", "product__code", "product__name", "unit")
            .annotate(qty=Sum("quantity"), freq=Count("id")))
    demands, meta = {}, {}
    for r in rows:
        code = r["product__code"]
        ppc, cpp = instr_map.get(r["product_id"], (1, 1))
        qty = r["qty"] or 0.0
        if r["unit"] == "szt":
            eq = qty / ppc
        elif r["unit"] == "pal":
            eq = qty * cpp
        else:  # kar
            eq = qty
        demands[code] = demands.get(code, 0.0) + eq
        m = meta.setdefault(code, {"name": r["product__name"], "freq": 0})
        m["freq"] += r["freq"]
    return demands, meta


def _slot_distances():
    """Distances of active-layout picking faces from the dock origin (nearest corner)."""
    layout = WarehouseLayout.objects.filter(is_active=True).first()
    if not layout:
        return []
    cells = list(WarehouseLayoutCell.objects.filter(layout=layout)
                 .values("grid_row", "grid_col"))
    if not cells:
        return []
    r0 = min(c["grid_row"] for c in cells)
    c0 = min(c["grid_col"] for c in cells)
    # One picking face per (row,col) column stack — dedupe so distances aren't level-inflated.
    faces = {(c["grid_row"], c["grid_col"]) for c in cells}
    return [slot_distance(r, c, (r0, c0)) for (r, c) in faces]


@_md_or_tr
def slotting_analysis(request):
    """ABC classification (Pareto) + what-if slotting travel simulation."""
    demands, meta = _demand_by_product()
    abc = abc_classify(demands)
    for row in abc:
        row.update(meta.get(row["key"], {"name": "", "freq": 0}))

    class_summary = {"A": {"n": 0, "demand": 0.0}, "B": {"n": 0, "demand": 0.0},
                     "C": {"n": 0, "demand": 0.0}}
    for row in abc:
        cs = class_summary[row["klass"]]
        cs["n"] += 1
        cs["demand"] += row["demand"]

    distances = _slot_distances()
    sim = simulate_slotting(demands, distances)

    # Pareto chart data (top 40 for readability): demand bars + cumulative % line.
    top = abc[:40]
    pareto = {
        "codes": [r["key"] for r in top],
        "demand": [r["demand"] for r in top],
        "cum": [round(r["cum_share"] * 100, 1) for r in top],
    }
    return render(request, "ui/slotting/analysis.html", {
        "abc": abc[:500],
        "class_summary": class_summary,
        "total_products": len(abc),
        "sim": sim,
        "slots": len(distances),
        "pareto": pareto,
        "has_layout": bool(distances),
    })

__all__ = [
    '_demand_by_product',
    '_slot_distances',
    'slotting_analysis',
]
