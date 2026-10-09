"""Symulacja dnia projektowego na wariancie hali (plan 2026-10-02, etap 3a) — czysty Python.

Zadania EWM z jednego dnia (rodzaj, materiał, dokument, chwila potwierdzenia) × mnożnik
wzrostu → dyspozytor przydziela je flocie (AGV, kombi, pickerzy EPT): zadanie bierze agent,
który najwcześniej może dojechać na start. Towar rozmieszczony wg rotacji (ABC): najczęściej
kompletowane materiały najbliżej pakowania, palety A w niskich poziomach przy czole rzędu.

Droga = odległość prostokątna (alejki pod kątem prostym) / prędkość z katalogu — tysiące zadań
liczą się w ułamku sekundy. Pełne trasy A* są dopiero w animacji wybranej godziny (etap 3b).
ponytail: zachłanny dyspozytor bez rezerwacji korytarzy (dwa kombi w jednym korytarzu się
nie blokują) — SimPy/blokady, jeśli kalibracja na obecnej hali (etap 6) pokaże rozjazd.
"""
import math
import random
from collections import Counter, defaultdict

from .blender_agents import LIFT_SPEED, SPEED
from .blender_containers import CONTAINER_DOCK_TAG, PALLETIZE_TAG, WRAPPER_TAG
from .blender_route import rack_point
from .blender_scene import (
    FORKLIFT_APPROACH, HANDOVER_M, PICK_DWELL, WRAP_S, _feature_center, _is_shelf, _slot, _vna_racks,
)

DAY_START_H, DAY_END_H = 5, 21          # zmiany 5–13 i 13–21
HORIZON_S = (DAY_END_H - DAY_START_H) * 3600
TARGET_UTIL = 0.85                      # sugerowana flota: tyle pracy na agenta w ciągu doby
HANDLE_S = 1.5                          # pobranie / odłożenie palety
TOUR_MAX_LINES = 20                     # pickerzy: max linii na jeden objazd
ABC_ZONES = ((0.2, 0.2), (0.5, 0.5))    # (udział materiałów, udział miejsc): A 20 % → najtańsze 20 % miejsc, B do 50 %
FLEET_KINDS = (("agv", "AGV (transport dok ↔ VNA)"), ("kombi", "Wózki kombi (VNA)"),
               ("ept", "Pickerzy EPT (półki K1)"))


