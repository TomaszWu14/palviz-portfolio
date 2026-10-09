# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from ui.views.core import (
    get_object_or_404, JsonResponse, module_required, render, WarehouseLayout,
    WarehouseLocationMasterBatch, WarehouseSnapshot,
)
from . import warehouse_map_detail_helpers as h
from .warehouse_map_core import _RACK_RE, _hall_features_data  # noqa: F401

# Antresola (A0-A3) — piętro, którego NIE modelujemy w mapie 3D. Usuwane całkowicie.
# (Przeniesione z warehouse_map_core.py — file-size gate; konsument to ten moduł.)
_ANTRESOLA_PREFIXES = {"A0", "A1", "A2", "A3"}
_ROW_FIELDS = ("location_code", "zone", "aisle", "stack", "col_idx", "level",
               "is_empty", "blocked_pick", "blocked_put", "capacity_mm", "warehouse_type")


def _is_antresola(code, zone=""):
    """Lokalizacja antresoli (prefiks A0-A3) — po zone albo prefiksie kodu."""
    prefix = (zone or (code or "").split("-")[0] or "").strip().upper()
    return prefix in _ANTRESOLA_PREFIXES


def _special_zones(rows_list, hu_by_code):
    """Lokalizacje spoza wzorca regału (blok/zewnętrzne, np. ZWROTY-01) grupowane po
    prefiksie kodu → lista stref z zajętością. Zamiast samego licznika 'nonrack' — user
    widzi CO to za strefy i ile w nich stoi (bezstratność, Etap 5)."""
    groups = {}
    for r in rows_list:
        code = (r["location_code"] or "").strip().upper()
        if not code or _RACK_RE.match(code):
            continue
        prefix = r.get("zone") or code.split("-")[0] or "—"
        g = groups.setdefault(prefix, {"prefix": prefix, "total": 0, "occupied": 0,
                                       "blocked": 0, "hu": 0})
        g["total"] += 1
        if not r["is_empty"]:
            g["occupied"] += 1
        if r["blocked_pick"] or r["blocked_put"]:
            g["blocked"] += 1
        g["hu"] += hu_by_code.get(code, 0)
    for g in groups.values():
        g["fill_pct"] = round(100 * g["occupied"] / g["total"]) if g["total"] else 0
    return sorted(groups.values(), key=lambda g: -g["total"])


@module_required("magazyn")   # B-001: wcześniej bez strażnika — anonim dostawał mapę i JSON
def warehouse_map_detail(request, pk):
    """3D visualization of one snapshot (orkiestrator — logika w warehouse_map_detail_helpers)."""
    snapshot = get_object_or_404(WarehouseSnapshot, pk=pk)
    active_layout = WarehouseLayout.objects.filter(is_active=True).first()
    rack_types = h.rack_types_ctx()
    active_master = WarehouseLocationMasterBatch.objects.filter(is_active=True).first()
    master_data = h.master_data_by_code(active_master)
    slot_width_mm = h.slot_width_mm(snapshot, rack_types, master_data)
    # Antresola (A0-A3) — usuwana całkowicie z modelu 3D. Filtr u źródła → render,
    # aisle_meta, strefy, sloty i statystyki poniżej wszystko ją pomijają.
    rows_list = [r for r in snapshot.rows.values(*_ROW_FIELDS)
                 if not _is_antresola(r["location_code"], r.get("zone"))]
    geo = h.build_geometry(active_layout, rows_list, slot_width_mm)
    hu_counts = h.hu_by_code()
    locs, aisle_geo = h.build_locs(rows_list, geo, rack_types, master_data, hu_counts)
    # R2 (fix ciężkiego ładowania): ~37k lokalizacji NIE wchodzi inline w HTML — szablon
    # dociąga je fetch-em z tego samego widoku w trybie ?fmt=json.
    if request.GET.get("fmt") == "json":
        return JsonResponse({"locs": locs})
    ctx = {
        "snapshot": snapshot,
        "pallet_stats": h.pallet_stats(hu_counts, rows_list),
        "active_layout": active_layout,
        "active_master": active_master,
        "use_physical": geo.use_physical,
        # Geometria regałów tylko przy prawdziwym layoucie; z kodów = pozycje przybliżone.
        "from_layout": geo.from_layout,
        "slot_width_mm": slot_width_mm,
        "stats": h.row_stats(rows_list),
        # Etap 5: strefy nietypowe (kody spoza wzorca regału) grupowane po prefiksie.
        "special_zones": _special_zones(rows_list, hu_counts),
        # Etap 5 slice 2+3: ręczne elementy hali + siatki lokalizacji nietypowych w strefach.
        "hall_features": _hall_features_data(active_layout.features.all(), rows_list, hu_counts)
                         if active_layout else [],
        # Surowe obiekty — json_script koduje raz (pre-dump = podwójne kodowanie → czarny canvas).
        "rack_types_ctx": rack_types,
        "aisle_meta": h.aisle_meta(aisle_geo, geo.aisle_cfg),
        "aisle_stats": h.aisle_stats(rows_list),
        "cap_groups": h.cap_groups(rows_list),
        "loc_count": len(locs),
    }
    return render(request, "ui/warehouse_map/detail.html", ctx)


__all__ = [
    'warehouse_map_detail', '_is_antresola', '_special_zones',
]
