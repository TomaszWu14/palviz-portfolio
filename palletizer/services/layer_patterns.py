# Wzorce ukladania warstwy (grid/stripes/brick/block/pinwheel/column) —
# wyniesione z PalletCalculator; klasa trzyma aliasy staticmethod.
from typing import List, Tuple

from palletizer.domain import PalletType
from .layer_geometry import Placement2D


def _area_utilization(pallet: PalletType, cartons_per_layer: int, dx: int, dy: int) -> float:
    used = cartons_per_layer * dx * dy
    total = pallet.length_cm * pallet.width_cm
    if total <= 0:
        return 0.0
    return round((used / total) * 100.0, 2)

def _uniform_grid(pallet: PalletType, dx: int, dy: int, rotated: bool) -> Tuple[int, Tuple[Placement2D, ...]]:
    cols = pallet.length_cm // dx
    rows = pallet.width_cm // dy
    count = int(cols * rows)

    placements: List[Placement2D] = []
    for cx in range(int(cols)):
        for cy in range(int(rows)):
            placements.append(Placement2D(
                x=int(cx * dx),
                y=int(cy * dy),
                dx=int(dx),
                dy=int(dy),
                rotated=rotated
            ))
    return count, tuple(placements)

def _mixed_stripe_length(pallet: PalletType, base_dx: int, base_dy: int, rot_dx: int, rot_dy: int) -> Tuple[int, Tuple[Placement2D, ...]]:
    # baza: uniform w orientacji base_dx/base_dy
    base_cols = pallet.length_cm // base_dx
    base_rows = pallet.width_cm // base_dy
    base_count = int(base_cols * base_rows)

    used_len = int(base_cols * base_dx)
    stripe_len = int(pallet.length_cm - used_len)
    placements: List[Placement2D] = []

    # placementy bazy
    for cx in range(int(base_cols)):
        for cy in range(int(base_rows)):
            placements.append(Placement2D(x=int(cx * base_dx), y=int(cy * base_dy), dx=int(base_dx), dy=int(base_dy), rotated=False))

    # stripe po długości (na końcu X): próbuj wypełnić obróconym kartonem
    add = 0
    if stripe_len > 0:
        stripe_cols = stripe_len // rot_dx
        stripe_rows = pallet.width_cm // rot_dy
        add = int(stripe_cols * stripe_rows)

        x0 = used_len
        for cx in range(int(stripe_cols)):
            for cy in range(int(stripe_rows)):
                placements.append(Placement2D(
                    x=int(x0 + cx * rot_dx),
                    y=int(cy * rot_dy),
                    dx=int(rot_dx),
                    dy=int(rot_dy),
                    rotated=True
                ))

    return base_count + add, tuple(placements)

def _mixed_stripe_width(pallet: PalletType, base_dx: int, base_dy: int, rot_dx: int, rot_dy: int) -> Tuple[int, Tuple[Placement2D, ...]]:
    base_cols = pallet.length_cm // base_dx
    base_rows = pallet.width_cm // base_dy
    base_count = int(base_cols * base_rows)

    used_wid = int(base_rows * base_dy)
    stripe_wid = int(pallet.width_cm - used_wid)
    placements: List[Placement2D] = []

    # placementy bazy
    for cx in range(int(base_cols)):
        for cy in range(int(base_rows)):
            placements.append(Placement2D(x=int(cx * base_dx), y=int(cy * base_dy), dx=int(base_dx), dy=int(base_dy), rotated=False))

    # stripe po szerokości (na końcu Y): obrócone
    add = 0
    if stripe_wid > 0:
        stripe_cols = pallet.length_cm // rot_dx
        stripe_rows = stripe_wid // rot_dy
        add = int(stripe_cols * stripe_rows)

        y0 = used_wid
        for cx in range(int(stripe_cols)):
            for cy in range(int(stripe_rows)):
                placements.append(Placement2D(
                    x=int(cx * rot_dx),
                    y=int(y0 + cy * rot_dy),
                    dx=int(rot_dx),
                    dy=int(rot_dy),
                    rotated=True
                ))

    return base_count + add, tuple(placements)

