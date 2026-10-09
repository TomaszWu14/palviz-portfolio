# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from ui.views.core import (
    _md_role, _parse_location_code, _planner, Count, get_object_or_404,
    HALL_FEATURE_COLORS, hall_feature_dict, hall_feature_kinds, messages,
    redirect, render, safe_json, save_hall_features, transaction,
    WarehouseHallFeature, WarehouseModel, WarehouseModelRack,
)
from django.urls import reverse

from wh3d.addressing import parse_bay_numbers
from wh3d.blender_route import rack_corners
from wh3d.ewm_service import active_master
from wh3d.model_geometry import floor_size, is_geometry_csv, parse_geometry_csv
from wh3d.models import BayTemplate, PickerActivityBatch, WarehouseSnapshot, WarehouseTaskBatch


@_planner
def warehouse_model_list(request):
    from ui.views.core import WarehouseLayout
    models_qs = WarehouseModel.objects.annotate(rack_count=Count("racks"))
    active_layout = WarehouseLayout.objects.filter(is_active=True).order_by("-uploaded_at").first()
    return render(request, "ui/warehouse_model/list.html",
                  {"models": models_qs, "active_layout": active_layout})

@_md_role
def warehouse_model_upload(request):
    from ui.forms import WarehouseModelForm
    if request.method == "POST":
        form = WarehouseModelForm(request.POST)
        f = request.FILES.get("location_file")
        if not f:
            messages.error(request, "Wybierz plik z lokalizacjami.")
            return render(request, "ui/warehouse_model/upload.html", {"form": form})
        if f.size > 5 * 1024 * 1024:
            messages.error(request, "Plik zbyt duży (max 5 MB).")
            return render(request, "ui/warehouse_model/upload.html", {"form": form})
        if form.is_valid():
            # Parsuj PRZED zapisem, żeby błąd w połowie nie zostawił osieroconego modelu.
            text = f.read().decode("utf-8-sig", errors="ignore")
            if is_geometry_csv(text):
                return _create_from_geometry(request, form, text)
            rack_map = {}  # (zone, rack) → {bays, levels}
            skipped = 0
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                # accept comma or tab separated; first column is location code
                parts = line.replace('\t', ',').split(',')
                code = parts[0].strip()
                parsed = _parse_location_code(code)
                if not parsed:
                    if code:
                        skipped += 1        # policz nierozpoznane — inaczej cicha utrata danych
                    continue
                zone, rack, bay, lvl, lvl_num = parsed
                key = (zone, rack)
                if key not in rack_map:
                    rack_map[key] = {"bays": set(), "levels": 0}
                rack_map[key]["bays"].add(bay)
                rack_map[key]["levels"] = max(rack_map[key]["levels"], lvl_num)
            # Model + regały all-or-nothing (jak warehouse_model_coords/generator).
            with transaction.atomic():
                wm = form.save()
                WarehouseModelRack.objects.bulk_create([
                    WarehouseModelRack(model=wm, zone=zone, rack_id=rack,
                                       n_bays=len(data["bays"]), n_levels=data["levels"])
                    for (zone, rack), data in sorted(rack_map.items())
                ])
            msg = f"Wczytano {len(rack_map)} regałów z pliku."
            if skipped:
                msg += f" Pominięto {skipped} nierozpoznanych linii (sprawdź format kodów)."
            messages.success(request, msg)
            return redirect("ui:warehouse_model_coords", pk=wm.pk)
    else:
        form = WarehouseModelForm()
    return render(request, "ui/warehouse_model/upload.html", {"form": form})

def _create_from_geometry(request, form, text):
    """Plik geometrii (np. z rysunku hali): regały od razu z pozycją, kątem i wymiarami.
    Błąd w którymkolwiek wierszu = nic nie zapisujemy (model nie powstaje „w połowie")."""
    racks, errors = parse_geometry_csv(text)
    if errors or not racks:
        for err in (errors or ["Plik nie zawiera żadnego regału."])[:10]:
            messages.error(request, err)
        return render(request, "ui/warehouse_model/upload.html", {"form": form})
    width, depth = floor_size(racks, rack_corners)
    with transaction.atomic():
        wm = form.save(commit=False)
        wm.floor_width_m = max(wm.floor_width_m or 0, width)
        wm.floor_depth_m = max(wm.floor_depth_m or 0, depth)
        wm.save()
        WarehouseModelRack.objects.bulk_create([WarehouseModelRack(model=wm, **r) for r in racks])
    messages.success(request, f"Wczytano {len(racks)} regałów z pozycjami — hala "
                              f"{wm.floor_width_m:g} × {wm.floor_depth_m:g} m.")
    return redirect("ui:warehouse_model_view", pk=wm.pk)


