# Warianty projektu magazynu: import z Blendera (palviz.design-variant), wariant z modelu
# obecnego magazynu, porównanie wskaźników obok siebie, pobranie do dalszej edycji.
import json

from django.urls import reverse

from ui.views.core import (
    _md_role, _planner, get_object_or_404, hall_feature_dict, JsonResponse, messages,
    redirect, render, require_POST, WarehouseModel,
)
from wh3d.blender_scene import model_floor, model_racks
from wh3d.design_kpi import clean_elements, compute_kpi, rack_to_element
from wh3d.models import WarehouseDesignVariant

MAX_UPLOAD = 5 * 1024 * 1024
MAX_COMPARE = 6

# (klucz, etykieta, jednostka, lepiej = "max" | "min" | None)
KPI_ROWS = [
    ("pallet_positions", "Miejsca paletowe", "", "max"),
    ("positions_per_m2", "Miejsca paletowe na m² hali", "", "max"),
    ("built_area_m2", "Powierzchnia zabudowy", "m²", None),
    ("floor_area_m2", "Powierzchnia hali", "m²", None),
    ("travel.avg_m", "Średnia droga do miejsca paletowego", "m", "min"),
    ("travel.a_zone_avg_m", "Średnia droga do strefy A (20 % najbliższych miejsc)", "m", "min"),
    ("travel.max_m", "Najdłuższa droga", "m", "min"),
    ("aisle_issue_count", "Naruszenia alejek (za wąskie / kolizje)", "", "min"),
    ("travel.anchors", "Punkty obsługi użyte do drogi (doki, bramy, stanowiska; 0 = przód hali)", "", None),
]


def _get(kpi, dotted):
    for part in dotted.split("."):
        kpi = (kpi or {}).get(part) if isinstance(kpi, dict) else None
    return kpi


def _comparison(variants):
    """Wiersze tabeli: wartości per wariant + oznaczenie najlepszej + różnica do pierwszego."""
    rows = []
    for key, label, unit, better in KPI_ROWS:
        vals = [_get(v.kpi, key) for v in variants]
        nums = [x for x in vals if isinstance(x, (int, float))]
        best = (max(nums) if better == "max" else min(nums)) if (better and len(nums) > 1) else None
        base = vals[0] if vals and isinstance(vals[0], (int, float)) else None
        cells = []
        for i, x in enumerate(vals):
            delta = None
            if i and base not in (None, 0) and isinstance(x, (int, float)):
                delta = round((x - base) / base * 100)
            good = None if (delta in (None, 0) or not better) else ((delta > 0) == (better == "max"))
            cells.append({"value": x, "best": best is not None and x == best, "delta": delta, "good": good})
        rows.append({"label": label, "unit": unit, "cells": cells})
    kinds = {}
    for v in variants:                                   # sprzęt: liczba szt. per rodzaj
        for kind, k in (v.kpi.get("by_kind") or {}).items():
            kinds.setdefault(kind, k["label"])
    for kind, label in kinds.items():
        cells = []
        for v in variants:
            k = (v.kpi.get("by_kind") or {}).get(kind)
            cap = (v.kpi.get("equipment") or {}).get(kind)
            cells.append({"value": k["count"] if k else 0, "best": False, "delta": None,
                          "extra": (f"{cap['throughput_h']}/h" if cap else
                                    (f"{k['pallet_positions']} miejsc" if k and k["pallet_positions"] else ""))})
        rows.append({"label": label, "unit": "szt.", "cells": cells, "equipment": True})
    return rows


def _save_variant(name, source, elements, features, floor, base_model=None, notes=""):
    kpi = compute_kpi(elements, features, floor["width"], floor["depth"])
    return WarehouseDesignVariant.objects.create(
        name=name[:200], source=source, base_model=base_model, floor_width_m=floor["width"],
        floor_depth_m=floor["depth"], elements=elements, features=features, kpi=kpi, notes=notes)


@_planner
def warehouse_variants(request):
    variants = list(WarehouseDesignVariant.objects.all())
    ids = [int(x) for x in request.GET.getlist("ids") if x.isdigit()]
    by_pk = {v.pk: v for v in variants}
    # kolejność kolumn = kolejność ids (pierwsza kolumna to punkt odniesienia)
    chosen = [by_pk[i] for i in dict.fromkeys(ids) if i in by_pk] if ids else variants[:4][::-1]
    chosen = chosen[:MAX_COMPARE]
    return render(request, "ui/warehouse_variants/list.html", {
        "variants": variants, "chosen": chosen, "chosen_ids": {v.pk for v in chosen},
        "rows": _comparison(chosen), "models": WarehouseModel.objects.all()[:50],
    })