def _column_packed(pallet: PalletType, l: int, w: int, allow_rotation: bool, transpose: bool = False) -> Tuple[int, Tuple[Placement2D, ...]]:
    """Walk columns left→right; for each column pick the orientation that fits
    the most rows. Often beats a uniform grid for non-divisible carton sizes.
    transpose=True walks the other axis (rows bottom→top)."""
    PL = pallet.width_cm if transpose else pallet.length_cm
    PW = pallet.length_cm if transpose else pallet.width_cm
    placements: List[Placement2D] = []
    x = 0
    count = 0
    guard = 0
    while x < PL and guard < 1000:
        guard += 1
        cands = []
        # orientation A: column width=l, rows of height w
        if x + l <= PL and w > 0:
            cands.append((PW // w, l, w, False))
        # orientation B (rotated): column width=w, rows of height l
        if allow_rotation and x + w <= PL and l > 0:
            cands.append((PW // l, w, l, True))
        cands = [c for c in cands if c[0] > 0]
        if not cands:
            break
        rows, cw, ch, rot = max(cands, key=lambda t: t[0])
        for r in range(int(rows)):
            px, py = (r * ch, x) if transpose else (x, r * ch)
            dx, dy = (ch, cw) if transpose else (cw, ch)
            placements.append(Placement2D(x=int(px), y=int(py), dx=int(dx), dy=int(dy), rotated=(rot ^ transpose)))
        count += int(rows)
        x += cw
    return count, tuple(placements)

def _brick_pattern(pallet: PalletType, dx: int, dy: int, rotated: bool) -> Tuple[int, Tuple[Placement2D, ...]]:
    """Uniform grid with every other row offset by half a carton ("na zakładkę")
    — interlocks the layers for stability. Usually a few cartons fewer than a
    flush grid, but a distinct, more stable arrangement to choose from."""
    PL, PW = pallet.length_cm, pallet.width_cm
    rows = PW // dy if dy else 0
    placements: List[Placement2D] = []
    count = 0
    for r in range(int(rows)):
        offset = (dx // 2) if (r % 2) else 0
        x = offset
        while x + dx <= PL:
            placements.append(Placement2D(x=int(x), y=int(r * dy), dx=int(dx), dy=int(dy), rotated=rotated))
            count += 1
            x += dx
    return count, tuple(placements)

def _fill_region(placements: List[Placement2D], x0: int, y0: int, x1: int, y1: int,
                 dx: int, dy: int, rot: bool) -> int:
    """Tile an axis-aligned region [x0,x1)×[y0,y1) with a uniform grid; returns count."""
    if dx <= 0 or dy <= 0:
        return 0
    cols = (x1 - x0) // dx
    rows = (y1 - y0) // dy
    n = 0
    for cx in range(int(cols)):
        for cy in range(int(rows)):
            placements.append(Placement2D(x=int(x0 + cx * dx), y=int(y0 + cy * dy),
                                          dx=int(dx), dy=int(dy), rotated=rot))
            n += 1
    return n

def _block_split(pallet: PalletType, l: int, w: int, axis: str = "x", frac: float = 0.5) -> Tuple[int, Tuple[Placement2D, ...]]:
    """Two disjoint blocks, each a uniform grid in a different orientation — base in
    one part, rotated in the other (split at `frac` of the pallet). Non-overlapping;
    a distinct, tidy "two-block" pattern interlocking the two orientations."""
    PL, PW = pallet.length_cm, pallet.width_cm
    placements: List[Placement2D] = []
    count = 0
    if axis == "x":
        split = int(PL * frac)
        count += _fill_region(placements, 0, 0, split, PW, w, l, True)
        count += _fill_region(placements, split, 0, PL, PW, l, w, False)
    else:
        split = int(PW * frac)
        count += _fill_region(placements, 0, 0, PL, split, l, w, False)
        count += _fill_region(placements, 0, split, PL, PW, w, l, True)
    return count, tuple(placements)

def _pinwheel(pallet: PalletType, l: int, w: int) -> Tuple[int, Tuple[Placement2D, ...]]:
    """Four quadrants with alternating orientation (windmill) — a classic, very
    stable interlocked pallet pattern, visually distinct from grids and stripes."""
    PL, PW = pallet.length_cm, pallet.width_cm
    mx, my = PL // 2, PW // 2
    placements: List[Placement2D] = []
    count = 0
    # bottom-left base, bottom-right rotated, top-left rotated, top-right base
    count += _fill_region(placements, 0, 0, mx, my, l, w, False)
    count += _fill_region(placements, mx, 0, PL, my, w, l, True)
    count += _fill_region(placements, 0, my, mx, PW, w, l, True)
    count += _fill_region(placements, mx, my, PL, PW, l, w, False)
    return count, tuple(placements)

