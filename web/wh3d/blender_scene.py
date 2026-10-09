"""Eksport modelu magazynu (moduł B — `WarehouseModel`) do sceny animacji Blendera.

Wynik to JSON „palviz.blender-flow" czytany przez `tools/blender/palviz_warehouse_anim.py`
(uruchamiany w Blenderze ręcznie, headless `blender -b -P …` albo przez Blender MCP).
Scena niesie geometrię hali (regały, doki, stanowiska) oraz policzone tutaj osie czasu:
  • wózki widłowe — przyjęcie (dok → gniazdo regału) i wydanie (gniazdo → dok) palet,
  • ludzie — kompletacja kartonów wg `PickerActivity` (realna kolejność pobrań pickera);
    bez importu aktywności — deterministyczna symulacja demo (oznaczona w `source`),
  • palety w lokalizacjach — realny stan (palety HU + snapshot SAP), patrz blender_stock.

`build_scene` jest czystą funkcją na dictach (testowalna bez bazy);
`build_scene_for_model` dociąga dane z ORM.
"""
import math
import random

from .blender_agents import Agent, Item
from .blender_containers import (
    CONTAINER_DOCK_TAG, PALLETIZE_TAG, WRAPPER_TAG, container_inbound,
)
from .blender_route import FloorGrid, heading_deg, rack_axes, rack_corners, rack_point
from .blender_stock import build_pallets, load_master_levels, load_stock_inputs
from .blender_tasks import FLOW_KIND, resolve_moves, window_source

FORMAT = "palviz.blender-flow"
VERSION = 2          # v2: + pallets[] / stock_stats (stan magazynu)

AGENT_COLORS = {"forklift": "#f59e0b", "person": "#2563eb", "kombi": "#ea580c",
                "agv": "#0d9488", "ept": "#4f46e5"}
FLOW_COLORS = {"inbound": "#16a34a", "outbound": "#dc2626", "picking": "#2563eb",
               "replenishment": "#7c3aed", "transfer": "#0891b2"}
DOCK_KINDS = ("dock", "gate")
STATION_KINDS = ("station", "leader", "returns")

FORKLIFT_APPROACH = 1.7     # m od lica regału — wózek stoi przodem, widły pod gniazdem
PERSON_APPROACH = 0.55
PICK_DWELL = 6.0            # s — pobranie kartonu z lokalizacji
HIGH_BAY_M = 8.0            # regał wysoki i …
VNA_AISLE_MAX_M = 2.2       # … przy wąskim korytarzu = VNA: AGV podwozi, kombi odkłada (reach truck
                            #   potrzebuje ~3 m — obecna hala zostaje przy zwykłych wózkach)
SHELF_LEVEL_M = 1.0         # poziom niższy → regał półkowy (K1): kompletacja z wózka EPT
HANDOVER_M = 2.0            # punkt przekazania palety AGV ↔ kombi: tyle przed czołem rzędu
WRAP_S = 60.0               # owinięcie palety folią przed wydaniem


def _span(q, u, size):
    o = q["x"] * u[0] + q["y"] * u[1]
    return o, o + size


def _aisle_m(r, racks):
    """Najwęższy korytarz przy regale: po każdej stronie najbliższy równoległy regał naprzeciwko
    (nakładający się wzdłuż). Plecy w plecy (< 0,3 m) albo brak sąsiada = brak korytarza → None."""
    u_w, u_d = rack_axes(r["angle"])
    rw, rd = _span(r, u_w, r["width"]), _span(r, u_d, r["depth"])
    nearest = {}                                   # strona (+1 za / −1 przed) → prześwit
    for q in racks:
        if q is r or abs(((q["angle"] - r["angle"]) + 180) % 360 - 180) > 1:
            continue
        qw, qd = _span(q, u_w, q["width"]), _span(q, u_d, q["depth"])
        if min(rw[1], qw[1]) - max(rw[0], qw[0]) <= 0.2:
            continue
        side, gap = (1, qd[0] - rd[1]) if qd[0] >= rd[1] - 0.01 else (-1, rd[0] - qd[1])
        if gap >= -0.01 and gap < nearest.get(side, 1e9):
            nearest[side] = gap
    aisles = [g for g in nearest.values() if g >= 0.3]
    return min(aisles, default=None)


