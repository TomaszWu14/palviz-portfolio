# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from ui.views.core import (
    _md_role, _planner, _sap_get, get_object_or_404, HALL_FEATURE_COLORS,
    hall_feature_dict, HandlingUnitItem, JsonResponse, messages, module_required, Q,
    redirect, render, require_POST, save_hall_features, WarehouseAisleConfig,
    WarehouseHallFeature, WarehouseLayout, WarehouseLocationMasterBatch,
    WarehouseSnapshot, WarehouseSnapshotRow,
)
from django.utils import timezone
from django.urls import reverse
import re

from .warehouse_map_upload_parse import read_upload_rows, snapshot_row_fields

# Regałami są WYŁĄCZNIE lokalizacje B0- (decyzja użytkownika). Kanoniczny kod regału:
# B0-ALEJA-STOS-SUFIKS (opcjonalny -N dla lokalizacji dzielonych). Reszta = blok/zewnętrzny.
_RACK_RE = re.compile(r'^B0-\d+-\d+[A-Za-z](?:-\d+)?$', re.IGNORECASE)

# _is_antresola/_special_zones przeniesione do warehouse_map_detail.py (file-size gate).

@module_required("magazyn")
def warehouse_map(request):
    """3D warehouse map — list of snapshots + upload form."""
    snapshots = WarehouseSnapshot.objects.all()[:10]
    active_layout = WarehouseLayout.objects.filter(is_active=True).first()
    layouts = WarehouseLayout.objects.all()[:5]
    active_master = WarehouseLocationMasterBatch.objects.filter(is_active=True).first()
    masters = WarehouseLocationMasterBatch.objects.all()[:5]
    from wh3d.models_tasks import WarehouseTaskBatch       # zakładka „Projektowanie magazynu”
    return render(request, "ui/warehouse_map/index.html", {
        "design_batch": WarehouseTaskBatch.objects.filter(status="done").first(),
        "design_steps_off": [(3, "Dzień projektowy", "Po imporcie zadań EWM."),
                             (4, "Kalibracja na obecnej hali", "Po imporcie zadań EWM."),
                             (5, "Prognoza wzrostu", "Po imporcie zadań EWM."),
                             (6, "Symulacja dnia", "Po imporcie zadań EWM."),
                             (7, "Porównanie wariantów", "Po imporcie zadań EWM.")],
        "snapshots": snapshots,
        "active_layout": active_layout,
        "layouts": layouts,
        "active_master": active_master,
        "masters": masters,
    })

@_planner
def warehouse_where_is(request):
    """„Gdzie jest produkt?" — wyszukaj fizyczne lokalizacje stocku dla danego indeksu.

    Źródłem są jednostki HU magazynowe (shipment.is_stock=True): zwraca listę pozycji z
    lokalizacją, ilością, LOT-em i datą ważności, pogrupowaną po lokalizacji."""
    query = request.GET.get("q", "").strip()
    rows, total_qty, locations = [], 0.0, set()
    if query:
        items = (HandlingUnitItem.objects
                 .filter(hu__shipment__is_stock=True)
                 .filter(Q(product__code__icontains=query) | Q(product__ean__icontains=query)
                         | Q(product__name__icontains=query) | Q(ref_code__icontains=query)
                         | Q(description__icontains=query))
                 .select_related("hu", "product")
                 .order_by("hu__location", "hu__seq")[:300])
        for it in items:
            loc = it.hu.location or "—"
            qty = it.alt_qty or it.expected_qty or 0
            total_qty += qty
            if it.hu.location:
                locations.add(it.hu.location)
            rows.append({
                "location": loc, "hu_ref": it.hu.ref, "hu_pk": it.hu.pk,
                "code": it.product.code if it.product else it.ref_code,
                "name": (it.product.name if it.product else it.description),
                "lot": it.lot, "expiry": it.expiry,
                "qty": qty, "unit": it.alt_unit or it.unit,
                "status": it.hu.get_status_display()})
    return render(request, "ui/warehouse/where_is.html", {
        "query": query, "rows": rows, "total_qty": total_qty,
        "location_count": len(locations), "today": timezone.localdate()})


