"""Slotting analytics — pure, framework-free so it's unit-testable without the DB.

Two building blocks behind the "Slotting & symulacja" module:
  * abc_classify  — Pareto/ABC classes from a demand profile.
  * simulate_slotting — quantifies the travel saved by demand-driven slotting vs the
    current (unsorted) placement, using the rearrangement inequality: matching the
    highest-demand SKUs to the nearest slots is provably the minimum-travel assignment.
"""
from typing import Dict, List, Tuple


def abc_classify(demands: Dict[str, float],
                 a_cut: float = 0.80, b_cut: float = 0.95) -> List[dict]:
    """Classify keys by cumulative demand share. A = top `a_cut` of demand, B = up to
    `b_cut`, C = the rest. Returns rows sorted by demand desc with class + cumulative %."""
    items = sorted(((k, float(v)) for k, v in demands.items() if v and v > 0),
                   key=lambda kv: kv[1], reverse=True)
    total = sum(v for _, v in items)
    rows, cum = [], 0.0
    for k, v in items:
        prev_share = cum / total if total else 0.0   # share BEFORE this item
        cum += v
        cum_share = cum / total if total else 0.0
        share = v / total if total else 0.0
        # The item that crosses a threshold still belongs to the lower class, so a single
        # dominant SKU (whose own share already exceeds a_cut) is correctly class A.
        klass = "A" if prev_share < a_cut else ("B" if prev_share < b_cut else "C")
        rows.append({"key": k, "demand": round(v, 3), "share": round(share, 4),
                     "cum_share": round(cum_share, 4), "klass": klass})
    return rows


def simulate_slotting(demands: Dict[str, float],
                      slot_distances: List[float]) -> dict:
    """Potencjał re-slottingu: podróż w NAJLEPSZYM vs NAJGORSZYM ułożeniu popytu.

    Nie mamy realnej mapy SKU→slot, więc NIE liczymy „obecnej" podróży (byłaby zgadywana
    z przypadkowej kolejności slotów). Zamiast tego — deterministyczne granice z nierówności
    rearanżacji: najwięcej popytu przy najbliższych slotach = minimum (`optimized`), przy
    najdalszych = maksimum (`worst`). `savings` = worst − optimized = ILE PODRÓŻY MOŻNA
    ZAOSZCZĘDZIĆ optymalnym ułożeniem względem najgorszego (górna granica potencjału).
    `baseline` = worst (punkt odniesienia). Bez zależności od kolejności `slot_distances`."""
    dem = sorted((float(v) for v in demands.values() if v and v > 0), reverse=True)
    if not dem or not slot_distances:
        return {"baseline": 0.0, "optimized": 0.0, "worst": 0.0,
                "savings": 0.0, "savings_pct": 0.0, "n_matched": 0}
    n = min(len(dem), len(slot_distances))
    dem = dem[:n]
    asc = sorted(float(d) for d in slot_distances)[:n]                    # nearest first → optimum
    desc = sorted((float(d) for d in slot_distances), reverse=True)[:n]   # farthest first → worst
    optimized = sum(d * s for d, s in zip(dem, asc))
    worst = sum(d * s for d, s in zip(dem, desc))
    savings = worst - optimized                                          # max osiągalny potencjał
    return {
        "baseline": round(worst, 2),           # punkt odniesienia = najgorsze ułożenie
        "optimized": round(optimized, 2),
        "worst": round(worst, 2),
        "savings": round(savings, 2),
        "savings_pct": round(100 * savings / worst, 1) if worst else 0.0,
        "n_matched": n,
    }


def slot_distance(grid_row: int, grid_col: int, origin: Tuple[int, int] = (0, 0)) -> int:
    """Manhattan distance of a layout cell from the dock origin (grid units)."""
    return abs(grid_row - origin[0]) + abs(grid_col - origin[1])