def _vna_racks(racks):
    return [r for r in racks if r["n_levels"] * r["level_h"] >= HIGH_BAY_M
            and (_aisle_m(r, racks) or 99) <= VNA_AISLE_MAX_M]


def _is_shelf(r):
    return r["level_h"] < SHELF_LEVEL_M


def _feature_center(f):
    """Środek elementu hali (narożnik + obrót jak w three.js)."""
    u_w, u_d = rack_axes(f.get("angle") or 0)
    hw, hd = (f.get("width") or 0) / 2, (f.get("depth") or 0) / 2
    return (f["x"] + u_w[0] * hw + u_d[0] * hd, f["y"] + u_w[1] * hw + u_d[1] * hd)


def _slot(rack, bay_idx, level):
    """Gniazdo regału: środek boku `bay_idx` (0..n-1) na poziomie `level` (1 = posadzka)."""
    bays = max(1, rack["n_bays"])
    bay_idx = min(bays - 1, max(0, bay_idx))
    along = (bay_idx + 0.5) * rack["width"] / bays
    z = (min(max(1, level), max(1, rack["n_levels"])) - 1) * rack["level_h"]
    return along, z


def _access(grid, rack, along, approach):
    """Punkt obsługi gniazda z alejki: przód regału (−u_d), a gdy zablokowany — tył."""
    for across in (-approach, rack["depth"] + approach):
        p = rack_point(rack, along, across)
        if grid.is_free(*grid.cell_of(p)):
            return p
    return rack_point(rack, along, -approach)


class _Ctx:
    def __init__(self, floor, racks, features):
        self.grid = FloorGrid(floor["width"], floor["depth"], racks)
        docks = [_feature_center(f) for f in features if f["kind"] in DOCK_KINDS]
        stations = [_feature_center(f) for f in features if f["kind"] in STATION_KINDS]
        fallback = (floor["width"] / 2, max(0.5, floor["depth"] - 1.0))
        self.docks = docks or [fallback]
        self.stations = stations or self.docks
        self.agents, self.items, self.conveyors = [], [], []

    def free_point(self, p):
        c = self.grid.nearest_free(p)
        return self.grid.center(*c) if c else p


def _rack_end(ctx, rack, bay_idx, level):
    """Koniec ruchu w gnieździe regału: (punkt dojazdu z alejki, środek gniazda, wysokość)."""
    along, z = _slot(rack, bay_idx, level)
    return _access(ctx.grid, rack, along, FORKLIFT_APPROACH), rack_point(rack, along, rack["depth"] / 2), z


def _point_end(ctx, points, near):
    """Koniec ruchu na posadzce (dok / stanowisko) — najbliższy do `near`."""
    return ctx.free_point(min(points, key=lambda p: math.dist(p, near))), None, 0.0


def _carry(ctx, fl, src, dst, flow, item_id, sku=""):
    """Wózek przewozi paletę z `src` do `dst` (krotki z _rack_end/_point_end): paleta z doku
    pojawia się tam przed przyjazdem wózka, paleta odstawiona na dok znika (załadunek)."""
    (a_src, slot_src, z_src), (a_dst, slot_dst, z_dst) = src, dst
    if slot_src is None:
        pal = Item(item_id, "pallet", a_src, 0.0, fl.heading, appear=max(0.0, fl.t - 3.0), sku=sku)
        fl.move(ctx.grid, a_src, flow)
        fl.pick_up(pal)
    else:
        face = heading_deg(a_src, slot_src, fl.heading)
        pal = Item(item_id, "pallet", slot_src, z_src + 0.05, face, appear=fl.t, sku=sku)
        fl.move(ctx.grid, a_src, flow)
        fl.face(face)
        fl.lift_to(z_src + 0.1)
        fl.pick_up(pal)
        fl.lift_to(0.0)
    ctx.items.append(pal)
    fl.move(ctx.grid, a_dst, flow)
    if slot_dst is None:
        fl.drop(pal, a_dst, 0.0, vanish=True)
    else:
        fl.face(heading_deg(a_dst, slot_dst, fl.heading))
        fl.lift_to(z_dst + 0.15)
        fl.drop(pal, slot_dst, z_dst + 0.05)
        fl.lift_to(0.0)