def _upload_error(request, msg):
    messages.error(request, msg)
    return redirect("ui:warehouse_map")


def _create_snapshot_rows(snapshot, rows_data):
    """Wiersze eksportu → WarehouseSnapshotRow (bulk po 5000). Zwraca liczniki raportu."""
    stats = {"rack": 0, "block": 0, "occupied": 0, "blocked": 0}
    bulk = []
    for row in rows_data:
        loc = _sap_get(row, "location")
        if not loc:
            continue
        loc = str(loc).strip().upper()
        # kanoniczne (regał) vs blok/zewnętrzny — do raportu bezstratności
        stats["rack" if _RACK_RE.match(loc) else "block"] += 1
        fields = snapshot_row_fields(loc, row)
        if not fields["is_empty"]:
            stats["occupied"] += 1
        if fields["blocked_pick"] or fields["blocked_put"]:
            stats["blocked"] += 1
        bulk.append(WarehouseSnapshotRow(snapshot=snapshot, **fields))
        if len(bulk) >= 5000:
            WarehouseSnapshotRow.objects.bulk_create(bulk)
            bulk = []
    if bulk:
        WarehouseSnapshotRow.objects.bulk_create(bulk)
    return stats


def _save_snapshot(name, rows_data):
    """Atomowo: snapshot + wiersze; brak rozpoznanych lokalizacji → ValueError (rollback)."""
    from django.db import transaction

    with transaction.atomic():
        snapshot = WarehouseSnapshot.objects.create(name=name)
        stats = _create_snapshot_rows(snapshot, rows_data)
        snapshot.row_count = snapshot.rows.count()
        if snapshot.row_count == 0:
            raise ValueError("Nie rozpoznano żadnych lokalizacji — sprawdź nagłówki kolumn w pliku.")
        snapshot.occupied_count = stats["occupied"]
        snapshot.blocked_count = stats["blocked"]
        snapshot.save()
    return snapshot, stats


@_md_role
@require_POST
def warehouse_map_upload(request):
    """Process SAP WMS Excel export and create a WarehouseSnapshot."""
    f = request.FILES.get("file")
    if not f:
        return _upload_error(request, "Brak pliku.")
    if f.size > 10 * 1024 * 1024:
        return _upload_error(request, "Plik zbyt duży (max 10 MB).")
    name = request.POST.get("name", "").strip() or f.name

    try:
        rows_data = read_upload_rows(f)
    except Exception as exc:
        return _upload_error(request, f"Błąd odczytu pliku: {exc}")
    if rows_data is None:
        return _upload_error(request, "Obsługiwane formaty: xlsx, xls, csv")

    try:
        snapshot, stats = _save_snapshot(name, rows_data)
    except Exception as exc:
        return _upload_error(request, f"Błąd zapisu snapshotu: {exc}")

    messages.success(
        request,
        f"Wgrano {snapshot.row_count} lokalizacji ({stats['rack']} regałowych, "
        f"{stats['block']} blokowych/zewnętrznych, 0 pominiętych) — "
        f"{stats['occupied']} zajętych, {stats['blocked']} zablokowanych.")
    return redirect("ui:warehouse_map_detail", pk=snapshot.pk)

def _serpentine_route(codes, loc_index):
    """Order location codes into a serpentine picking route. `loc_index` maps a code to
    (aisle, stack, level). Aisles are walked in ascending order; within each aisle the
    stacks alternate direction (down one aisle, back the next) to avoid backtracking;
    within a stack, ascending level. Codes not in the index are appended at the end."""
    def num(v):
        try:
            return (0, int(str(v)))
        except (TypeError, ValueError):
            return (1, str(v))

    known = [c for c in codes if c in loc_index]
    unknown = [c for c in codes if c not in loc_index]
    aisles = sorted({loc_index[c][0] for c in known}, key=num)
    aisle_order = {a: i for i, a in enumerate(aisles)}
    ordered = []
    for a in aisles:
        in_aisle = [c for c in known if loc_index[c][0] == a]
        reverse = aisle_order[a] % 2 == 1            # snake back on odd aisles
        in_aisle.sort(key=lambda c: (num(loc_index[c][1]), num(loc_index[c][2])),
                      reverse=reverse)
        ordered.extend(in_aisle)
    return ordered, unknown


