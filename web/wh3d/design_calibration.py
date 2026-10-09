"""Kalibracja symulacji na obecnej hali (plan 2026-10-02, etap 6) — czysty Python.

Dla każdego zasobu (wózka / pickera) z zadań EWM bierzemy kolejne potwierdzenia: odstęp między
nimi to realny czas cyklu (dojazd z poprzedniego miejsca + pobranie + przewóz + odłożenie).
Ten sam cykl liczymy fizyką symulacji (droga prostokątna / prędkość z katalogu, podnoszenie,
chwyt) na modelu obecnej hali. Mediana „real ÷ symulacja” = współczynnik korekty, którym
symulacja dnia mnoży czasy obsługi na nowej hali.

Odstęp dłuższy niż GAP_MAX_S to przerwa, a nie cykl — odpada. Krótsze przerwy (czekanie na
pracę) zostają, więc współczynnik jest raczej górnym oszacowaniem (ostrożny dla wymiarowania).
"""
import statistics
from collections import defaultdict

from .blender_agents import LIFT_SPEED, SPEED
from .blender_route import rack_point
from .blender_scene import FORKLIFT_APPROACH, PERSON_APPROACH, PICK_DWELL, _feature_center, _slot
from .blender_tasks import resolve_moves

GAP_MAX_S = 15 * 60
HANDLE_S = 1.5
MIN_PAIRS = 20                  # mniej par → współczynnik niewiarygodny (pokazujemy, ale ostrzegamy)
GROUPS = (("trucks", "Wózki (przyjęcia, wydania, uzupełnienia, przesunięcia)"),
          ("picking", "Kompletacja (pickerzy)"))


def _manh(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _point(ep, features, near, approach):
    """Koniec ruchu z `resolve_moves` → (punkt na posadzce, wysokość gniazda)."""
    if ep[0] == "rack":
        _, rack, bay_idx, level = ep
        along, z = _slot(rack, bay_idx, level)
        return rack_point(rack, along, -approach), z
    kinds = ("dock", "gate") if ep[0] == "dock" else ("station", "leader", "returns")
    pts = [_feature_center(f) for f in features if f["kind"] in kinds]
    return (min(pts, key=lambda p: _manh(p, near)) if pts else near), 0.0


def ideal_cycle(prev, src, dst, *, picking):
    """Czas cyklu wg fizyki symulacji: dojazd prev → src, chwyt, przewóz src → dst, odłożenie."""
    (sp, sz), (dp, dz) = src, dst
    if picking:
        v = SPEED["person"]
        return (_manh(prev, sp) + _manh(sp, dp)) / v + PICK_DWELL
    v = SPEED["forklift"]
    return (_manh(prev, sp) + _manh(sp, dp)) / v + 2 * (sz + dz) / LIFT_SPEED + 2 * HANDLE_S


def calibrate(rows, locator, features):
    """rows: krotki `blender_tasks.ROW_FIELDS` jednego dnia (rosnąco po potwierdzeniu)."""
    if not rows:
        return None
    moves, skipped = resolve_moves(rows, locator, rows[0][0])
    by_agent = defaultdict(list)
    for t, agent, kind, src, dst, _sku in moves:
        by_agent[agent].append((t, kind, src, dst))
    pairs = {g: [] for g, _ in GROUPS}
    per_kind = defaultdict(lambda: {"real": [], "sim": []})
    hours = defaultdict(set)
    for agent, seq in by_agent.items():
        prev_end = None
        for i, (t, kind, src, dst) in enumerate(seq):
            picking = kind == "picking"
            approach = PERSON_APPROACH if picking else FORKLIFT_APPROACH
            near = prev_end[0] if prev_end else (0.0, 0.0)
            s = _point(src, features, near, approach)
            d = _point(dst, features, s[0], approach)
            hours[int(t // 3600)].add(agent)
            if i and prev_end is not None:
                gap = t - seq[i - 1][0]
                if 0 < gap <= GAP_MAX_S:
                    sim = ideal_cycle(prev_end[0], s, d, picking=picking)
                    pairs["picking" if picking else "trucks"].append((gap, sim))
                    per_kind[kind]["real"].append(gap)
                    per_kind[kind]["sim"].append(sim)
            prev_end = d
    groups = []
    for g, label in GROUPS:
        ps = pairs[g]
        groups.append({"key": g, "label": label, "pairs": len(ps), "reliable": len(ps) >= MIN_PAIRS,
                       "real_s": round(statistics.median(p[0] for p in ps), 1) if ps else None,
                       "sim_s": round(statistics.median(p[1] for p in ps), 1) if ps else None,
                       "k": round(statistics.median(p[0] / max(1.0, p[1]) for p in ps), 2) if ps else None})
    kinds = [{"kind": k, "pairs": len(v["real"]), "real_s": round(statistics.median(v["real"]), 1),
              "sim_s": round(statistics.median(v["sim"]), 1)} for k, v in sorted(per_kind.items())]
    active = [len(a) for a in hours.values()]
    return {"tasks": len(rows), "mapped": len(moves), "skipped": skipped, "resources": len(by_agent),
            "active_per_hour": round(statistics.mean(active), 1) if active else 0,
            "groups": groups, "kinds": kinds}
