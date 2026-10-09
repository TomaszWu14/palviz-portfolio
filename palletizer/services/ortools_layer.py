"""Optimal single-layer carton packing via OR-Tools CP-SAT.

This is an *optional* engine: the heavy `ortools` dependency is imported lazily, and every
entry point returns ``None`` when it is not installed, so the framework-free packing core
keeps working without it. Where the heuristics in ``pallet_calculator`` only *approximate*
the best cartons-per-layer, this proves the true optimum (axis-aligned, 90°-rotation
allowed) for a single uniform-SKU layer, within a time budget.

Units follow the rest of the package: integer centimetres/millimetres.
"""
from typing import List, Optional, Tuple

# A placement is (x, y, w, h) of one carton footprint on the pallet, same convention as
# pallet_calculator.Placement2D (origin bottom-left).
Placement = Tuple[int, int, int, int]


def optimal_uniform_layer(
    pallet_l: int,
    pallet_w: int,
    dx: int,
    dy: int,
    allow_rotation: bool = True,
    time_limit_s: float = 5.0,
) -> Optional[List[Placement]]:
    """Return a provably (within the time budget) maximum set of non-overlapping carton
    footprints on one pallet layer, or ``None`` if OR-Tools is unavailable or no carton fits.

    Each carton occupies ``dx × dy`` (or ``dy × dx`` when rotation is allowed). The result is
    a list of ``(x, y, w, h)`` placements; ``len(result)`` is the optimal cartons-per-layer.
    """
    try:
        from ortools.sat.python import cp_model
    except Exception:
        return None

    if dx <= 0 or dy <= 0 or pallet_l <= 0 or pallet_w <= 0:
        return None
    if min(dx, dy) > max(pallet_l, pallet_w) or (dx > pallet_l and dy > pallet_l) \
            or (dx > pallet_w and dy > pallet_w):
        # Even a single carton (in either orientation) cannot fit.
        if not (_fits(dx, dy, pallet_l, pallet_w) or
                (allow_rotation and _fits(dy, dx, pallet_l, pallet_w))):
            return None

    # Upper bound on count: pallet area / carton area (can't beat this).
    area_ub = (pallet_l * pallet_w) // (dx * dy)
    if area_ub <= 0:
        return None
    n = area_ub

    model = cp_model.CpModel()
    used, x_iv, y_iv, xs, ys, ws, hs, rot = [], [], [], [], [], [], [], []
    for i in range(n):
        u = model.NewBoolVar(f"u{i}")
        x = model.NewIntVar(0, pallet_l, f"x{i}")
        y = model.NewIntVar(0, pallet_w, f"y{i}")
        w = model.NewIntVar(min(dx, dy), max(dx, dy), f"w{i}")
        h = model.NewIntVar(min(dx, dy), max(dx, dy), f"h{i}")
        r = model.NewBoolVar(f"r{i}")
        if allow_rotation and dx != dy:
            model.Add(w == dx).OnlyEnforceIf(r.Not())
            model.Add(h == dy).OnlyEnforceIf(r.Not())
            model.Add(w == dy).OnlyEnforceIf(r)
            model.Add(h == dx).OnlyEnforceIf(r)
        else:
            model.Add(w == dx)
            model.Add(h == dy)
        xe = model.NewIntVar(0, pallet_l, f"xe{i}")
        ye = model.NewIntVar(0, pallet_w, f"ye{i}")
        # Optional intervals: only constrain/occupy space when the carton is used.
        ix = model.NewOptionalIntervalVar(x, w, xe, u, f"ix{i}")
        iy = model.NewOptionalIntervalVar(y, h, ye, u, f"iy{i}")
        used.append(u); x_iv.append(ix); y_iv.append(iy)
        xs.append(x); ys.append(y); ws.append(w); hs.append(h); rot.append(r)

    model.AddNoOverlap2D(x_iv, y_iv)
    # Symmetry breaking: keep used cartons "before" unused ones to shrink the search.
    for i in range(n - 1):
        model.AddImplication(used[i].Not(), used[i + 1].Not())
    model.Maximize(sum(used))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    out: List[Placement] = []
    for i in range(n):
        if solver.Value(used[i]):
            out.append((solver.Value(xs[i]), solver.Value(ys[i]),
                        solver.Value(ws[i]), solver.Value(hs[i])))
    return _compact_layer(out) or None


def _fits(w: int, h: int, L: int, W: int) -> bool:
    return w <= L and h <= W


def _compact_layer(rects: List[Placement], passes: int = 4) -> List[Placement]:
    """Docisnij warstwe w rog (y=0, potem x=0) — CP-SAT maksymalizuje LICZBE kartonow,
    ale pozostawia je czasem 'plywajace' w wolnej przestrzeni. Przesuwanie kartonu w luke
    nie moze stworzyc kolizji ani zmienic liczby, a daje ciasny, stabilny uklad.
    Czysty Python (bez solvera, bez kosztu czasu) — kilka przebiegow do stabilizacji."""
    rs = [list(r) for r in rects]
    for _ in range(passes):
        moved = False
        # Zjazd w dol: najnizszy dopuszczalny y = najwyzsza krawedz kartonu ponizej,
        # ktory nachodzi na zakres X (albo 0). Sortuj od dolu, by dolne blokowaly gorne.
        for r in sorted(rs, key=lambda r: (r[1], r[0])):
            floor = 0
            for o in rs:
                if o is r or not (o[0] < r[0] + r[2] and r[0] < o[0] + o[2]):
                    continue
                top = o[1] + o[3]
                if top <= r[1] and top > floor:
                    floor = top
            if floor < r[1]:
                r[1] = floor; moved = True
        # Zjazd w lewo: analogicznie po osi X.
        for r in sorted(rs, key=lambda r: (r[0], r[1])):
            wall = 0
            for o in rs:
                if o is r or not (o[1] < r[1] + r[3] and r[1] < o[1] + o[3]):
                    continue
                right = o[0] + o[2]
                if right <= r[0] and right > wall:
                    wall = right
            if wall < r[0]:
                r[0] = wall; moved = True
        if not moved:
            break
    return [tuple(r) for r in rs]