@_planner
def warehouse_picking_route(request):
    """Trasa kompletacji — wklej listę lokalizacji (po jednej w wierszu); zwraca je w
    kolejności wężowej (snake) po alejach/stosach/poziomach z najnowszej migawki, aby
    skrócić drogę kompletującego. Lokalizacje spoza migawki trafiają na koniec."""
    raw = (request.POST.get("locations") or "").strip()
    route, unknown = [], []
    if raw:
        codes, seen = [], set()
        for line in raw.replace(",", "\n").splitlines():
            c = line.strip().upper()
            if c and c not in seen:
                seen.add(c)
                codes.append(c)
        snap = WarehouseSnapshot.objects.first()
        loc_index = {}
        if snap:
            for r in snap.rows.filter(location_code__in=codes).values(
                    "location_code", "aisle", "stack", "level"):
                loc_index.setdefault(r["location_code"], (r["aisle"], r["stack"], r["level"]))
        ordered, unknown = _serpentine_route(codes, loc_index)
        route = [{"n": i + 1, "code": c, "aisle": loc_index[c][0],
                  "stack": loc_index[c][1], "level": loc_index[c][2]}
                 for i, c in enumerate(ordered)]
    return render(request, "ui/warehouse/picking_route.html", {
        "raw": raw, "route": route, "unknown": unknown})


@_planner
def warehouse_location_contents(request):
    """JSON: stock HUs/items currently stored at a location code (?code=...). Backs the
    click-a-location panel on the 3D/2D warehouse map."""
    code = (request.GET.get("code") or "").strip()
    if not code:
        return JsonResponse({"location": "", "items": [], "count": 0})
    items = (HandlingUnitItem.objects
             .filter(hu__shipment__is_stock=True, hu__location=code)
             .select_related("hu", "product").order_by("hu__seq", "id")[:200])
    data = [{
        "hu_ref": it.hu.ref, "hu_pk": it.hu.pk,
        "code": it.product.code if it.product else it.ref_code,
        "name": (it.product.name if it.product else it.description)[:80],
        "lot": it.lot, "expiry": it.expiry.isoformat() if it.expiry else "",
        "qty": it.alt_qty or it.expected_qty or 0, "unit": it.alt_unit or it.unit,
        "status": it.hu.get_status_display(),
    } for it in items]
    return JsonResponse({"location": code, "items": data, "count": len(data)})


@_planner
@_md_role
def warehouse_aisle_config(request, pk):
    """Konfiguracja szerokości korytarzy per aleja dla snapshotu (na aktywnym layoutcie).
    Prefill listą alej z kodów snapshotu; zapis w WarehouseAisleConfig(layout, aisle)."""
    snapshot = get_object_or_404(WarehouseSnapshot, pk=pk)
    active_layout = WarehouseLayout.objects.filter(is_active=True).first()
    if not active_layout:
        messages.error(request, "Brak aktywnego layoutu — konfiguracja alei wymaga aktywnego layoutu.")
        return redirect("ui:warehouse_map_detail", pk=snapshot.pk)

    # Aleje z kodów snapshotu (te same, po których render pozycjonuje regały).
    aisles = sorted({
        (r.get("aisle") or "").strip()
        for r in snapshot.rows.values("aisle") if (r.get("aisle") or "").strip()
    })
    existing = {c.aisle: c for c in active_layout.aisles.all()}

    def _num(raw, default=None):
        if raw is None or str(raw).strip() == "":
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    if request.method == "POST":
        from django.db import transaction
        with transaction.atomic():
            for aisle in aisles:
                w = _num(request.POST.get(f"width_{aisle}"))
                a = _num(request.POST.get(f"angle_{aisle}"), 0.0) or 0.0
                a = ((a + 180) % 360 + 360) % 360 - 180   # wrap do -180..180
                # Wiersz istotny gdy szerokość>0 LUB kąt≠0; inaczej usuń konfigurację.
                if (w is None or w <= 0) and a == 0:
                    existing.get(aisle) and existing[aisle].delete()
                    continue
                cfg = existing.get(aisle) or WarehouseAisleConfig(layout=active_layout, aisle=aisle)
                cfg.width_m = w if (w and w > 0) else cfg.width_m  # zachowaj domyślną gdy puste
                cfg.angle_deg = a
                cfg.notes = (request.POST.get(f"notes_{aisle}") or "")[:200]
                cfg.save()
        messages.success(request, "Konfiguracja alej zapisana.")
        return redirect("ui:warehouse_aisle_config", pk=snapshot.pk)

    rows = [{"aisle": a,
             "width_m": existing[a].width_m if a in existing else "",
             "angle_deg": existing[a].angle_deg if a in existing else 0,
             "notes": existing[a].notes if a in existing else ""} for a in aisles]
    return render(request, "ui/warehouse_map/aisle_config.html", {
        "snapshot": snapshot, "active_layout": active_layout, "rows": rows,
    })