def _forklift_task(ctx, fl, rack, bay_idx, level, inbound, n, sku=""):
    """Demo: przyjęcie (dok → gniazdo) albo wydanie (gniazdo → dok), doki po kolei."""
    slot = _rack_end(ctx, rack, bay_idx, level)
    dock = (ctx.free_point(ctx.docks[n % len(ctx.docks)]), None, 0.0)
    _carry(ctx, fl, *((dock, slot, "inbound") if inbound else (slot, dock, "outbound")),
           f"paleta-{fl.id}-{n}", sku)


def _handover(ctx, rack, near):
    """Przekazanie palety w przejeździe poprzecznym: przed tym czołem rzędu, które bliżej `near`."""
    ends = (rack_point(rack, -HANDOVER_M, -FORKLIFT_APPROACH),
            rack_point(rack, rack["width"] + HANDOVER_M, -FORKLIFT_APPROACH))
    return ctx.free_point(min(ends, key=lambda p: math.dist(p, near)))


def _relay_task(ctx, agv, kombi, rack, bay_idx, level, inbound, n, sku="", pickup=None, wrap=None):
    """Demo VNA: AGV wozi paletę dok ↔ czoło rzędu, kombi przejmuje ją tam i odkłada
    w gnieździe (wydanie odwrotnie). Drugi agent czeka, aż pierwszy odstawi paletę.
    `pickup` = (miejsce, t_gotowa, paleta) z paletyzacji kontenera zamiast palety z doku;
    `wrap` = owijarka po drodze do doku wydań."""
    dock = ctx.free_point(ctx.docks[n % len(ctx.docks)])
    hand = _handover(ctx, rack, dock)
    along, z = _slot(rack, bay_idx, level)
    access = _access(ctx.grid, rack, along, FORKLIFT_APPROACH)
    slot = rack_point(rack, along, rack["depth"] / 2)
    face = heading_deg(access, slot, 0.0)
    pid = f"paleta-{agv.id}-{n}"
    if inbound:
        if pickup:                                # paleta ułożona z kartonów kontenera
            src, ready, pal = pickup
            hand = _handover(ctx, rack, src)
            agv.move(ctx.grid, ctx.free_point(src), "inbound")
            agv.wait_until(ready)
        else:
            pal = Item(pid, "pallet", dock, 0.0, agv.heading, appear=max(0.0, agv.t - 3.0), sku=sku)
            ctx.items.append(pal)
            agv.move(ctx.grid, dock, "inbound")
        agv.pick_up(pal)
        agv.move(ctx.grid, hand, "inbound")
        agv.drop(pal, hand, 0.0)
        kombi.wait_until(agv.t)
        kombi.move(ctx.grid, hand, "inbound")
        kombi.pick_up(pal)
        kombi.move(ctx.grid, access, "inbound")
        kombi.face(face)
        kombi.lift_to(z + 0.15)
        kombi.drop(pal, slot, z + 0.05)
        kombi.lift_to(0.0)
    else:
        pal = Item(pid, "pallet", slot, z + 0.05, face, appear=kombi.t, sku=sku)
        kombi.move(ctx.grid, access, "outbound")
        kombi.face(face)
        kombi.lift_to(z + 0.1)
        kombi.pick_up(pal)
        kombi.lift_to(0.0)
        kombi.move(ctx.grid, hand, "outbound")
        kombi.drop(pal, hand, 0.0)
        agv.wait_until(kombi.t)
        agv.move(ctx.grid, hand, "outbound")
        agv.pick_up(pal)
        if wrap:
            agv.move(ctx.grid, wrap, "outbound")
            agv.wait(WRAP_S)
        agv.move(ctx.grid, dock, "outbound")
        agv.drop(pal, dock, 0.0, vanish=True)
        ctx.items.append(pal)


