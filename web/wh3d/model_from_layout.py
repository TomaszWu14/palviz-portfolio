"""Model OBECNEGO magazynu w metrach z danych GROOVE: mapa lokalizacji + master lokalizacji.

Mapa lokalizacji (moduł A: WarehouseLayoutCell.grid_col/grid_row) jest siatką komórek;
mapa 3D przelicza ją na metry jedną „podziałką gniazda" (mediana szerokości gniazda
z mastera) — tutaj używamy dokładnie tej samej podziałki, więc model zgadza się z mapą.

Z każdej alejki (strefa + nr alejki z kodu lokalizacji) powstaje regał modelu (moduł B):
  • orientacja: poziomo/pionowo wg rozpiętości komórek, kierunek = rosnące numery boków,
  • boki = liczba różnych numerów boków, poziomy = max poziom z kodu,
  • wysokość poziomu = mediana (max wysokość gniazda na poziomie) + 150 mm belki,
  • głębokość = mediana głębokości gniazd z mastera (brak → 1,1 m).
Kody parsowane konwencją mapy 3D / SAP (litera EWM = poziom w stosie — ewm_levels).

`plan_racks` jest czystą funkcją (testowalna bez bazy); `create_model_from_layout`
zapisuje model w bazie.
"""
from collections import Counter, defaultdict
from statistics import median

from .blender_route import rack_corners
from .locations import parse_code

BEAM_MM = 150
BACK_TO_BACK_M = 0.1         # szczelina między regałami „plecami do siebie"
CORRIDOR_M = 3.0             # domyślny korytarz roboczy (wózek wysokiego składowania)
DEFAULT_LEVEL_MM = 2000
DEFAULT_DEPTH_MM = 1100
MARGIN_M = 2.0