def _hall_features_data(features, rows_list=None, hu_by_code=None):
    """Elementy hali → lista dictów do renderu (wspólny hall_feature_dict).

    Gdy podano rows_list: strefy block_zone/returns z zone_code dostają `slots` — stany
    (0-3) dopasowanych lokalizacji nietypowych (prefiks kodu) — do renderu siatki
    wewnątrz obszaru strefy (Etap 5 slice 3, bezstratność 3D)."""
    hu_by_code = hu_by_code or {}
    # Lokalizacje nietypowe (spoza wzorca regału) → (kod, stan, hu) do dopasowania po prefiksie.
    nonrack = []
    for r in (rows_list or []):
        code = (r["location_code"] or "").strip().upper()
        if not code or _RACK_RE.match(code):
            continue
        if r["blocked_pick"] or r["blocked_put"]:
            state = 2 if not r["is_empty"] else 3
        elif not r["is_empty"]:
            state = 1
        else:
            state = 0
        nonrack.append((code, state, hu_by_code.get(code, 0)))

    out = []
    for f in features:
        d = hall_feature_dict(f)          # wspólny builder (kolor/etykieta/wymiary)
        zc = (f.zone_code or "").strip().upper()
        if rows_list is not None and zc and f.kind in ("block_zone", "returns"):
            matched = [(st, hu) for code, st, hu in nonrack if code.startswith(zc)]
            d["slots"] = [st for st, _hu in matched]
            d["occupied"] = sum(1 for st, _hu in matched if st in (1, 2))
            d["hu"] = sum(hu for _st, hu in matched)
        out.append(d)
    return out


@_md_role
def warehouse_layout_features(request, pk):
    """Elementy hali (doki/bramy/strefy/liderzy) dla AKTYWNEGO layoutu mapy 3D.
    Ten sam edytor + zapis co moduł B (save_hall_features), różni się tylko FK=layout."""
    snapshot = get_object_or_404(WarehouseSnapshot, pk=pk)
    active_layout = WarehouseLayout.objects.filter(is_active=True).first()
    if not active_layout:
        messages.error(request, "Brak aktywnego layoutu — elementy hali wymagają aktywnego layoutu.")
        return redirect("ui:warehouse_map_detail", pk=snapshot.pk)

    if request.method == "POST":
        save_hall_features(request, "layout", active_layout)
        messages.success(request, "Elementy hali zapisane.")
        return redirect("ui:warehouse_layout_features", pk=snapshot.pk)

    return render(request, "ui/warehouse_model/features.html", {
        "features": active_layout.features.all(),
        "kind_choices": WarehouseHallFeature.KIND_CHOICES,
        "feature_colors": HALL_FEATURE_COLORS,
        "list_url": reverse("ui:warehouse_map"),
        "list_label": "Magazyn 3D",
        "back_url": reverse("ui:warehouse_map_detail", args=[snapshot.pk]),
        "parent_name": active_layout.name,
        "meta_line": f"Layout: {active_layout.name}",
    })

# Helpery z podkreśleniem: konsumowane przez wh3d/tests przez fasadę warehouse_map.
__all__ = [
    'warehouse_map', 'warehouse_where_is', 'warehouse_map_upload',
    'warehouse_picking_route', 'warehouse_location_contents', 'warehouse_aisle_config',
    'warehouse_layout_features', '_RACK_RE', '_hall_features_data', '_serpentine_route',
]