def _labelled(features, kind, tag):
    return [_feature_center(f) for f in features
            if f["kind"] == kind and tag in (f.get("label") or "").lower()]


def _container_flow(ctx, features, floor, forklifts, tasks):
    """Kontenery przy dokach kontenerowych → przenośnik → paletyzacja; zwraca gotowe palety
    (w kolejności gotowości) dla AGV i owijarkę (najbliższą dokom wydań) albo None."""
    docks = _labelled(features, "dock", CONTAINER_DOCK_TAG)
    stations = _labelled(features, "station", PALLETIZE_TAG)
    inbound = sum(1 for k in range(forklifts) for n in range(tasks) if (n + k) % 2 == 0)
    used = docks[:max(1, min(len(docks), forklifts))] if docks else []
    agents, items, conveyors, ready = container_inbound(
        used, stations, floor, pallets_per_dock=-(-inbound // max(1, len(used))))
    ctx.agents += agents
    ctx.items += items
    ctx.conveyors += conveyors
    wrappers = _labelled(features, "station", WRAPPER_TAG)
    wrap = ctx.free_point(wrappers[0]) if wrappers else None
    return sorted(ready, key=lambda r: r[1]), wrap


def _task_forklifts(ctx, moves):
    """Wózki z zadań EWM: agent na zasób, zadania od znacznika potwierdzenia (blender_tasks)."""
    fleet = {}
    for n, (t, name, kind, src, dst, sku) in enumerate(moves):
        fl = fleet.get(name)
        if fl is None:
            k = len(fleet)
            fl = fleet[name] = Agent(f"wozek-{k + 1}", "forklift", name,
                                     ctx.free_point(ctx.docks[k % len(ctx.docks)]))
        fl.wait_until(t)
        ends = []
        for ep in (src, dst):
            near = ends[0][0] if ends else fl.pos
            ends.append(_rack_end(ctx, *ep[1:]) if ep[0] == "rack"
                        else _point_end(ctx, ctx.docks if ep[0] == "dock" else ctx.stations, near))
        _carry(ctx, fl, ends[0], ends[1], FLOW_KIND[kind], f"wt-{n + 1}", sku)
    ctx.agents.extend(fleet.values())


def _picker_route(ctx, person, stops, drop_at):
    for n, (rack, bay_idx, level, sku) in enumerate(stops):
        along, z = _slot(rack, bay_idx, level)
        access = _access(ctx.grid, rack, along, PERSON_APPROACH)
        person.move(ctx.grid, access, "picking")
        person.face(heading_deg(access, rack_point(rack, along, rack["depth"] / 2), person.heading))
        carton = Item(f"karton-{person.id}-{n}", "carton", rack_point(rack, along, 0.25),
                      min(z, 1.6) + 0.1, person.heading, appear=person.t, sku=sku)
        ctx.items.append(carton)
        person.wait(PICK_DWELL - 1.5)
        person.pick_up(carton)
    person.move(ctx.grid, drop_at, "picking")
    for it in list(person.carrying):
        person.drop(it, drop_at, 0.8, handling=0.6, vanish=True)


def _demo_picks(rng, racks, n_pickers, per_picker):
    out = []
    for p in range(n_pickers):
        stops = []
        for _ in range(per_picker):
            r = rng.choice(racks)
            stops.append((r, rng.randrange(max(1, r["n_bays"])), rng.randint(1, 2), ""))
        out.append((f"Picker {p + 1}", stops))
    return out


def build_scene(model, floor, racks, features, picks=None, *, moves=None, forklifts=3,
                forklift_tasks=4, demo_pickers=3, demo_picks=5, seed=None):
    """Składa scenę. `picks` = [(nazwa_pickera, [(rack, bay_idx, level, sku), …]), …]
    albo None → demo. `moves` = ruchy wózków z zadań EWM (blender_tasks.resolve_moves) albo
    None → wózki demo. Racks/features w formacie widoku 3D (x, y, width, depth, angle…)."""
    rng = random.Random(model.get("id", 0) if seed is None else seed)
    ctx = _Ctx(floor, racks, features)
    source = {"picking": "picker_activity" if picks else "demo",
              "forklifts": "demo" if moves is None else "ewm_tasks"}

    high = _vna_racks(racks)
    shelves = [r for r in racks if _is_shelf(r)]
    if racks:
        if moves is not None:
            _task_forklifts(ctx, moves)
            forklifts = 0
        if high:                                  # hala wysokiego składowania: AGV + kombi
            source["equipment"] = "agv_kombi"
            ready, wrap = _container_flow(ctx, features, floor, forklifts, forklift_tasks)
            for k in range(max(0, forklifts)):
                dock = ctx.free_point(ctx.docks[k % len(ctx.docks)])
                agv = Agent(f"agv-{k + 1}", "agv", f"AGV {k + 1}", dock, t0=k * 4.0)
                kombi = Agent(f"kombi-{k + 1}", "kombi", f"Kombi {k + 1}",
                              _handover(ctx, high[k % len(high)], dock), t0=k * 4.0)
                for n in range(max(0, forklift_tasks)):
                    r = rng.choice(high)
                    inbound = (n + k) % 2 == 0
                    _relay_task(ctx, agv, kombi, r, rng.randrange(max(1, r["n_bays"])),
                                rng.randint(1, max(1, r["n_levels"])), inbound=inbound, n=n,
                                pickup=ready.pop(0) if inbound and ready else None, wrap=wrap)
                ctx.agents += [agv, kombi]
            forklifts = 0
        truck_racks = [r for r in racks if not _is_shelf(r)] or racks
        for k in range(max(0, forklifts)):
            start = ctx.free_point(ctx.docks[k % len(ctx.docks)])
            fl = Agent(f"wozek-{k + 1}", "forklift", f"Wózek {k + 1}", start, t0=k * 4.0)
            for n in range(max(0, forklift_tasks)):
                r = rng.choice(truck_racks)
                _forklift_task(ctx, fl, r, rng.randrange(max(1, r["n_bays"])),
                               rng.randint(1, max(1, r["n_levels"])), inbound=(n + k) % 2 == 0, n=n)
            ctx.agents.append(fl)

        # Kompletacja demo z półek K1 (gdy są) na wózkach EPT; realne trasy — piesi pickerzy.
        picker_kind = "ept" if shelves and not picks else "person"
        routes = picks or _demo_picks(rng, shelves or racks, demo_pickers, demo_picks)
        for k, (name, stops) in enumerate(routes):
            start = ctx.free_point(ctx.stations[k % len(ctx.stations)])
            person = Agent(f"picker-{k + 1}", picker_kind, name or f"Picker {k + 1}", start, t0=k * 2.5)
            _picker_route(ctx, person, stops, start)
            ctx.agents.append(person)

    duration = max([a.t for a in ctx.agents] + [0.0]) + 2.0
    return {
        "format": FORMAT, "version": VERSION,
        "units": "m", "time_unit": "s",
        "coords": "hala: x w prawo, y w głąb (oś z w three.js); z w górę",
        "model": model, "floor": floor, "racks": racks, "features": features,
        "agents": [a.as_dict(AGENT_COLORS[a.kind]) for a in ctx.agents],
        "items": [i.as_dict() for i in ctx.items],
        "flows": [f for a in ctx.agents for f in a.flows],
        "flow_colors": FLOW_COLORS,
        "conveyors": ctx.conveyors,
        "pallets": [], "stock_stats": None,
        "duration": round(duration, 2),
        "source": source,
    }


# ─── ORM → dicty ─────────────────────────────────────────────────────────────

def _activity_picks(rows, locator, max_pickers, max_picks):
    """Aktywność pickerów (picker, kod, materiał; kolejność = confirmed_at) → trasy.
    Lokalizacje nierozpoznane lub spoza regałów modelu są pomijane."""
    per_picker = {}
    for picker, code, material in rows:
        found = locator.rack_and_bay(code)
        if not found:
            continue
        name = picker or "Picker"
        if name not in per_picker and len(per_picker) >= max_pickers:
            continue
        stops = per_picker.setdefault(name, [])
        if len(stops) < max_picks:
            rack, bay_idx, _col, level = found
            stops.append((rack, bay_idx, level, material or ""))
    return list(per_picker.items())


def model_racks(wm):
    """Regały WarehouseModel jako dicty w formacie sceny (x, y, width, depth, level_h…)."""
    return [{
        "id": r.pk, "zone": r.zone, "rack_id": r.rack_id,
        "x": r.x_m or 0.0, "y": r.y_m or 0.0, "angle": r.angle_deg or 0.0,
        "width": r.width_m, "depth": r.depth_cm / 100, "level_h": r.level_height_cm / 100,
        "n_bays": max(1, r.n_bays), "n_levels": max(1, r.n_levels),
    } for r in wm.racks.order_by("zone", "rack_id")]


def model_floor(wm, racks):
    """Hala co najmniej tak duża, jak obrys regałów (dane bywają „poza halą")."""
    corners = [p for r in racks for p in rack_corners(r)]
    width = max([wm.floor_width_m] + [p[0] + 2 for p in corners])
    depth = max([wm.floor_depth_m] + [p[1] + 2 for p in corners])
    return {"width": round(width, 2), "depth": round(depth, 2)}


def build_scene_for_model(wm, batch=None, *, snapshot=None, with_pallets=True,
                          max_pickers=6, max_picks=12, wt=None, **kwargs):
    """Scena dla `WarehouseModel`. `batch` = PickerActivityBatch (None → demo kompletacji),
    `snapshot` = WarehouseSnapshot (zajętość/blokady z SAP); palety HU na stanie zawsze.
    `wt` = okno zadań EWM z `blender_tasks.load_window` (None → wózki demo)."""
    from ui.views.core import hall_feature_dict

    racks = model_racks(wm)
    features = [hall_feature_dict(f) for f in wm.features.all()]
    floor = model_floor(wm, racks)

    snap_rows, stock, activity = ([], [], [])
    if with_pallets:
        snap_rows, stock, _ = load_stock_inputs(snapshot, None)
    rows = []
    if batch is not None:
        rows = list(batch.activities.order_by("confirmed_at").values_list(
            "picker_name", "location_code", "material_code"))
        activity = [(code, mat) for _p, code, mat in rows]
    wt_codes = {c for r in wt["rows"] for c in (r[4], r[5]) if c} if wt else ()
    pallets, stats, locator = build_pallets(racks, snap_rows, stock, activity,
                                            levels=load_master_levels(), extra_codes=wt_codes)
    picks = _activity_picks(rows, locator, max_pickers, max_picks) or None
    moves, skipped = resolve_moves(wt["rows"], locator, wt["start"], wt["scale"]) if wt else (None, 0)
    scene = build_scene({"id": wm.pk, "name": wm.name}, floor, racks, features, picks,
                        moves=moves, **kwargs)
    if wt:
        scene["source"].update(window_source(wt, len(moves), skipped))
    scene["pallets"] = pallets if with_pallets else []
    scene["stock_stats"] = stats if with_pallets else None
    if batch is not None:
        scene["source"]["batch"] = batch.name
    if with_pallets:
        scene["source"]["pallets"] = "snapshot+stan_hu" if snapshot is not None else "stan_hu"
        if snapshot is not None:
            scene["source"]["snapshot"] = snapshot.name
    return scene