@require_POST
@_md_role
def warehouse_variant_import(request):
    f = request.FILES.get("variant_file")
    if not f or f.size > MAX_UPLOAD:
        messages.error(request, "Wybierz plik wariantu JSON z Blendera (max 5 MB).")
        return redirect("ui:warehouse_variants")
    try:
        data = json.loads(f.read().decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        messages.error(request, "Plik nie jest poprawnym JSON-em.")
        return redirect("ui:warehouse_variants")
    if not isinstance(data, dict) or data.get("format") != "palviz.design-variant":
        messages.error(request, "To nie jest plik wariantu (palviz.design-variant) z zestawu projektowego.")
        return redirect("ui:warehouse_variants")
    elements, errors = clean_elements(data.get("elements"))
    if not elements:
        messages.error(request, "Wariant nie zawiera poprawnych elementów. " + "; ".join(errors[:5]))
        return redirect("ui:warehouse_variants")
    try:
        floor = {"width": float(data["floor"]["width"]), "depth": float(data["floor"]["depth"])}
    except (KeyError, TypeError, ValueError):
        floor = {"width": 50.0, "depth": 30.0}
    features = [x for x in (data.get("features") or []) if isinstance(x, dict)]
    base_id = (data.get("base_model") or {}).get("id") if isinstance(data.get("base_model"), dict) else None
    base = WarehouseModel.objects.filter(pk=base_id).first() if isinstance(base_id, int) else None
    name = (request.POST.get("name") or data.get("name") or f.name).strip()
    v = _save_variant(name, "blender", elements, features, floor, base_model=base)
    messages.success(request, f"Zaimportowano wariant „{v.name}”: {len(elements)} elementów, "
                              f"{v.kpi['pallet_positions']} miejsc paletowych.")
    if errors:
        messages.warning(request, f"Pominięto {len(errors)} błędnych elementów: " + "; ".join(errors[:5]))
    others = WarehouseDesignVariant.objects.exclude(pk=v.pk).values_list("pk", flat=True)[:3]
    ids = [*reversed(list(others)), v.pk]                # nowy wariant w ostatniej kolumnie
    return redirect(reverse("ui:warehouse_variants") + "?" + "&".join(f"ids={i}" for i in ids))


@require_POST
@_md_role
def warehouse_variant_from_model(request):
    wm = get_object_or_404(WarehouseModel, pk=request.POST.get("model_id") or 0)
    racks = model_racks(wm)
    if not racks:
        messages.error(request, f"Model „{wm.name}” nie ma regałów.")
        return redirect("ui:warehouse_variants")
    elements = [rack_to_element(r) for r in racks]
    features = [hall_feature_dict(f) for f in wm.features.all()]
    v = _save_variant(f"Obecny: {wm.name}", "model", elements, features, model_floor(wm, racks),
                      base_model=wm, notes="Wariant bazowy z modelu magazynu (regały → rack_std).")
    messages.success(request, f"Dodano wariant bazowy „{v.name}”: {v.kpi['pallet_positions']} miejsc paletowych.")
    return redirect("ui:warehouse_variants")


@_planner
def warehouse_variant_json(request, pk):
    """Wariant jako palviz.design-variant — do dalszej edycji w Blenderze (kit.load_variant)."""
    v = get_object_or_404(WarehouseDesignVariant, pk=pk)
    data = {"format": "palviz.design-variant", "version": 1, "name": v.name,
            "base_model": {"id": v.base_model_id} if v.base_model_id else {},
            "floor": {"width": v.floor_width_m, "depth": v.floor_depth_m},
            "features": v.features, "elements": v.elements, "summary": v.kpi}
    resp = JsonResponse(data, json_dumps_params={"ensure_ascii": False})
    resp["Content-Disposition"] = f'attachment; filename="palviz_wariant_{v.pk}.json"'
    return resp


@require_POST
@_md_role
def warehouse_variant_delete(request, pk):
    v = get_object_or_404(WarehouseDesignVariant, pk=pk)
    name = v.name
    v.delete()
    messages.success(request, f"Usunięto wariant „{name}”.")
    return redirect("ui:warehouse_variants")
