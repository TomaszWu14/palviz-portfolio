# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from .core import (
    _planner, Carton, Q, Paginator, render, _md_role, get_object_or_404, CartonForm,
    json, messages, redirect, _location_dim_groups, require_POST, JsonResponse,
    CartonArtwork, PalletizationInstruction, _read_xlsx_as_dicts, _xlsx_int,
    _xlsx_float, _after_import_redirect, _make_xlsx_response, _style_xlsx_header,
    _add_example_rows, _finalize_xlsx, HttpResponse, CartonVariant, Dimensions,
    _fig_carton_3d, _fig_json
)


@_planner
def planner_cartons(request):
    q = request.GET.get("q", "").strip()
    qs = Carton.objects.select_related("inner_pack").order_by("name")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(ean__icontains=q))
    page_obj = Paginator(qs, 25).get_page(request.GET.get("page", 1))
    return render(request, "ui/planner/cartons.html",
                  {"page_obj": page_obj, "query": q, "active_tab": "cartons"})

@_md_role
def planner_carton_form(request, pk=None):
    obj = get_object_or_404(Carton, pk=pk) if pk else None
    if request.method == "POST":
        form = CartonForm(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            carton = form.save(commit=False)
            # Persist the live-designed pallet layout (JSON from the hidden field).
            raw_layout = request.POST.get("layout_design", "").strip()
            if raw_layout:
                try:
                    parsed = json.loads(raw_layout)
                    carton.layout_design = parsed if isinstance(parsed, dict) else None
                except (ValueError, TypeError):
                    carton.layout_design = carton.layout_design  # keep previous on bad JSON
            elif "layout_design" in request.POST:
                carton.layout_design = None  # explicit clear
            carton.save()
            form.save_m2m()
            messages.success(request, f"Karton {'zaktualizowany' if pk else 'dodany'} pomyślnie.")
            return redirect("ui:planner_cartons")
    else:
        form = CartonForm(instance=obj)
    return render(request, "ui/planner/carton_form.html", {
        "form": form,
        "obj": obj,
        "dim_groups": _location_dim_groups(),
        "saved_layout": obj.layout_design if (obj and obj.layout_design) else None,
    })

@require_POST
@_md_role
def planner_carton_delete(request, pk: int):
    get_object_or_404(Carton, pk=pk).delete()
    messages.success(request, "Karton usunięty.")
    return redirect("ui:planner_cartons")

@_md_role
def carton_artwork_list(request, pk: int):
    carton = get_object_or_404(Carton, pk=pk)
    arts = [a.as_dict() for a in carton.artworks.all()]
    return JsonResponse({"ok": True, "artworks": arts})

@require_POST
@_md_role
def carton_artwork_upload(request, pk: int):
    # Ta sama ścieżka co sztuka/OPZ (walidacja, podmiana nadruku, derywaty) — jedno
    # miejsce, żeby poziomy hierarchii nie rozjechały się przy kolejnej zmianie.
    from .artwork import _art_upload
    return _art_upload(request, get_object_or_404(Carton, pk=pk), CartonArtwork, "carton")

@require_POST
@_md_role
def carton_artwork_update(request, art_id: int):
    from .artwork import _art_update
    return _art_update(request, get_object_or_404(CartonArtwork, pk=art_id))

@require_POST
@_md_role
def carton_artwork_delete(request, art_id: int):
    from .artwork import _art_delete
    return _art_delete(get_object_or_404(CartonArtwork, pk=art_id))

@_md_role
def planner_carton_unify(request):
    """Group cartons by rounded dimensions and allow merging duplicates."""
    from django.db import transaction
    from django.db.models import Count

    def _round_to_tol(value, tol):
        return int(round(value / tol) * tol)

    try:
        tol = max(1, int(request.GET.get("tol", 1) or 1))
    except (ValueError, TypeError):
        tol = 1

    all_cartons = list(Carton.objects.all().order_by("pk"))

    # Annotate each carton with instruction count
    instr_counts = {
        row["carton_id"]: row["cnt"]
        for row in PalletizationInstruction.objects.order_by().values("carton_id").annotate(cnt=Count("id"))
        if row["carton_id"] is not None
    }

    # Group by rounded dims
    from collections import defaultdict
    groups_map = defaultdict(list)
    for c in all_cartons:
        rl = _round_to_tol(c.length_cm, tol)
        rw = _round_to_tol(c.width_cm, tol)
        rh = _round_to_tol(c.height_cm, tol)
        key = f"{rl}x{rw}x{rh}"
        groups_map[key].append(c)

    if request.method == "POST":
        merged_cartons = 0
        merged_instrs = 0

        with transaction.atomic():     # whole merge is all-or-nothing
            for key, members in groups_map.items():
                if len(members) < 2:
                    continue
                canon_pk_str = request.POST.get(f"canonical_{key}", "")
                try:
                    canon_pk = int(canon_pk_str)
                except (ValueError, TypeError):
                    continue

                canon = next((c for c in members if c.pk == canon_pk), None)
                if canon is None:
                    continue

                non_canonical = [c for c in members if c.pk != canon_pk]
                for old_carton in non_canonical:
                    reassigned = PalletizationInstruction.objects.filter(carton=old_carton)
                    count = reassigned.count()
                    reassigned.update(
                        carton=canon,
                        carton_l=canon.length_cm,
                        carton_w=canon.width_cm,
                        carton_h=canon.height_cm,
                        unit_weight=canon.unit_weight_kg,
                        pcs_per_carton=canon.pieces_per_carton,
                        carton_tare=canon.tare_kg,
                    )
                    merged_instrs += count
                    old_carton.delete()
                    merged_cartons += 1

        messages.success(
            request,
            f"Scalono {merged_cartons} kartonów, przeniesiono {merged_instrs} instrukcji paletyzacji."
        )
        return redirect("ui:planner_carton_unify")

    # GET — build groups context
    groups = []
    singleton_count = 0
    for key, members in sorted(groups_map.items()):
        if len(members) < 2:
            singleton_count += 1
            continue
        # Default canonical: most instructions, or largest pk if tied
        def _sort_key(c):
            return (instr_counts.get(c.pk, 0), c.pk)
        best = max(members, key=_sort_key)
        cartons_data = []
        for c in sorted(members, key=lambda c: c.pk):
            cartons_data.append({
                "obj": c,
                "instr_count": instr_counts.get(c.pk, 0),
                "selected": (c.pk == best.pk),
            })
        groups.append({
            "key": key,
            "cartons": cartons_data,
        })

    return render(request, "ui/planner/carton_unify.html", {
        "groups": groups,
        "tol": tol,
        "singleton_count": singleton_count,
        "active_tab": "cartons",
    })

@_md_role
def planner_carton_import(request):
    """Import cartons from CSV file."""
    if request.method == "POST":
        f = request.FILES.get("file")
        if not f:
            messages.error(request, "Nie wybrano pliku.")
            return redirect("ui:planner_cartons")
        if f.size > 5 * 1024 * 1024:
            messages.error(request, "Plik zbyt duży (max 5 MB).")
            return redirect("ui:planner_cartons")
        try:
            import csv as csv_mod
            decoded = f.read().decode("utf-8-sig")
            reader = csv_mod.DictReader(decoded.splitlines())
            created = updated = errors = 0
            for i, row in enumerate(reader, 1):
                try:
                    name = row.get("name", "").strip()
                    if not name:
                        errors += 1
                        continue
                    defaults = {
                        "ean":              row.get("ean", "").strip(),
                        "length_cm":        int(float(str(row.get("length_cm") or row.get("l", 0)).replace(",", "."))) or 1,
                        "width_cm":         int(float(str(row.get("width_cm")  or row.get("w", 0)).replace(",", "."))) or 1,
                        "height_cm":        int(float(str(row.get("height_cm") or row.get("h", 0)).replace(",", "."))) or 1,
                        "unit_weight_kg":   float(str(row.get("unit_weight_kg") or row.get("unit_weight", 0)).replace(",", ".")),
                        "pieces_per_carton":int(float(str(row.get("pieces_per_carton") or row.get("pcs", 1)).replace(",", "."))) or 1,
                        "tare_kg":          float(str(row.get("tare_kg") or row.get("tare", 0)).replace(",", ".")),
                        "notes":            row.get("notes", "").strip(),
                        "is_active":        str(row.get("is_active", "1")).strip() not in ("0", "false", "False", "nie"),
                    }
                    obj, was_created = Carton.objects.update_or_create(name=name, defaults=defaults)
                    if was_created:
                        created += 1
                    else:
                        updated += 1
                except Exception:
                    errors += 1
            messages.success(request, f"Import zakończony: {created} nowych, {updated} zaktualizowanych, {errors} błędów.")
        except Exception as e:
            messages.error(request, f"Błąd wczytywania pliku: {e}")
    return redirect("ui:planner_cartons")

@_md_role
def excel_import_cartons(request):
    """Import cartons from xlsx template."""
    if request.method != "POST":
        return redirect("ui:planner_excel_templates")

    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Nie wybrano pliku.")
        return redirect("ui:planner_excel_templates")
    if f.size > 5 * 1024 * 1024:
        messages.error(request, "Plik zbyt duży (max 5 MB).")
        return redirect("ui:planner_excel_templates")

    try:
        rows = _read_xlsx_as_dicts(f)
        created = updated = errors = 0
        for row in rows:
            name = row.get("name", "").strip()
            if not name:
                errors += 1
                continue
            try:
                L, W, H = (_xlsx_int(row.get("length_cm")), _xlsx_int(row.get("width_cm")),
                           _xlsx_int(row.get("height_cm")))
                # Puste/zero wymiary → NIE koduj cicho 1×1×1 (kalkulator liczyłby śmieci).
                if L <= 0 or W <= 0 or H <= 0:
                    errors += 1
                    continue
                defaults = {
                    "ean":              row.get("ean", "").strip(),
                    "length_cm":        L,
                    "width_cm":         W,
                    "height_cm":        H,
                    "unit_weight_kg":   _xlsx_float(row.get("unit_weight_kg")),
                    "pieces_per_carton":_xlsx_int(row.get("pieces_per_carton"), 1),
                    "tare_kg":          _xlsx_float(row.get("tare_kg")),
                    "notes":            row.get("notes", "").strip(),
                    "is_active":        row.get("is_active", "1").strip() not in ("0", "false", "False", "nie"),
                }
                _, was_new = Carton.objects.update_or_create(name=name, defaults=defaults)
                if was_new:
                    created += 1
                else:
                    updated += 1
            except Exception:
                errors += 1

        messages.success(request, f"Import kartonów: {created} nowych, {updated} zaktualizowanych, {errors} błędów.")
        from ..models import ImportRun
        ImportRun.record("cartons", rows=created + updated,
                         label=getattr(f, "name", ""), user=request.user,
                         error=f"{errors} błędów w wierszach" if errors else "")
    except Exception as e:
        messages.error(request, f"Błąd wczytywania pliku: {e}")

    return _after_import_redirect(request, "ui:planner_cartons")

@_planner
def excel_template_cartons(request):
    wb, ws, response = _make_xlsx_response("PalViz_kartony_wzor.xlsx")
    ws.title = "Kartony"
    cols = [
        ("name",             "Unikalna nazwa kartonu",               25, "Karton A 40×30×25"),
        ("ean",              "Kod EAN kartonu (opcjonalnie)",         16, "5901234000001"),
        ("length_cm",        "Długość (L) [cm]",                     11, 40),
        ("width_cm",         "Szerokość (W) [cm]",                   11, 30),
        ("height_cm",        "Wysokość (H) [cm]",                    11, 25),
        ("unit_weight_kg",   "Waga netto 1 sztuki produktu [kg]",    16, 0.45),
        ("pieces_per_carton","Sztuk produktu w kartonie",             16, 24),
        ("tare_kg",          "Tara (waga samego kartonu) [kg]",      16, 0.2),
        ("notes",            "Uwagi (opcjonalnie)",                  20, "Standardowy"),
        ("is_active",        "Czy aktywny? 1=tak, 0=nie",            12, 1),
    ]
    _style_xlsx_header(ws, cols, "059669")
    rows = [
        ["Karton mały 40×30×25","5901234123457",40,30,25,0.45,24,0.2,"",1],
        ["Karton średni 50×35×30","",50,35,30,0.60,18,0.25,"",1],
        ["Karton duży 60×40×40","5901234123458",60,40,40,1.20,6,0.4,"EU pallet",1],
        ["Karton pół-palet 60×40×20","",60,40,20,0.55,24,0.3,"Lokalizacje półkowe",1],
    ]
    _add_example_rows(ws, cols, rows)
    return _finalize_xlsx(wb, ws, response)

@_planner
def planner_carton_csv_template(request):
    """Download sample CSV template for cartons."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="kartony_wzor.csv"'
    response.write("﻿")  # UTF-8 BOM for Excel
    import csv as csv_mod
    w = csv_mod.writer(response)
    w.writerow(["name", "ean", "length_cm", "width_cm", "height_cm",
                "unit_weight_kg", "pieces_per_carton", "tare_kg", "notes", "is_active"])
    w.writerow(["Karton mały 40x30x25", "5901234123457", 40, 30, 25, 0.45, 24, 0.2, "Standardowy", 1])
    w.writerow(["Karton duży 60x40x35", "",              60, 40, 35, 0.80, 12, 0.3, "",            1])
    w.writerow(["Karton EU 60x40x40",   "5901234123458", 60, 40, 40, 1.20,  6, 0.4, "EU pallet",   1])
    return response

@_planner
def carton_api(request, pk: int):
    """Return carton JSON for JS autofill."""
    c = get_object_or_404(Carton, pk=pk)
    return JsonResponse({
        "length_cm": c.length_cm, "width_cm": c.width_cm, "height_cm": c.height_cm,
        "unit_weight_kg": c.unit_weight_kg, "pieces_per_carton": c.pieces_per_carton,
        "tare_kg": c.tare_kg,
    })

@_planner
def carton_preview_json(request):
    """Return Plotly JSON for a carton preview given L/W/H dims."""
    try:
        l_raw = int(float(request.GET.get("l", 0)))
        w_raw = int(float(request.GET.get("w", 0)))
        h_raw = int(float(request.GET.get("h", 0)))
        if l_raw <= 0 or w_raw <= 0 or h_raw <= 0:
            return JsonResponse({"ok": False})
        l_cm = l_raw
        w_cm = w_raw
        h_cm = h_raw
        pcs  = max(1, int(float(request.GET.get("pcs", 1) or 1)))
        carton = CartonVariant(
            sku="PREVIEW", variant="",
            dims=Dimensions(l_cm=l_cm, w_cm=w_cm, h_cm=h_cm),
            unit_weight_kg=0.0, pieces_per_carton=pcs,
            demand_pieces=0, carton_tare_kg=0.0, allow_rotation=False,
        )
        fig = _fig_carton_3d(carton)
        return JsonResponse({"ok": True, "fig": _fig_json(fig)})
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)})

__all__ = [
    'planner_cartons',
    'planner_carton_form',
    'planner_carton_delete',
    'carton_artwork_list',
    'carton_artwork_upload',
    'carton_artwork_update',
    'carton_artwork_delete',
    'planner_carton_unify',
    'planner_carton_import',
    'excel_import_cartons',
    'excel_template_cartons',
    'planner_carton_csv_template',
    'carton_api',
    'carton_preview_json',
]
