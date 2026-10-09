# MaxRects — heurystyka pojedynczej warstwy (wyniesiona z pallet_calculator).
from typing import List, Dict, Any



def _maxrects_layer(pallet_l: int, pallet_w: int,
                    carton_l: int, carton_w: int,
                    allow_rotation: bool = True,
                    heuristic: str = "bssf") -> List[Dict[str, Any]]:
    """
    Pure-Python MaxRects guillotine packer for a single pallet layer.
    Returns list of dicts with keys x, y, dx, dy, rotated.
    Consistently outperforms simple grid packing for non-divisible carton sizes.

    `heuristic` selects the free-rectangle choice rule so the same packer can yield
    several visually distinct arrangements:
      bssf — best short-side fit (default)   blsf — best long-side fit
      baf  — best area fit                    bl   — bottom-left (y then x)
    """
    # Degenerate input: a zero/negative carton footprint would place zero-area rects
    # forever (it never consumes free space) — bail out instead of looping.
    if carton_l <= 0 or carton_w <= 0 or pallet_l <= 0 or pallet_w <= 0:
        return []

    placements: List[Dict] = []
    free: List[Dict] = [{"x": 0, "y": 0, "w": pallet_l, "h": pallet_w}]

    def _try(rect: Dict, cw: int, ch: int, rot: bool):
        if rect["w"] >= cw and rect["h"] >= ch:
            return {"x": rect["x"], "y": rect["y"], "w": cw, "h": ch, "rot": rot}
        return None

    def _score(rect: Dict, cw: int, ch: int):
        lw, lh = rect["w"] - cw, rect["h"] - ch
        if heuristic == "blsf":
            return (max(lw, lh), min(lw, lh))
        if heuristic == "baf":
            return (rect["w"] * rect["h"] - cw * ch, min(lw, lh))
        if heuristic == "bl":
            return (rect["y"], rect["x"])
        return (min(lw, lh), max(lw, lh))   # bssf

    def _split(free_list: List[Dict], p: Dict) -> List[Dict]:
        result = []
        px, py, pw, ph = p["x"], p["y"], p["w"], p["h"]
        for r in free_list:
            rx, ry, rw, rh = r["x"], r["y"], r["w"], r["h"]
            # No overlap
            if px >= rx + rw or px + pw <= rx or py >= ry + rh or py + ph <= ry:
                result.append(r)
                continue
            # Guillotine split — generate up to 4 sub-rects
            if px > rx:
                result.append({"x": rx, "y": ry, "w": px - rx, "h": rh})
            if px + pw < rx + rw:
                result.append({"x": px + pw, "y": ry, "w": rx + rw - px - pw, "h": rh})
            if py > ry:
                result.append({"x": rx, "y": ry, "w": rw, "h": py - ry})
            if py + ph < ry + rh:
                result.append({"x": rx, "y": py + ph, "w": rw, "h": ry + rh - py - ph})
        # Dedup PRZED prune — dwa geometrycznie identyczne wolne prostokąty (możliwe po
        # niezależnych splitach nakładających się frees) „zawierały się nawzajem" i prune
        # kasował OBA, gubiąc wolną przestrzeń (mniej kartonów w warstwie niż możliwe).
        seen = set()
        uniq = []
        for r in result:
            key = (r["x"], r["y"], r["w"], r["h"])
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        # Prune rects contained in a larger one
        pruned = []
        for a in uniq:
            if not any(
                b["x"] <= a["x"] and b["y"] <= a["y"] and
                b["x"] + b["w"] >= a["x"] + a["w"] and
                b["y"] + b["h"] >= a["y"] + a["h"]
                for b in uniq if b is not a
            ):
                pruned.append(a)
        return pruned

    orientations = [(carton_l, carton_w, False)]
    if allow_rotation and carton_l != carton_w:
        orientations.append((carton_w, carton_l, True))

    while free:
        best = None
        best_score = (float("inf"), float("inf"))
        for cw, ch, rot in orientations:
            for r in free:
                p = _try(r, cw, ch, rot)
                if p:
                    score = _score(r, cw, ch)
                    if score < best_score:
                        best_score, best = score, p
        if best is None:
            break
        placements.append({
            "x": best["x"], "y": best["y"],
            "dx": best["w"], "dy": best["h"],
            "rotated": best["rot"]
        })
        free = _split(free, best)

    return placements
