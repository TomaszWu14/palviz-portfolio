"""Animacja godziny z symulacji dnia (plan 2026-10-02, etap 3b) — czysty Python.

Przebiegi agentów zapisane przez `design_sim.simulate(trace=…)` odtwarzamy na agentach
`blender_agents` → ta sama scena „palviz.blender-flow” i ten sam odtwarzacz 3D. Te same
przydziały i chwile startu co w symulacji; trasy po alejkach i przejazdach poprzecznych
(siatka A* na hali 300 × 180 m byłaby za wolna dla setek ruchów naraz).
"""
from .blender_agents import Agent, Item
from .blender_scene import AGENT_COLORS, FLOW_COLORS, FORMAT, VERSION, _feature_center
from .design_sim import DAY_START_H

MAX_LEGS = 300                  # przebiegów na scenę (godzina szczytu bywa większa — przycinamy jawnie)
LABELS = {"agv": "AGV", "kombi": "Kombi", "ept": "EPT"}


class CorridorRouter:
    """Trasa „jak w magazynie”: wzdłuż korytarza do przejazdu poprzecznego, nim do wysokości
    celu i znowu korytarzem. Interfejs jak `FloorGrid.route` (tego używa `Agent.move`)."""

    def __init__(self, features):
        self.xs = [_feature_center(f)[0] for f in features
                   if f["kind"] == "corridor" and (f.get("depth") or 0) > (f.get("width") or 0)]

    def route(self, a, b):
        a, b = tuple(a), tuple(b)
        if abs(a[1] - b[1]) < 0.5 or not self.xs:
            return [a, b]
        cx = min(self.xs, key=lambda x: abs(a[0] - x) + abs(b[0] - x))
        pts = [a, (cx, a[1]), (cx, b[1]), b]
        return [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]


def _pick_time(leg):
    """Chwila przejęcia ładunku: kombi rusza wcześniej niż AGV, ale paletę bierze po nim —
    odtwarzamy w tej kolejności, żeby klatki palety szły do przodu w czasie."""
    return next((s[1] for s in leg["steps"] if s[0] == "wait_until"), leg["depart"])


def window_legs(trace, hour, limit=MAX_LEGS):
    """Przebiegi rozpoczęte w godzinie `hour` (zegar 5–21). Zwraca (przebiegi, ile było, przycięto?)."""
    w0 = (hour - DAY_START_H) * 3600
    legs = sorted((t for t in trace if w0 <= t["depart"] < w0 + 3600), key=lambda t: t["depart"])
    kept = sorted(legs[:limit], key=_pick_time)
    return kept, len(legs), len(legs) > limit


def build_sim_scene(model, floor, racks, features, trace, hour, *, info=None):
    w0 = (hour - DAY_START_H) * 3600
    legs, total, truncated = window_legs(trace, hour)
    router = CorridorRouter(features)
    agents, items = {}, {}
    for leg in legs:
        a = agents.get(leg["agent"])
        if a is None:
            label = f"{LABELS.get(leg['kind'], leg['kind'])} {leg['agent'].rsplit('-', 1)[-1]}"
            a = agents[leg["agent"]] = Agent(leg["agent"], leg["kind"], label, leg["from"], t0=leg["depart"] - w0)
        a.wait_until(leg["depart"] - w0)
        for step in leg["steps"]:
            op = step[0]
            if op == "go":
                a.move(router, step[1], step[2])
            elif op == "wait_until":
                a.wait_until(step[1] - w0)
            elif op == "lift":
                a.lift_to(step[1])
            elif op == "dwell":
                a.wait(step[1])
            elif op == "pick":
                _, key, kind, pos, z = step
                it = items.get(key)
                if it is None:
                    it = items[key] = Item(key, kind, pos, z, a.heading, appear=max(0.0, a.t - 2.0))
                else:                # trasa korytarzami bywa dłuższa niż w symulacji — czekaj na ładunek
                    a.wait_until(it.keyframes[-1]["t"])
                a.pick_up(it, handling=0.6 if kind == "carton" else 1.5)
            elif op == "drop":
                _, key, pos, z, vanish = step
                it = items.get(key)
                if it is not None and it in a.carrying:
                    a.drop(it, pos, z, handling=0.6 if it.kind == "carton" else 1.5, vanish=vanish)
    duration = max([a.t for a in agents.values()] + [0.0]) + 2.0
    return {
        "format": FORMAT, "version": VERSION, "units": "m", "time_unit": "s",
        "coords": "hala: x w prawo, y w głąb (oś z w three.js); z w górę",
        "model": model, "floor": floor, "racks": racks, "features": features,
        "agents": [a.as_dict(AGENT_COLORS[a.kind]) for a in agents.values()],
        "items": [i.as_dict() for i in items.values()],
        "flows": [f for a in agents.values() for f in a.flows],
        "flow_colors": FLOW_COLORS, "conveyors": [], "pallets": [], "stock_stats": None,
        "duration": round(duration, 2),
        "source": {"picking": "simulation", "forklifts": "simulation",
                   "simulation": {"hour": hour, "legs": len(legs), "total": total,
                                  "truncated": truncated, "limit": MAX_LEGS, **(info or {})}},
    }
