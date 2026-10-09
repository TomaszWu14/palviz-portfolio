"""Przyjęcie kontenera z kartonami luzem (plan 2026-10-02, etap 2b) — czysty Python.

Kontener 40' stoi przy doku kontenerowym → kartony jadą przenośnikiem teleskopowym na
stanowisko paletyzacji → pracownik układa z nich paletę → gotowa paleta czeka na AGV
(sztafeta AGV → kombi w `blender_scene._relay_task`).
"""
import math

from .blender_agents import ITEM_SIZE, Agent, Item
from .blender_route import heading_deg
from .design_catalog import ELEMENTS

# ponytail: dok kontenerowy i stanowiska rozpoznajemy po etykietach nadawanych przez generator
# hali; gdy dojdą ręcznie rysowane hale z kontenerami — osobny rodzaj elementu hali.
CONTAINER_DOCK_TAG = "kontenerowy"
PALLETIZE_TAG = "paletyzacja"
WRAPPER_TAG = "owijarka"

CONVEYOR_SPEED = ELEMENTS["conveyor"]["params"]["speed"]      # m/s
CONVEYOR_H = ELEMENTS["conveyor"]["params"]["height"]         # m — wysokość taśmy
CONVEYOR_W = 0.6
CARTON_EVERY_S = 3.0          # ~1 200 kartonów/h na przenośniku
CARTONS_PER_PALLET = 12       # demo: realnie kilkadziesiąt — skrót, żeby paleta powstała w minutę
CONTAINER_IN_M = 5.0          # skąd ruszają kartony: tyle w głąb kontenera od drzwi
PLACE_S = 2.0                 # odłożenie kartonu na paletę


def outward(center, floor):
    """Kierunek „na zewnątrz hali” od elementu przy ścianie: normalna najbliższej ściany."""
    x, y = center
    walls = [(x, (-1, 0)), (floor["width"] - x, (1, 0)), (y, (0, -1)), (floor["depth"] - y, (0, 1))]
    return min(walls)[1]


def _at(p, d, k):
    return (p[0] + d[0] * k, p[1] + d[1] * k)


def container_inbound(docks, stations, floor, *, pallets_per_dock, t0=0.0):
    """docks: [(środek doku kontenerowego)], stations: [(środek stanowiska paletyzacji)].
    Zwraca (agenci, ładunki, przenośniki, gotowe palety [(pozycja, t_gotowa, paleta)])."""
    agents, items, conveyors, ready = [], [], [], []
    for i, dock in enumerate(docks):
        if not stations:
            break
        station = min(stations, key=lambda s: math.dist(s, dock))
        out = outward(dock, floor)
        length = ITEM_SIZE["container"][0]
        items.append(Item(f"kontener-{i + 1}", "container", _at(dock, out, length / 2 + 0.2), 0.0,
                          heading_deg(dock, _at(dock, out, 1.0)), appear=0.0))
        path = [_at(dock, out, CONTAINER_IN_M), _at(dock, out, -1.0), station]
        conveyors.append({"points": [[round(x, 3), round(y, 3)] for x, y in path],
                          "width": CONVEYOR_W, "height": CONVEYOR_H})
        legs = [math.dist(a, b) / CONVEYOR_SPEED for a, b in zip(path, path[1:], strict=False)]
        worker = Agent(f"paletyzacja-{i + 1}", "person", f"Paletyzacja {i + 1}",
                       _at(station, (-out[1], out[0]), 1.0), t0=t0)
        n = 0
        for p in range(pallets_per_dock):
            pallet = None
            for _ in range(CARTONS_PER_PALLET):
                start = t0 + n * CARTON_EVERY_S
                c = Item(f"karton-k{i + 1}-{n + 1}", "carton", path[0], CONVEYOR_H, 0.0, appear=start)
                t = start
                for pt, dt in zip(path[1:], legs, strict=False):
                    t += dt
                    c.key(t, pt, CONVEYOR_H, 0.0)
                c.vanish = t + PLACE_S
                items.append(c)
                if pallet is None:
                    pallet = Item(f"paleta-k{i + 1}-{p + 1}", "pallet", station, 0.0, 0.0, appear=t)
                    items.append(pallet)
                worker.wait_until(t)
                worker.face(heading_deg(worker.pos, station, worker.heading))
                worker.wait(PLACE_S)
                n += 1
            ready.append((station, worker.t, pallet))
        agents.append(worker)
    return agents, items, conveyors, ready