def _parse_pasted_codes(text):
    """Wklejone adresy → ({(zone, rack): {bays:set, levels:int}}, skipped).
    Toleruje kolumny po przecinku/tabie (bierze pierwszą, jak upload) i małe litery."""
    rack_map, skipped = {}, 0
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        code = line.replace("\t", ",").split(",")[0].strip().upper()
        parsed = _parse_location_code(code)
        if not parsed:
            if code:
                skipped += 1        # policz nierozpoznane — inaczej cicha utrata danych
            continue
        zone, rack, bay, lvl, lvl_num = parsed
        data = rack_map.setdefault((zone, rack), {"bays": set(), "levels": 0})
        data["bays"].add(bay)
        data["levels"] = max(data["levels"], lvl_num)
    return rack_map, skipped


@_md_role
def warehouse_model_paste(request, pk):
    """Wklej adresy jednej alejki/regału (zamiast pliku) i ustaw je w linii na planie:
    poziomo wzdłuż X (0°) albo pionowo wzdłuż Y (90°). Scalanie jest BEZSTRATNE —
    n_bays/n_levels istniejącego regału tylko rosną, nigdy nie maleją."""
    wm = get_object_or_404(WarehouseModel, pk=pk)
    ctx = {"wm": wm, "codes": "", "orientation": "horizontal",
           "start_x": 0, "start_y": 0, "keep_existing": False}
    if request.method != "POST":
        return render(request, "ui/warehouse_model/paste.html", ctx)

    codes_text = request.POST.get("codes") or ""
    orientation = "vertical" if request.POST.get("orientation") == "vertical" else "horizontal"
    keep_existing = bool(request.POST.get("keep_existing"))

    def _f(name):
        try:
            return float(request.POST.get(name) or 0)
        except (TypeError, ValueError):
            return 0.0
    start_x, start_y = _f("start_x"), _f("start_y")
    ctx.update({"codes": codes_text, "orientation": orientation, "start_x": start_x,
                "start_y": start_y, "keep_existing": keep_existing})

    rack_map, skipped = _parse_pasted_codes(codes_text)
    if not rack_map:
        messages.error(request, "Nie rozpoznano żadnego adresu — sprawdź format (np. B0-01-300A).")
        return render(request, "ui/warehouse_model/paste.html", ctx)

    from django.db import transaction
    angle = 90.0 if orientation == "vertical" else 0.0
    added = updated = repositioned = 0
    cursor = start_y if orientation == "vertical" else start_x
    with transaction.atomic():
        existing = {(r.zone, r.rack_id): r for r in wm.racks.select_for_update()}
        for (zone, rack_id), data in sorted(rack_map.items()):
            rack = existing.get((zone, rack_id))
            is_new = rack is None
            if is_new:
                rack = WarehouseModelRack(model=wm, zone=zone, rack_id=rack_id,
                                          n_bays=len(data["bays"]), n_levels=data["levels"])
                added += 1
            else:
                # Scalanie bezstratne: nigdy nie zmniejszaj już opisanego regału.
                rack.n_bays = max(rack.n_bays or 0, len(data["bays"]))
                rack.n_levels = max(rack.n_levels or 0, data["levels"])
                updated += 1
            place = is_new or not keep_existing
            if place:
                rack.angle_deg = angle
                if orientation == "vertical":
                    rack.x_m, rack.y_m = start_x, cursor
                else:
                    rack.x_m, rack.y_m = cursor, start_y
                if not is_new:
                    repositioned += 1
            rack.save()
            if place:
                cursor += rack.width_m       # kursor przesuwamy o realną szerokość regału

    msg = (f"Rozpoznano {len(rack_map)} regałów: dodano {added}, zaktualizowano {updated}"
           f"{f' (w tym przesunięto {repositioned})' if repositioned else ''}.")
    if skipped:
        msg += f" Pominięto {skipped} nierozpoznanych linii."
    messages.success(request, msg)
    limit = wm.floor_depth_m if orientation == "vertical" else wm.floor_width_m
    if limit and cursor > limit:
        messages.warning(request, f"Linia regałów ({cursor:.1f} m) wychodzi poza obrys hali "
                                  f"({limit} m) — popraw pozycje w edytorze.")
    return redirect("ui:warehouse_model_coords", pk=wm.pk)


