"""Osie czasu agentów (wózki widłowe, ludzie) i ładunków (palety, kartony) dla
eksportu animacji do Blendera. Czysty Python — bez Django i bez bpy.

Agent porusza się po trasach z `FloorGrid.route`; każda klatka kluczowa agenta
jest lustrzanie dopisywana do niesionych ładunków, więc Blender tylko liniowo
interpoluje położenia — cała „logika" przepływu jest policzona tutaj.
"""
import math

from .blender_route import heading_deg, path_length, unwrap_deg

# Parametry ruchu [m/s, s] — realistyczne rzędy wielkości dla hali paletowej.
# kombi = wózek systemowy VNA (operator jedzie z widłami), agv = transport poziomy dok ↔ bufor,
# ept = picker na elektrycznym wózku paletowym (kompletacja z półek K1). Wartości z katalogu.
SPEED = {"forklift": 2.2, "person": 1.3, "kombi": 2.5, "agv": 1.5, "ept": 2.0}
LIFT_SPEED = 0.45          # m/s — podnoszenie/opuszczanie wideł
TURN_TIME = 0.5            # s — obrót w miejscu na zakręcie trasy
# Gdzie ładunek „siedzi" względem agenta: (wysunięcie do przodu [m], wysokość [m]).
CARRY_OFFSET = {"forklift": (1.15, 0.12), "person": (0.35, 0.95), "kombi": (1.3, 0.12),
                "agv": (0.0, 0.35), "ept": (0.9, 0.3)}

ITEM_SIZE = {"pallet": (1.2, 0.8, 1.1), "carton": (0.4, 0.3, 0.3),     # [m] dł×szer×wys
             "container": (12.19, 2.44, 2.59)}                            # kontener 40' (obrys)


def _r(v):
    return round(v, 3)


class Item:
    """Ładunek: paleta albo karton. `appear`/`vanish` sterują widocznością w Blenderze."""

    def __init__(self, item_id, kind, pos, z=0.0, heading=0.0, appear=0.0, sku=""):
        self.id, self.kind, self.sku = item_id, kind, sku
        self.appear, self.vanish = appear, None
        self.keyframes = []
        self.key(appear, pos, z, heading)

    def key(self, t, pos, z, heading):
        kf = {"t": _r(t), "x": _r(pos[0]), "y": _r(pos[1]), "z": _r(z), "heading": _r(heading)}
        if self.keyframes and self.keyframes[-1]["t"] == kf["t"]:
            self.keyframes[-1] = kf
        else:
            self.keyframes.append(kf)

    def as_dict(self):
        return {"id": self.id, "kind": self.kind, "sku": self.sku,
                "size": list(ITEM_SIZE[self.kind]), "appear": _r(self.appear),
                "vanish": None if self.vanish is None else _r(self.vanish),
                "keyframes": self.keyframes}


class Agent:
    """Agent z osią czasu ruchu: rodzaj z `SPEED` (wózek, pracownik, kombi, AGV, EPT)."""

    def __init__(self, agent_id, kind, label, pos, t0=0.0, heading=0.0):
        self.id, self.kind, self.label = agent_id, kind, label
        self.t, self.pos, self.heading, self.lift = t0, tuple(pos), heading, 0.0
        self.keyframes, self.carrying, self.flows = [], [], []
        self._key()

    # ── klatki kluczowe ─────────────────────────────────────────────────────
    def _carry_pose(self, item):
        fwd, up = CARRY_OFFSET[self.kind]
        if item.kind == "carton" and self.kind in ("person", "ept"):
            # kolejne kartony „piętrzą się" w rękach / na wózku ręcznym
            up += 0.3 * self.carrying.index(item)
        h = math.radians(self.heading)
        return ((self.pos[0] + fwd * math.cos(h), self.pos[1] + fwd * math.sin(h)),
                up + self.lift)

    def _key(self):
        kf = {"t": _r(self.t), "x": _r(self.pos[0]), "y": _r(self.pos[1]),
              "heading": _r(self.heading), "lift": _r(self.lift)}
        if self.keyframes and self.keyframes[-1]["t"] == kf["t"]:
            self.keyframes[-1] = kf
        else:
            self.keyframes.append(kf)
        for it in self.carrying:
            pos, z = self._carry_pose(it)
            it.key(self.t, pos, z, self.heading)

    # ── akcje ───────────────────────────────────────────────────────────────
    def face(self, target_heading):
        target = unwrap_deg(self.heading, target_heading)
        if abs(target - self.heading) > 1.0:
            self.t += TURN_TIME
            self.heading = target
            self._key()

    def move(self, grid, target, flow_kind):
        """Jazda/przejście trasą A* do `target`; trasa trafia też do mapy przepływów."""
        pts = grid.route(self.pos, tuple(target))
        if path_length(pts) < 1e-3:
            return
        self.flows.append({"kind": flow_kind, "agent": self.id,
                           "points": [[_r(x), _r(y)] for x, y in pts]})
        speed = SPEED[self.kind]
        for nxt in pts[1:]:
            self.face(heading_deg(self.pos, nxt, self.heading))
            self.t += math.dist(self.pos, nxt) / speed
            self.pos = tuple(nxt)
            self._key()

    def wait(self, seconds):
        self.t += seconds
        self._key()

    def wait_until(self, t):
        """Postój do chwili `t` (realny znacznik zadania); zajęty agent nie cofa się w czasie."""
        if t > self.t:
            self.wait(t - self.t)

    def lift_to(self, height):
        if abs(height - self.lift) < 1e-3:
            return
        self.t += abs(height - self.lift) / LIFT_SPEED
        self.lift = height
        self._key()

    def pick_up(self, item, handling=1.5):
        """Przejęcie ładunku: klatka „na miejscu" → po `handling` s ładunek jest na agencie."""
        item.key(self.t, (item.keyframes[-1]["x"], item.keyframes[-1]["y"]),
                 item.keyframes[-1]["z"], item.keyframes[-1]["heading"])
        self.carrying.append(item)
        self.wait(handling)

    def drop(self, item, pos, z, handling=1.5, vanish=False):
        """Odłożenie ładunku w `pos` na wysokości `z` (np. gniazdo regału albo dok)."""
        self.carrying.remove(item)
        self.wait(handling)
        item.key(self.t, pos, z, self.heading)
        if vanish:                       # np. załadowano na naczepę / wydano z magazynu
            item.vanish = self.t + 0.5

    def as_dict(self, color):
        return {"id": self.id, "kind": self.kind, "label": self.label, "color": color,
                "keyframes": self.keyframes}
