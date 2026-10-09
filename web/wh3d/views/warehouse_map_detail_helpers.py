"""Czyste helpery widoku ``warehouse_map_detail`` (CODE-001: rozbicie funkcji CC=84).

Moduł NIE jest re-eksportowany przez ``wh3d.views`` (brak star-importu w __init__) —
konsumentem jest wyłącznie ``warehouse_map_detail.py``.
"""
import re
from typing import NamedTuple

from django.db.models import Count

from ui.views.core import (
    _apply_aisle_widths, _apply_perpendicular_wings, _as_int, _build_stack_base,
    _stack_base_from_rows, HandlingUnit, WarehouseRackType,
)
from .warehouse_map_core import _RACK_RE


def _norm_code(code):
    return (code or "").strip().upper()


def rack_types_ctx():
    """Definicje typów regałów (kod → wymiary/poziomy/kolor) dla sceny 3D."""
    return {
        rt.code: {
            "width_mm": rt.width_mm,
            "depth_mm": rt.depth_mm,
            "manip_mm": rt.manip_mm,
            "max_weight_kg": rt.max_weight_kg,
            "max_volume_m3": rt.max_volume_m3,
            "level_heights": {str(k): v for k, v in (rt.level_heights or {}).items()},
            "level_cols": {str(k): _as_int(v, 1) for k, v in rt.level_cols.items()} if rt.level_cols else {},
            "level_weights": {str(k): _as_int(v, 0) for k, v in (rt.level_weights or {}).items()},
            "color": rt.color_hex,
        }
        for rt in WarehouseRackType.objects.all()
    }


def master_data_by_code(active_master):
    """Aktywne master data lokalizacji (nadpisują domyślne typu) — kod → pola."""
    if not active_master:
        return {}
    return {
        m["location_code"]: m
        for m in active_master.locations.values(
            "location_code", "warehouse_type", "height_mm", "width_mm", "depth_mm",
            "max_volume_m3", "max_weight_kg"
        )
    }