@_md_role
def warehouse_model_coords(request, pk):
    from django.db import transaction
    wm = get_object_or_404(WarehouseModel, pk=pk)
    racks = wm.racks.order_by("zone", "rack_id")
    templates = list(BayTemplate.objects.all())
    if request.method == "POST":
        tpl_ids = {t.pk for t in templates}
        to_update, bad_numbers = [], []
        for rack in racks:
            prefix = f"rack_{rack.pk}_"
            try:
                rack.x_m = float(request.POST.get(prefix + "x_m") or 0)
                rack.y_m = float(request.POST.get(prefix + "y_m") or 0)
                rack.angle_deg = float(request.POST.get(prefix + "angle_deg") or 0)
                # max(1, ...) — jawne "0"/wartość ujemna daje zdegenerowaną geometrię (NaN/znika
                # w renderze three.js). `or default` chroni tylko puste. Jak w warehouse_rack_generator.
                rack.bay_width_cm = max(1, int(request.POST.get(prefix + "bay_width_cm") or 100))
                rack.depth_cm = max(1, int(request.POST.get(prefix + "depth_cm") or 80))
                rack.level_height_cm = max(1, int(request.POST.get(prefix + "level_height_cm") or 200))
            except (ValueError, TypeError):
                continue
            # Reguła adresu (edytor układu cz. 1) — tylko gdy formularz ją przysłał (stare
            # formularze/skrypty bez tych pól nie kasują szablonu ani numeracji).
            if prefix + "bay_numbers" in request.POST:
                tpl = request.POST.get(prefix + "template") or ""
                rack.template_id = int(tpl) if tpl.isdigit() and int(tpl) in tpl_ids else None
                rack.reverse = bool(request.POST.get(prefix + "reverse"))
                numbers = request.POST.get(prefix + "bay_numbers", "").strip()
                try:
                    parse_bay_numbers(numbers)
                    rack.bay_numbers = numbers
                except ValueError as exc:
                    bad_numbers.append(f"{rack}: {exc}")
            to_update.append(rack)
        if to_update:
            with transaction.atomic():
                WarehouseModelRack.objects.bulk_update(
                    to_update,
                    ["x_m", "y_m", "angle_deg", "bay_width_cm", "depth_cm", "level_height_cm",
                     "template", "bay_numbers", "reverse"],
                )
        messages.success(request, "Współrzędne zapisane.")
        for msg in bad_numbers[:10]:
            messages.warning(request, f"Numeracja gniazd nie zmieniona — {msg}")
        return redirect("ui:warehouse_model_view", pk=wm.pk)
    return render(request, "ui/warehouse_model/coords.html", {"wm": wm, "racks": racks, "templates": templates})