def _manh(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


class _Agent:
    def __init__(self, kind, pos, no, k=1.0):
        self.kind, self.pos, self.free, self.busy, self.dist, self.n = kind, pos, 0.0, 0.0, 0.0, 0
        self.k = k                      # współczynnik kalibracji czasów (etap 6), 1 = katalog
        self.id = f"{kind}-{no}"

    def go(self, t, target):
        d = _manh(self.pos, target)
        self.pos, self.dist = target, self.dist + d
        return t + d / SPEED[self.kind] * self.k


def _pick(fleet, release, start):
    """Agent, który najwcześniej stanie w `start` (nie wcześniej niż `release`)."""
    return min(fleet, key=lambda a: max(a.free, release) + _manh(a.pos, start) / SPEED[a.kind])


def _work(agent, t0, t1, idle=0.0):
    """Praca od wyjazdu `t0` do końca `t1`; `idle` = postój w oczekiwaniu na ładunek (nie praca)."""
    agent.busy += t1 - t0 - max(0.0, idle)
    agent.free = t1
    agent.n += 1


class Layout:
    """Punkty obsługi i miejsca wariantu wyliczone z regałów i elementów hali."""

    def __init__(self, racks, features):
        def centers(kind, tag=None):
            return [_feature_center(f) for f in features if f["kind"] == kind
                    and (tag is None or tag in (f.get("label") or "").lower())]
        self.vna = _vna_racks(racks)
        self.shelves = [r for r in racks if _is_shelf(r)]
        docks = centers("dock") + centers("gate")
        self.in_points = centers("station", PALLETIZE_TAG) or centers("dock", CONTAINER_DOCK_TAG) or docks
        out = [d for d in docks if d not in centers("dock", CONTAINER_DOCK_TAG)]
        self.out_docks = out or docks
        self.wrappers = centers("station", WRAPPER_TAG)
        packs = [c for c in centers("station") if c not in self.in_points and c not in self.wrappers]
        self.pack = packs or self.out_docks
        # Półki K1: gniazda posortowane od najbliższego pakowania (A = najbliżej).
        self.shelf_slots = sorted(
            ((r, b) for r in self.shelves for b in range(r["n_bays"])),
            key=lambda rb: min(_manh(self._front(*rb, 0.55), p) for p in self.pack))
        # VNA: miejsca (regał, gniazdo, poziom) od najtańszego: jazda od czoła rzędu + podnoszenie.
        self.vna_slots = sorted(
            ((r, b, lv) for r in self.vna for b in range(r["n_bays"]) for lv in range(1, r["n_levels"] + 1)),
            key=lambda s: min(_slot(s[0], s[1], 1)[0], s[0]["width"] - _slot(s[0], s[1], 1)[0]) / SPEED["kombi"]
            + _slot(*s)[1] / LIFT_SPEED)

    def _front(self, rack, bay, approach):
        along, _ = _slot(rack, bay, 1)
        return rack_point(rack, along, -approach)

    def shelf(self, rank):
        r, b = self.shelf_slots[rank % len(self.shelf_slots)]
        return self._front(r, b, 0.55)

    def vna_slot(self, share, rng, toward):
        """Miejsce palety dla materiału z czołówki `share` (0 = najczęstszy … 1) wg stref ABC:
        losowe w swojej strefie miejsc. Zwraca (dojazd w korytarzu, wysokość, punkt przekazania
        przed tym czołem rzędu, które jest bliżej `toward`, środek gniazda)."""
        lo, hi = 0.0, 1.0
        prev = (0.0, 0.0)
        for mat_cut, slot_cut in ABC_ZONES:
            if share < mat_cut:
                lo, hi = prev[1], slot_cut
                break
            prev = (mat_cut, slot_cut)
        else:
            lo = prev[1]
        n = len(self.vna_slots)
        r, b, level = self.vna_slots[min(n - 1, int(n * (lo + rng.random() * (hi - lo))))]
        _, z = _slot(r, b, level)
        ends = (rack_point(r, -HANDOVER_M, -FORKLIFT_APPROACH),
                rack_point(r, r["width"] + HANDOVER_M, -FORKLIFT_APPROACH))
        hand = min(ends, key=lambda p: _manh(p, toward))
        along, _ = _slot(r, b, level)
        return self._front(r, b, FORKLIFT_APPROACH), z, hand, rack_point(r, along, r["depth"] / 2)

    def ok(self):
        return bool(self.vna and self.shelves and self.in_points and self.out_docks)


def scale_tasks(tasks, multiplier, seed=0):
    """Mnożnik wzrostu: > 1 dokłada losowe kopie zadań (czas ±15 min), < 1 losowo ujmuje."""
    rng = random.Random(seed)
    whole, frac = int(multiplier), multiplier - int(multiplier)
    out = []
    for t in tasks:
        copies = whole + (1 if rng.random() < frac else 0)
        for c in range(copies):
            jitter = rng.uniform(-900, 900) if c else 0.0
            out.append((max(0.0, t[0] + jitter),) + tuple(t[1:]))
    return sorted(out, key=lambda t: t[0])


def _tours(picks):
    """Linie kompletacji → objazdy: per dokument (bez dokumentu — pojedynczo), max 20 linii."""
    by_doc = defaultdict(list)
    for n, (release, _kind, material, doc) in enumerate(picks):
        by_doc[doc or f"_{n}"].append((release, material))
    tours = []
    for lines in by_doc.values():
        lines.sort()
        for i in range(0, len(lines), TOUR_MAX_LINES):
            chunk = lines[i:i + TOUR_MAX_LINES]
            tours.append((chunk[0][0], [m for _, m in chunk]))
    return sorted(tours)


FLOW = {"putaway": "inbound", "outbound": "outbound", "replenishment": "replenishment",
        "move": "transfer", "picking": "picking"}


def simulate(tasks, racks, features, fleet, *, multiplier=1.0, seed=0, trace=None, calib=None):
    """tasks: [(sekunda od DAY_START, rodzaj, materiał, dokument)]; fleet: {"agv": n, "kombi": n, "ept": n}.
    Zwraca wskaźniki dnia albo None, gdy wariant nie ma VNA / półek / doków. `trace` (lista) —
    dopisywane są przebiegi agentów do animacji: {"agent", "kind", "depart", "from", "steps"},
    kroki: ("go", punkt, przepływ) · ("wait_until", t) · ("lift", z) · ("dwell", s) ·
    ("pick", klucz, rodzaj, punkt, z) · ("drop", klucz, punkt, z, znika)."""
    lay = Layout(racks, features)
    if not lay.ok():
        return None
    tasks = scale_tasks(tasks, multiplier, seed)
    demand = Counter(m for _, k, m, _ in tasks if k == "picking")
    rank = {m: i for i, (m, _) in enumerate(demand.most_common())}
    by_pallets = Counter(m for _, k, m, _ in tasks if k != "picking").most_common()
    pallet_share = {m: i / max(1, len(by_pallets)) for i, (m, _) in enumerate(by_pallets)}
    rng = random.Random(seed)
    home = {"agv": lay.in_points, "kombi": lay.in_points, "ept": lay.pack}
    calib = calib or {}                # {"trucks": k, "picking": k} z kalibracji na obecnej hali
    ks = {"agv": calib.get("trucks", 1.0), "kombi": calib.get("trucks", 1.0), "ept": calib.get("picking", 1.0)}
    agents = {k: [_Agent(k, home[k][i % len(home[k])], i + 1, ks[k]) for i in range(max(1, fleet.get(k, 1)))]
              for k, _ in FLEET_KINDS}

    def log(agent, depart, origin, steps):
        if trace is not None:
            trace.append({"agent": agent.id, "kind": agent.kind, "depart": depart, "from": origin, "steps": steps})
    waits, done_h, demand_h, late = defaultdict(list), Counter(), Counter(), 0
    k1 = lay.shelf(0)
    n_in = n_out = 0

    def finish(kind, release, start, end):
        nonlocal late
        waits[kind].append(start - release)
        demand_h[int(release // 3600)] += 1
        done_h[int(end // 3600)] += 1
        late += end > HORIZON_S

    def kombi_leg(release, a_from, b_to, z, key, flow, src=(None, 0.0), dst=(None, 0.0)):
        """Kombi jedzie (na pusto) do `a_from`, ładunek bierze nie wcześniej niż `release`.
        src/dst = (punkt ładunku, wysokość) — środek gniazda w regale albo None (posadzka)."""
        k = _pick(agents["kombi"], release, a_from)
        depart, origin = k.free, k.pos
        arrive = k.go(depart, a_from)
        start = max(arrive, release)
        t = k.go(start + HANDLE_S * k.k, b_to) + (2 * z / LIFT_SPEED + HANDLE_S) * k.k
        _work(k, depart, t, idle=start - arrive)
        log(k, depart, origin, [
            ("go", a_from, flow), ("wait_until", start), ("lift", src[1]),
            ("pick", key, "pallet", src[0] or a_from, src[1]), ("lift", 0.0), ("go", b_to, flow),
            ("lift", dst[1]), ("drop", key, dst[0] or b_to, dst[1], False), ("lift", 0.0)])
        return start, t

    def agv_leg(release, stops, key, flow, extra=0.0, vanish=False):
        a = _pick(agents["agv"], release, stops[0])
        depart, origin = max(a.free, release), a.pos
        start = a.go(depart, stops[0])
        t = start + HANDLE_S * a.k
        for p in stops[1:]:
            t = a.go(t, p)
        t += HANDLE_S * a.k + extra            # owijarka (extra) to maszyna — bez kalibracji
        _work(a, depart, t)
        steps = [("go", stops[0], flow), ("wait_until", release), ("pick", key, "pallet", stops[0], 0.0)]
        for i, p in enumerate(stops[1:], 1):
            steps.append(("go", p, flow))
            if extra and i == len(stops) - 2:          # owijarka = przedostatni przystanek
                steps.append(("dwell", extra))
        steps.append(("drop", key, stops[-1], 0.0, vanish))
        log(a, depart, origin, steps)
        return start, t

    for n, (release, kind, material, _doc) in enumerate(tasks):
        if kind == "picking":
            continue
        share, key, flow = pallet_share.get(material, 1.0), f"p{n}", FLOW[kind]
        if kind == "putaway":
            src = lay.in_points[n_in % len(lay.in_points)]
            n_in += 1
            slot, z, hand, center = lay.vna_slot(share, rng, src)
            s, t = agv_leg(release, [src, hand], key, flow)
            _, end = kombi_leg(t, hand, slot, z, key, flow, dst=(center, z))
        elif kind == "outbound":
            dock = lay.out_docks[n_out % len(lay.out_docks)]
            n_out += 1
            slot, z, hand, center = lay.vna_slot(share, rng, dock)
            s, t = kombi_leg(release, slot, hand, z, key, flow, src=(center, z))
            _, end = agv_leg(t, [hand] + lay.wrappers[:1] + [dock], key, flow,
                             WRAP_S if lay.wrappers else 0.0, vanish=True)
        elif kind == "replenishment":
            slot, z, hand, center = lay.vna_slot(share, rng, k1)
            s, t = kombi_leg(release, slot, hand, z, key, flow, src=(center, z))
            _, end = agv_leg(t, [hand, k1], key, flow, vanish=True)
        else:                                  # przesunięcie w VNA
            slot, z, _, center = lay.vna_slot(share, rng, k1)
            other, z2, _, center2 = lay.vna_slot(share, rng, k1)
            s, end = kombi_leg(release, slot, other, max(z, z2), key, flow,
                               src=(center, z), dst=(center2, z2))
        finish(kind, release, s, end)

    for n, (release, materials) in enumerate(_tours([t for t in tasks if t[1] == "picking"])):
        stops = sorted((lay.shelf(rank.get(m, 0)) for m in materials), key=lambda p: (p[1], p[0]))
        e = _pick(agents["ept"], release, stops[0])
        start, origin = max(e.free, release), e.pos
        t, steps = start, []
        for i, p in enumerate(stops):
            t = e.go(t, p) + PICK_DWELL * e.k
            steps += [("go", p, "picking"), ("dwell", PICK_DWELL - HANDLE_S),
                      ("pick", f"c{n}-{i}", "carton", p, 0.9)]
        pack = min(lay.pack, key=lambda q: _manh(q, e.pos))
        t = e.go(t, pack)
        steps += [("go", pack, "picking")] + [("drop", f"c{n}-{i}", pack, 0.8, True) for i in range(len(stops))]
        _work(e, start, t)
        log(e, start, origin, steps)
        for _ in materials:
            finish("picking", release, start, t)

    return _kpi(agents, waits, demand_h, done_h, late, Counter(k for _, k, _, _ in tasks), multiplier)


def _p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(math.ceil(0.95 * len(xs))) - 1)] if xs else 0.0


def _kpi(agents, waits, demand_h, done_h, late, counts, multiplier):
    fleet = []
    for kind, label in FLEET_KINDS:
        group = agents[kind]
        busy = sum(a.busy for a in group)
        fleet.append({"kind": kind, "label": label, "count": len(group), "tasks": sum(a.n for a in group),
                      "busy_h": round(busy / 3600, 1),
                      "util_pct": round(100 * busy / (len(group) * HORIZON_S)),
                      "suggested": max(1, math.ceil(busy / (HORIZON_S * TARGET_UTIL))),
                      "km": round(sum(a.dist for a in group) / 1000, 1)})
    hours = [{"h": DAY_START_H + h, "demand": demand_h.get(h, 0), "done": done_h.get(h, 0)}
             for h in range(min(list(demand_h) + list(done_h) + [0]), max(list(demand_h) + list(done_h) + [0]) + 1)]
    return {"multiplier": multiplier, "tasks": dict(counts), "fleet": fleet,
            "waits": {k: {"mean_min": round(sum(v) / len(v) / 60, 1), "p95_min": round(_p95(v) / 60, 1), "n": len(v)}
                      for k, v in waits.items()},
            "hours": hours, "late": late, "horizon_h": HORIZON_S / 3600}


# ─── ORM ──────────────────────────────────────────────────────────────────────

def load_day_tasks(batch, day):
    """Zadania potwierdzone w dniu `day` (czas lokalny) jako sekundy od DAY_START_H."""
    from django.utils import timezone

    tz = timezone.get_current_timezone()
    out = []
    qs = batch.tasks.exclude(confirmed_at=None).filter(confirmed_at__date=day)
    for kind, material, doc, at in qs.values_list("kind", "material", "document", "confirmed_at").iterator():
        local = timezone.localtime(at, tz)
        sec = (local.hour - DAY_START_H) * 3600 + local.minute * 60 + local.second
        out.append((max(0.0, float(sec)), kind, material, doc))
    return sorted(out, key=lambda t: t[0])