def slot_width_mm(snapshot, rack_types, master_data):
    """Mediana skoku slotu (manip_mm) do skali 3D — master data > typy użyte w snapshocie."""
    used_types = set(snapshot.rows.values_list("warehouse_type", flat=True).distinct())
    type_widths = [
        rt["manip_mm"] for code, rt in rack_types.items()
        if code in used_types and rt.get("manip_mm", 0) > 0
    ]
    md_widths = [m["width_mm"] for m in master_data.values() if m.get("width_mm", 0) > 0]
    all_widths = md_widths or type_widths
    return sorted(all_widths)[len(all_widths) // 2] if all_widths else 0


def row_stats(rows_list):
    """Statystyki panelu z odfiltrowanych wierszy (bez antresoli)."""
    total = len(rows_list)
    occupied = sum(1 for r in rows_list if not r["is_empty"])
    return {
        "total": total, "occupied": occupied, "empty": total - occupied,
        "fill_pct": round(occupied / total * 100) if total else 0,
        "blocked": sum(1 for r in rows_list if r["blocked_pick"] or r["blocked_put"]),
        # Bezstratność: ile lokalizacji to blok/zewnętrzne (kod spoza wzorca regału).
        "nonrack": sum(1 for r in rows_list if not _RACK_RE.match(_norm_code(r["location_code"]))),
    }


def _normstack(s):
    d = re.sub(r"\D", "", str(s))
    return d or str(s)


def layout_lookup(base):
    """Lookup (aleja, stos) → pozycja z dopasowaniem po cyfrach stosu ('430D' → '430').
    Po każdej modyfikacji bazy trzeba zbudować lookup od nowa."""
    norm = {}
    for (a, s), v in base.items():
        norm.setdefault((a, _normstack(s)), v)

    def pos(a, s):
        return base[(a, s)] if (a, s) in base else norm.get((a, _normstack(s)))
    return pos


class Geometry(NamedTuple):
    stack_base: dict
    layout_pos: object      # callable (aisle, stack) -> pos | None
    from_layout: bool
    use_physical: bool
    aisle_cfg: dict
    aisle_angles: dict
    rotated_aisles: set


def _aisle_widths(aisle_cfg, slot_mm):
    """Krok pz per aleja (1 rząd regału + korytarz); None = bez zmian rozstawu."""
    if not (aisle_cfg and slot_mm):
        return None
    return {a: 1 + max(1, round(c.width_m * 1000 / slot_mm)) for a, c in aisle_cfg.items()}


def build_geometry(active_layout, rows_list, slot_mm):
    """Pozycjonowanie: fizyczny layout, jeśli pokrywa ≥50% par (aleja, stos) snapshotu,
    inaczej siatka z kodów; potem szerokości alej i skrzydło prostopadłe (kąt ~90°)."""
    layout_base = _build_stack_base(active_layout) if active_layout else {}
    layout_pos = layout_lookup(layout_base)
    distinct_keys = {(r["aisle"], r["stack"]) for r in rows_list}
    matched = sum(1 for (a, s) in distinct_keys if layout_pos(a, s) is not None)
    use_layout = bool(distinct_keys) and matched / len(distinct_keys) >= 0.5
    aisle_cfg = {c.aisle: c for c in active_layout.aisles.all()} if active_layout else {}
    aisle_widths = _aisle_widths(aisle_cfg, slot_mm)
    if use_layout:
        if aisle_widths:
            layout_base = _apply_aisle_widths(layout_base, aisle_widths)
            layout_pos = layout_lookup(layout_base)
        stack_base = layout_base
    else:
        stack_base = _stack_base_from_rows(rows_list, aisle_widths)
    aisle_angles = {a: c.angle_deg for a, c in aisle_cfg.items() if c.angle_deg}
    rotated_aisles = set()
    if aisle_angles:
        stack_base, rotated_aisles = _apply_perpendicular_wings(stack_base, aisle_angles)
        if use_layout and rotated_aisles:
            layout_base = stack_base
            layout_pos = layout_lookup(layout_base)
    return Geometry(stack_base, layout_pos, use_layout and bool(layout_base), bool(stack_base),
                    aisle_cfg, aisle_angles, rotated_aisles)


def hu_by_code():
    """Realne palety (stock HU) per znormalizowany kod lokalizacji."""
    out = {}
    for row in (HandlingUnit.objects.filter(shipment__is_stock=True)
                .exclude(location="").values("location").annotate(n=Count("id"))):
        # GROUP BY idzie po surowym kodzie — warianty zapisu tej samej lokalizacji
        # (wielkość liter/spacje) to osobne wiersze, więc po normalizacji SUMUJEMY.
        key = _norm_code(row["location"])
        out[key] = out.get(key, 0) + row["n"]
    return out


def _loc_state(r):
    """0 wolna, 1 zajęta, 2 zablokowana zajęta, 3 zablokowana pusta."""
    if r["blocked_pick"] or r["blocked_put"]:
        return 2 if not r["is_empty"] else 3
    return 0 if r["is_empty"] else 1


def _loc_height(md, rt, r):
    """Wysokość: master data > typ regału per poziom > pojemność ze snapshotu."""
    if md.get("height_mm", 0):
        return md["height_mm"]
    lv = str(r["level"])
    if rt.get("level_heights", {}).get(lv):
        return _as_int(rt["level_heights"][lv], r["capacity_mm"])
    return r["capacity_mm"]


def _loc_pos(r, geo):
    # from_layout: klucz (aleja, stos); z kodów: (strefa, aleja, stos) — inaczej strefy
    # o tej samej alei/stosie (B0 i A0 mają '01') nakładałyby się na siatce.
    if geo.from_layout:
        return geo.layout_pos(r["aisle"], r["stack"])
    if not geo.use_physical:
        return None
    return geo.stack_base.get((_zone_key(r), r["aisle"], r["stack"]))


def _zone_key(r):
    """Strefa wiersza — jak klucz siatki z kodów: zone albo prefiks kodu."""
    return r["zone"] or str(r["location_code"] or "").split("-")[0]


def build_locs(rows_list, geo, rack_types, master_data, hu_counts):
    """Rekordy lokalizacji dla sceny 3D + zasięg przestrzenny alei per (strefa, aleja).

    Kluczem NIE jest ``pz``: skrzydło prostopadłe numeruje rzędy od 0 (jak blok główny),
    a różne strefy mogą mieć tę samą numerację alei — klucz po pz/alei gubił aleje."""
    locs, aisle_geo = [], {}
    for r in rows_list:
        hu_count = hu_counts.get(_norm_code(r["location_code"]), 0)
        md = master_data.get(r["location_code"], {})
        wtype = md.get("warehouse_type") or r.get("warehouse_type", "")
        rt = rack_types.get(wtype, {})
        tail = [r["level"], _loc_state(r), _loc_height(md, rt, r)]
        vol = md.get("max_volume_m3") or rt.get("max_volume_m3", 0.0)
        wgt = md.get("max_weight_kg") or rt.get("max_weight_kg", 0.0)
        pos = _loc_pos(r, geo)
        if pos is None:
            locs.append([r["aisle"], r["stack"], *tail, 0, vol, wgt, wtype,
                         r["location_code"], r["aisle"], hu_count, 0])
            continue
        px, pz = pos[0] + r["col_idx"], pos[1]
        g = aisle_geo.setdefault((_zone_key(r), r["aisle"]), {"pzs": set(), "px": [px, px]})
        g["pzs"].add(pz)
        g["px"][0], g["px"][1] = min(g["px"][0], px), max(g["px"][1], px)
        # 13. pole: kąt obrotu alei (Etap 4) — 90° dla lokalizacji w skrzydle prostopadłym.
        ang = geo.aisle_angles.get(r["aisle"], 0) if r["aisle"] in geo.rotated_aisles else 0
        locs.append([px, pz, *tail, 1, vol, wgt, wtype,
                     r["location_code"], r["aisle"], hu_count, ang])
    return locs, aisle_geo


def _aisle_labels(keys):
    """Etykieta alei: sam numer, a gdy ta sama numeracja jest w kilku strefach — 'strefa-aleja'."""
    zones_of = {}
    for zone, aisle in keys:
        zones_of.setdefault(aisle, set()).add(zone)
    # Wiersze bez alei (strefy nietypowe, np. ZWROTY-01) → sama strefa zamiast 'ZWROTY-'.
    return {(z, a): ("-".join(p for p in (z, a) if p) if len(zones_of[a]) > 1 else a)
            for z, a in keys}


def aisle_meta(aisle_geo, aisle_cfg):
    """Metadane przestrzenne alei (etykiety/korytarze 3D) per (strefa, aleja), po alei."""
    labels = _aisle_labels(aisle_geo)
    return sorted([
        {
            "aisle": aisle,
            "zone": zone,
            "label": labels[(zone, aisle)],
            "pz_min": min(g["pzs"]),
            "pz_max": max(g["pzs"]),
            "pz_center": round(sum(g["pzs"]) / len(g["pzs"]), 2),
            "px_min": g["px"][0],
            "px_max": g["px"][1],
            # width_m z konfiguracji alei (None, jeśli nieskonfigurowana) — Etap 3.
            "width_m": aisle_cfg[aisle].width_m if aisle in aisle_cfg else None,
            # angle_deg — 90° dla alei w skrzydle prostopadłym (Etap 4), inaczej 0.
            "angle_deg": aisle_cfg[aisle].angle_deg if aisle in aisle_cfg else 0,
        }
        for (zone, aisle), g in aisle_geo.items()
    ], key=lambda x: (x["aisle"], x["zone"]))


def aisle_stats(rows_list):
    """Zajętość per (strefa, aleja) do panelu bocznego (max 30 alei).

    Liczone z ``rows_list`` (już bez antresoli) — nie z ``snapshot.rows``, które
    wliczały antresolę A0-A3 do alei regałowych o tej samej numeracji."""
    groups = {}
    for r in rows_list:
        g = groups.setdefault((_zone_key(r), r["aisle"]), {"total": 0, "occupied": 0})
        g["total"] += 1
        g["occupied"] += 0 if r["is_empty"] else 1
    labels = _aisle_labels(groups)
    stats = [
        {"aisle": aisle, "zone": zone, "label": labels[(zone, aisle)],
         "total": g["total"], "occupied": g["occupied"],
         "pct": round(g["occupied"] / g["total"] * 100) if g["total"] else 0}
        for (zone, aisle), g in groups.items()
    ]
    return sorted(stats, key=lambda x: (x["aisle"], x["zone"]))[:30]


_CAP_BUCKETS = ((700, "≤700"), (1200, "≤1200"), (2000, "≤2000"))


def cap_groups(rows_list):
    """Rozkład pojemności (capacity_mm) wierszy mapy — bez antresoli (filtr rows_list)."""
    groups = {"0": 0, "≤700": 0, "≤1200": 0, "≤2000": 0, ">2000": 0}
    for cap in (r["capacity_mm"] for r in rows_list):
        if cap == 0:
            groups["0"] += 1
            continue
        key = next((label for limit, label in _CAP_BUCKETS if cap <= limit), ">2000")
        groups[key] += 1
    return groups


def pallet_stats(hu_counts, rows_list):
    """Ile realnych HU trafiło na mapę (kod pasuje) vs poza mapą."""
    rendered = {_norm_code(r["location_code"]) for r in rows_list}
    total = sum(hu_counts.values())
    placed = sum(v for k, v in hu_counts.items() if k in rendered)
    return {"total": total, "placed": placed, "unplaced": total - placed}