def slot_pitch_mm(master):
    """Podziałka gniazda jak w mapie 3D: mediana szerokości gniazd z mastera."""
    widths = sorted(m["width_mm"] for m in master.values() if (m.get("width_mm") or 0) > 0)
    return widths[len(widths) // 2] if widths else 0


def _origin_for_bbox(rack, x0, y0):
    """Przesuwa regał tak, by lewy-górny róg jego obrysu wypadł w (x0, y0)."""
    probe = dict(rack, x=0.0, y=0.0)
    cs = rack_corners(probe)
    return x0 - min(c[0] for c in cs), y0 - min(c[1] for c in cs)


def _spaced_line(prev, cur, run, ctx, new):
    """Nowa pozycja linii `cur` po linii `prev` → (pozycja, czy rozstaw przyjęty).

    `run` = liczba ciasnych (przyjętych) odstępów od ostatniego ZNANEGO odstępu: parzysta →
    `prev`|`cur` plecami do siebie, nieparzysta → korytarz. Znany odstęp (szerokość korytarza
    z konfiguracji alej albo realny odstęp mapy) to korytarz za `prev` — po nim `cur` zaczyna
    nową parę, więc wywołujący zeruje `run`."""
    depth_of, aisles_of, aisle_cfg, report = ctx
    cfg = [aisle_cfg[a] for a in aisles_of[prev] if a in aisle_cfg]
    if cfg:
        return new[prev] + depth_of[prev] + max(cfg), False
    if cur - prev >= depth_of[prev] + 0.05:
        return new[prev] + (cur - prev), False   # mapa ma już realny odstęp
    report["assumed_spacing"] = True
    gap = BACK_TO_BACK_M if run % 2 == 0 else CORRIDOR_M
    return new[prev] + depth_of[prev] + gap, True


def _line_depths_and_aisles(group, key, lines):
    """Dla każdej linii: max głębokość regałów i zbiór alejek „strefa-nr"."""
    depth_of = {ln: max(r["depth"] for r in group if round(r[key], 4) == ln) for ln in lines}
    aisles_of = {ln: {f"{r['zone']}-{r['rack_id']}" for r in group if round(r[key], 4) == ln}
                 for ln in lines}
    return depth_of, aisles_of


def _space_group(group, key, aisle_cfg, report):
    """Rozsuwa linie jednej orientacji (key = "y0" dla poziomych, "x0" dla pionowych)."""
    lines = sorted({round(r[key], 4) for r in group})
    if len(lines) < 2:
        return
    ctx = (*_line_depths_and_aisles(group, key, lines), aisle_cfg, report)
    new, run = {lines[0]: lines[0]}, 0
    for prev, cur in zip(lines, lines[1:], strict=False):
        new[cur], assumed = _spaced_line(prev, cur, run, ctx, new)
        run = run + 1 if assumed else 0          # znany odstęp = korytarz → nowa para
    for r in group:
        r[key] = new[round(r[key], 4)]


def _space_lines(racks, aisle_cfg, report):
    """Rozsuwa równoległe rzędy regałów, gdy siatka mapy jest ciaśniejsza niż głębokość
    regału (np. rysunek B0: 1 rząd arkusza = 1 alejka). Szerokość korytarza bierze
    z konfiguracji alej GROOVE; bez niej: pary plecami do siebie + korytarz CORRIDOR_M,
    liczone od ostatniego znanego odstępu (korytarz z konfiguracji albo realny odstęp mapy)."""
    for horizontal in (True, False):
        key = "y0" if horizontal else "x0"
        _space_group([r for r in racks if r["_horizontal"] == horizontal], key, aisle_cfg, report)


def _group_cells(cells):
    """Komórki mapy → ({(strefa, alejka): [komórki]}, liczba pominiętych kodów).

    Duplikat kodu (po tej samej normalizacji strip/upper co parse_code) liczy się raz —
    pierwsze wystąpienie wygrywa (pozycja i poziom), jak w import_locations."""
    groups, skipped, seen = defaultdict(list), 0, set()
    for code, col, row, level in cells:
        norm = (code or "").strip().upper()
        if norm in seen:
            continue
        seen.add(norm)
        p = parse_code(norm)
        if not p:
            skipped += 1
            continue
        zone, rack_id, stack, _col_idx, lvl = p
        groups[(zone, rack_id)].append({"code": norm, "col": col, "row": row,
                                        "stack": int(stack), "level": level or lvl})
    return groups, skipped


def _orientation(base):
    """(poziomo?, oś wzdłuż regału, oś w poprzek) wg rozpiętości komórek poziomu 1."""
    cols = [c["col"] for c in base]
    rows = [c["row"] for c in base]
    horizontal = (max(cols) - min(cols)) >= (max(rows) - min(rows))
    return (horizontal, "col", "row") if horizontal else (horizontal, "row", "col")


def _angle(base, axis, horizontal):
    """Kąt regału (kierunek = rosnące numery boków wzdłuż osi) + boki {nr: [pozycje]}."""
    by_stack = defaultdict(list)
    for c in base:
        by_stack[c["stack"]].append(c[axis])
    order = sorted(by_stack)
    rising = median(by_stack[order[-1]]) >= median(by_stack[order[0]])
    angle = (0.0 if rising else 180.0) if horizontal else (-90.0 if rising else 90.0)
    return angle, by_stack


def _level_and_depth_mm(cs, master):
    """(wysokość poziomu, głębokość) [mm] z mastera; brak danych → wartości domyślne."""
    depths = [master[c["code"]]["depth_mm"] for c in cs
              if (master.get(c["code"], {}).get("depth_mm") or 0) > 0]
    per_level = defaultdict(list)
    for c in cs:
        h = master.get(c["code"], {}).get("height_mm") or 0
        if h > 0:
            per_level[c["level"]].append(h)
    level_mm = (median(max(v) for v in per_level.values()) + BEAM_MM) if per_level else DEFAULT_LEVEL_MM
    return level_mm, (median(depths) if depths else DEFAULT_DEPTH_MM)


def _plan_rack(zone, rack_id, cs, master, s, multi_row):
    """Regał modelu z komórek jednej alejki (z roboczymi x0/y0/_horizontal)."""
    base = [c for c in cs if c["level"] == 1] or cs
    horizontal, axis, across = _orientation(base)
    line = Counter(c[across] for c in base).most_common(1)[0][0]
    if len({c[across] for c in base}) > 1:
        multi_row.append(f"{zone}-{rack_id}")
    angle, by_stack = _angle(base, axis, horizontal)
    level_mm, depth_mm = _level_and_depth_mm(cs, master)
    depth_m = depth_mm / 1000
    span = [c[axis] for c in base]
    width_m = (max(span) - min(span) + 1) * s
    n_bays = len(by_stack)
    rack = {"zone": zone, "rack_id": rack_id, "angle": angle, "width": width_m,
            "level_h": round(level_mm / 1000, 3),
            "depth": depth_m, "n_bays": n_bays, "n_levels": max(c["level"] for c in cs),
            "bay_width_cm": max(1, round(width_m * 100 / n_bays)),
            "depth_cm": max(1, round(depth_m * 100)), "level_height_cm": max(1, round(level_mm / 10))}
    lo = min(span) * s
    rack["x0"], rack["y0"] = (lo, line * s) if horizontal else (line * s, lo)
    rack["_horizontal"] = horizontal
    return rack


def _shift_to_margin(racks):
    """Przesuwa całość na margines od (0, 0) — mapa bywa „w połowie arkusza".
    Zwraca (dx, dy, szerokość posadzki, głębokość posadzki)."""
    if not racks:
        return 0.0, 0.0, 50.0, 30.0
    xs, ys = _corner_coords(racks)
    dx, dy = MARGIN_M - min(xs), MARGIN_M - min(ys)
    for r in racks:
        r["x"], r["y"] = round(r["x"] + dx, 3), round(r["y"] + dy, 3)
    xs, ys = _corner_coords(racks)
    return dx, dy, max(xs) + MARGIN_M, max(ys) + MARGIN_M


def _corner_coords(racks):
    """(wszystkie x, wszystkie y) narożników obrysów regałów."""
    corners = [p for r in racks for p in rack_corners(r)]
    return [p[0] for p in corners], [p[1] for p in corners]


def plan_racks(cells, master, slot_mm=None, aisle_cfg=None):
    """cells: iterowalne (code, grid_col, grid_row, level|None); master: {code: {height_mm,
    width_mm, depth_mm}}; aisle_cfg: {"B0-01": szerokość korytarza za rzędem [m]}.
    Zwraca (racks, report) — racks w formacie WarehouseModelRack."""
    slot_mm = slot_mm or slot_pitch_mm(master) or 900
    s = slot_mm / 1000
    groups, skipped = _group_cells(cells)
    multi_row = []
    racks = [_plan_rack(zone, rack_id, cs, master, s, multi_row)
             for (zone, rack_id), cs in sorted(groups.items())]

    report = {"assumed_spacing": False}
    _space_lines(racks, aisle_cfg or {}, report)
    for rack in racks:
        rack["x"], rack["y"] = _origin_for_bbox(rack, rack.pop("x0"), rack.pop("y0"))
        rack.pop("_horizontal")

    dx, dy, floor_w, floor_d = _shift_to_margin(racks)
    report.update({"racks": len(racks), "cells": sum(len(v) for v in groups.values()),
                   "skipped_codes": skipped, "multi_row_aisles": multi_row, "slot_mm": slot_mm,
                   "offset": (round(dx, 3), round(dy, 3)),
                   "floor": (round(floor_w, 1), round(floor_d, 1))})
    return racks, report


def create_model_from_layout(layout, master_batch=None, name=None):
    """Zapisuje WarehouseModel (+ regały, + elementy hali z layoutu) i zwraca (model, report)."""
    from django.db import transaction

    from ui.models import WarehouseHallFeature, WarehouseModel, WarehouseModelRack

    cells = list(layout.cells.values_list("location_code", "grid_col", "grid_row", "level"))
    master = {}
    if master_batch is not None:
        for m in master_batch.locations.values("location_code", "height_mm", "width_mm", "depth_mm"):
            master[m["location_code"].strip().upper()] = m
    aisle_cfg = {a.aisle.strip().upper(): a.width_m for a in layout.aisles.all() if a.width_m}
    racks, report = plan_racks(cells, master, aisle_cfg=aisle_cfg)
    s = report["slot_mm"] / 1000
    min_col = min((c[1] for c in cells), default=0)
    min_row = min((c[2] for c in cells), default=0)
    dx, dy = report["offset"]
    with transaction.atomic():
        wm = WarehouseModel.objects.create(
            name=name or f"Obecny magazyn — {layout.name}",
            notes=(f"Wygenerowano z mapy lokalizacji „{layout.name}”"
                   + (f" i mastera „{master_batch.name}”" if master_batch else "")
                   + f"; podziałka gniazda {report['slot_mm']} mm"
                   + ("; rozstaw rzędów przyjęty (pary + korytarz 3 m) — sprawdź."
                      if report["assumed_spacing"] else ".")),
            floor_width_m=report["floor"][0], floor_depth_m=report["floor"][1])
        WarehouseModelRack.objects.bulk_create([WarehouseModelRack(
            model=wm, zone=r["zone"], rack_id=r["rack_id"], n_bays=r["n_bays"],
            n_levels=r["n_levels"], bay_width_cm=r["bay_width_cm"], depth_cm=r["depth_cm"],
            level_height_cm=r["level_height_cm"], x_m=r["x"], y_m=r["y"], angle_deg=r["angle"],
        ) for r in racks])
        # Elementy hali layoutu są w metrach od narożnika bloku regałów mapy 3D.
        for f in layout.features.all():
            WarehouseHallFeature.objects.create(
                model=wm, kind=f.kind, label=f.label, zone_code=f.zone_code,
                x_m=round(f.x_m + min_col * s + dx, 3), y_m=round(f.y_m + min_row * s + dy, 3),
                width_m=f.width_m, depth_m=f.depth_m, angle_deg=f.angle_deg,
                color_hex=f.color_hex, notes=f.notes)
    report["features"] = layout.features.count()
    return wm, report