@_planner
def warehouse_model_view(request, pk):
    wm = get_object_or_404(WarehouseModel, pk=pk)
    racks = list(wm.racks.order_by("zone", "rack_id"))
    # Assign zone colors
    zones = sorted({r.zone for r in racks})
    zone_palette = ["#3b82f6","#10b981","#f59e0b","#ef4444","#8b5cf6","#f97316","#06b6d4","#ec4899","#14b8a6","#6b7280"]
    zone_color = {z: zone_palette[i % len(zone_palette)] for i, z in enumerate(zones)}
    racks_data = [
        {
            "id": r.pk, "zone": r.zone, "rack_id": r.rack_id,
            "x": r.x_m or 0, "y": r.y_m or 0,
            "width": r.width_m, "depth": r.depth_cm / 100,
            "angle": r.angle_deg,
            "n_levels": r.n_levels, "n_bays": r.n_bays,
            "level_h": r.level_height_cm / 100,
            "color": zone_color.get(r.zone, "#3b82f6"),
            # Wypełnienie regału [%] dla wskaźnika 3D (kolor + %). None → „brak danych"
            # (szary). Realne źródło (stan magazynu) podpinane w osobnym kroku — na razie
            # świadomie None, żeby nie pokazywać zmyślonej liczby jako faktu.
            "fill_pct": None,
        }
        for r in racks
    ]
    features_data = _features_data(wm)
    # Legenda typów elementów obecnych w modelu (unikalne kind, w kolejności definicji).
    _kinds = hall_feature_kinds()
    present_kinds = {f["kind"] for f in features_data}
    feature_legend = [
        {"label": _kinds[k], "color": HALL_FEATURE_COLORS[k]}
        for k in _kinds if k in present_kinds
    ]
    return render(request, "ui/warehouse_model/view.html", {
        "wm": wm,
        "racks": racks,                       # nagłówek „N regałów" (dotąd zawsze 0)
        # safe_json (nie json.dumps) — escapuje </>&, żeby wolny tekst z pola
        # (zone/rack_id/label regału) nie mógł wyjść z <script> (stored XSS).
        "racks_json": safe_json(racks_data),
        "features_json": safe_json(features_data),
        "feature_legend": feature_legend,
        "zone_color": zone_color,
        "zones": zones,
        "zone_legend": [{"zone": z, "color": zone_color[z]} for z in zones],
        "floor_w": wm.floor_width_m,
        "floor_d": wm.floor_depth_m,
        "has_master": active_master() is not None,
        # Odtwarzacz przepływów (_flow_player.html): źródła do wyboru — importy kompletacji
        # z SAP (realna kolejność pobrań) i snapshoty zajętości lokalizacji.
        "flow_batches": PickerActivityBatch.objects.order_by("-uploaded_at")[:12],
        "flow_snapshots": WarehouseSnapshot.objects.order_by("-uploaded_at")[:6],
        # Ruchy wózków: importy zadań EWM (?wt=<pk> z raportu importu = wybrany od razu).
        "flow_wt_batches": WarehouseTaskBatch.objects.filter(status="done", first_confirmed__isnull=False)[:12],
        "flow_wt_selected": request.GET.get("wt", ""),
    })

@_md_role
def warehouse_model_features(request, pk):
    """Edytowalna tabela elementów hali (dodawanie/edycja/usuwanie wierszy).
    Zapis/render przez współdzielone helpery (save_hall_features / hall_feature_dict)."""
    wm = get_object_or_404(WarehouseModel, pk=pk)
    if request.method == "POST":
        save_hall_features(request, "model", wm)
        messages.success(request, "Elementy hali zapisane.")
        return redirect("ui:warehouse_model_features", pk=wm.pk)

    return render(request, "ui/warehouse_model/features.html", {
        "wm": wm, "features": wm.features.all(),
        "kind_choices": WarehouseHallFeature.KIND_CHOICES,
        "feature_colors": HALL_FEATURE_COLORS,
        # Chrome parametryzowany (ten sam szablon dla modułu A — mapa 3D).
        "list_url": reverse("ui:warehouse_model_list"),
        "list_label": "Modele magazynu",
        "back_url": reverse("ui:warehouse_model_view", args=[wm.pk]),
        "parent_name": wm.name,
        "meta_line": f"hala {wm.floor_width_m}×{wm.floor_depth_m} m",
    })


def _features_data(wm):
    """Elementy hali → lista dictów do renderu (współdzielony hall_feature_dict)."""
    return [hall_feature_dict(f) for f in wm.features.all()]


@_md_role
def warehouse_model_delete(request, pk):
    wm = get_object_or_404(WarehouseModel, pk=pk)
    if request.method == "POST":
        wm.delete()
        messages.success(request, "Model magazynu usunięty.")
        return redirect("ui:warehouse_model_list")
    return render(request, "ui/warehouse_model/confirm_delete.html", {"object": wm})

__all__ = [
    'warehouse_model_list',
    'warehouse_model_upload',
    'warehouse_model_paste',
    'warehouse_model_features',
    'warehouse_model_coords',
    'warehouse_model_view',
    'warehouse_model_delete',
]
