# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from .core import (
    neutralize_workbook,
    login_required, get_object_or_404, Product, render, PalletizationInstruction,
    CartonVariant, Dimensions, _fig_json, safe_json, _fig_pallet_3d, _fig_layer_2d, _planner, Q,
    Paginator, _md_role, InstructionForm, _recalculate_instruction, messages, redirect,
    has_role, GROUP_ADMIN, GROUP_MASTER_DATA, _fig_carton_3d, mark_safe,
    _svg_thumbnail, require_POST, _as_int, HttpResponse, _compute_cog,
    _optimization_hints, WarehouseLocationType, _render_panel, render_to_string,
    openpyxl, io, _re
)


@login_required(login_url="/login/")
def warehouse_instruction(request, code: str, version: int = None):
    product = get_object_or_404(Product, code=code, is_active=True)
    instructions = product.active_instructions()
    if not instructions.exists():
        return render(request, "ui/warehouse/no_instruction.html", {"product": product})

    if version:
        instr = get_object_or_404(PalletizationInstruction, product=product, version=version, is_active=True)
    else:
        instr = instructions.order_by("-version").first()

    layout = instr.get_selected_layout()
    meta = instr.get_meta()

    all_layouts = [(l["name"], l) for l in (instr.layouts or [])]
    ctx = {
        "product": product,
        "instr": instr,
        "all_versions": list(instructions.order_by("version")),
        "layout": layout,
        "all_layouts": all_layouts,
        "meta": meta,
        "placements_json": safe_json(layout["placements"] if layout else []),
        "carton_dims_json": safe_json({"h": instr.carton_h, "l": instr.carton_l, "w": instr.carton_w}),
    }
    if layout:
        carton_var = CartonVariant(
            sku=product.code, variant=f"v{instr.version}",
            dims=Dimensions(l_cm=instr.carton_l, w_cm=instr.carton_w, h_cm=instr.carton_h),
            unit_weight_kg=instr.unit_weight, pieces_per_carton=instr.pcs_per_carton,
            demand_pieces=instr.demand_pcs, carton_tare_kg=instr.carton_tare,
            allow_rotation=True,
        ).validate()
        ctx["fig_pallet_json"] = _fig_json(_fig_pallet_3d(meta, carton_var, layout, layout.get("layers_used", 3), layout["total_height_used_cm"], label=f"{product.code} {product.name}"[:18]))
        ctx["fig_layer_json"] = _fig_json(_fig_layer_2d(meta, layout, product.code, 1))
    return render(request, "ui/warehouse/instruction.html", ctx)

@_planner
def planner_instructions(request):
    q = request.GET.get("q", "").strip()
    qs = PalletizationInstruction.objects.select_related("product", "carton").order_by("product__code", "version")
    if q:
        qs = qs.filter(Q(product__code__icontains=q) | Q(product__name__icontains=q) | Q(name__icontains=q))
    page = Paginator(qs, 25).get_page(request.GET.get("page", 1))
    return render(request, "ui/planner/instructions.html",
                  {"page_obj": page, "query": q, "active_tab": "instructions"})

@_md_role
def planner_instruction_form(request, pk=None):
    obj = get_object_or_404(PalletizationInstruction, pk=pk) if pk else None
    if request.method == "POST":
        form = InstructionForm(request.POST, instance=obj)
        if form.is_valid():
            instr = form.save(commit=False)
            # Auto-fill from carton DB if selected
            carton_db = form.cleaned_data.get("carton_from_db")
            if carton_db:
                instr.carton = carton_db
                instr.carton_l = carton_db.length_cm
                instr.carton_w = carton_db.width_cm
                instr.carton_h = carton_db.height_cm
                instr.unit_weight = carton_db.unit_weight_kg
                instr.pcs_per_carton = carton_db.pieces_per_carton
                instr.carton_tare = carton_db.tare_kg

            # When editing, let the user keep the current version (overwrite) or
            # branch off a new one (v+1) leaving the original untouched.
            new_version = bool(obj) and request.POST.get("save_mode") == "new_version"
            if new_version:
                from django.db import transaction as _tx
                from django.db.models import Max as _Max
                with _tx.atomic():
                    product_locked = Product.objects.select_for_update().get(pk=instr.product_id)
                    last_v = product_locked.instructions.aggregate(m=_Max("version"))["m"] or 0
                    instr.pk = None
                    instr._state.adding = True
                    instr.version = last_v + 1
                    instr.layouts = []          # force a fresh recompute
                    instr.selected_layout = ""
                    instr.save()
            else:
                instr.save()
            try:
                _recalculate_instruction(instr)
                if new_version:
                    messages.success(request, f"Utworzono nową wersję v{instr.version} i przeliczono.")
                else:
                    messages.success(request, f"Instrukcja {'zaktualizowana' if pk else 'utworzona'} i przeliczona pomyślnie.")
            except Exception as e:
                messages.warning(request, f"Zapisano, ale błąd obliczeń: {e}")
            return redirect("ui:planner_instruction_detail", pk=instr.pk)
    else:
        form = InstructionForm(instance=obj)
    return render(request, "ui/planner/instruction_form.html", {"form": form, "obj": obj})

@_planner
def planner_instruction_detail(request, pk: int):
    instr = get_object_or_404(PalletizationInstruction, pk=pk)
    meta = instr.get_meta()

    # Read open to any planner; mutations require master-data role.
    if request.method == "POST" and not has_role(request.user, GROUP_ADMIN, GROUP_MASTER_DATA):
        return render(request, "ui/403.html", status=403)

    if request.method == "POST" and "recalculate" in request.POST:
        try:
            _recalculate_instruction(instr)
            messages.success(request, "Przeliczono pomyślnie.")
        except Exception as e:
            messages.error(request, f"Błąd obliczeń: {e}")
        return redirect("ui:planner_instruction_detail", pk=pk)

    if request.method == "POST" and "select_layout" in request.POST:
        selected = request.POST.get("selected_layout", "")
        if any(l["name"] == selected for l in (instr.layouts or [])):
            instr.selected_layout = selected
            instr.save(update_fields=["selected_layout"])
        return redirect("ui:planner_instruction_detail", pk=pk)

    layout = instr.get_selected_layout()

    carton_rows = [
        ("Wymiary (L×W×H)", f"{instr.carton_l}×{instr.carton_w}×{instr.carton_h} cm"),
        ("Waga jedn.", f"{instr.unit_weight} kg"),
        ("Szt / karton", str(instr.pcs_per_carton)),
        ("Tara kartonu", f"{instr.carton_tare} kg"),
        ("Waga kartonu", f"{round(instr.unit_weight * instr.pcs_per_carton + instr.carton_tare, 3)} kg"),
        ("Popyt", f"{instr.demand_pcs} szt"),
    ]

    fig_pallet_json = fig_layer_json = fig_carton_json = None
    layer_data = []
    all_layouts_data = []
    carton_var = None

    if layout:
        carton_var = CartonVariant(
            sku=instr.product.code, variant=f"v{instr.version}",
            dims=Dimensions(l_cm=instr.carton_l, w_cm=instr.carton_w, h_cm=instr.carton_h),
            unit_weight_kg=instr.unit_weight, pieces_per_carton=instr.pcs_per_carton,
            demand_pieces=instr.demand_pcs, carton_tare_kg=instr.carton_tare,
            allow_rotation=True,
        ).validate()
        fig_pallet_json = _fig_json(_fig_pallet_3d(meta, carton_var, layout, layout.get("layers_used", 3), layout["total_height_used_cm"], label=f"{instr.product.code} {instr.product.name}"[:18]))
        fig_layer_json = _fig_json(_fig_layer_2d(meta, layout, instr.product.code, 1))
        fig_carton_json = _fig_json(_fig_carton_3d(carton_var))

        # Per-layer breakdown
        base_h = meta["base_height_cm"]
        h_cm = instr.carton_h
        placements = layout.get("placements", [])
        layers_used = layout.get("layers_used", 1)
        cpl = layout.get("cartons_per_layer", 0)
        for i in range(1, layers_used + 1):
            lp = [p for p in placements if p.get("layer", i) == i]
            count = len(lp) if lp else cpl
            layer_data.append({
                "num": i,
                "count": count,
                "z_start": base_h + (i-1) * h_cm,
                "z_end": base_h + i * h_cm,
                "thumb": mark_safe(_svg_thumbnail(meta, layout, w=100, h=70)),
            })

    # All layouts comparison
    for l in (instr.layouts or [])[:8]:
        all_layouts_data.append({
            "name": l["name"],
            "cartons_per_pallet": l["cartons_per_pallet"],
            "layers_used": l["layers_used"],
            "total_weight_kg": l.get("weight_per_pallet_kg", ""),
            "efficiency_pct": l.get("utilization_percent", ""),
            "cartons_per_layer": l.get("cartons_per_layer", ""),
            "is_selected": l["name"] == instr.selected_layout,
            "thumb": mark_safe(_svg_thumbnail(meta, l, w=110, h=80)),
        })

    return render(request, "ui/planner/instruction_detail.html", {
        "instr": instr, "meta": meta, "layout": layout,
        "carton_var": carton_var,
        "carton_rows": carton_rows,
        "layer_data": layer_data,
        "all_layouts_data": all_layouts_data,
        "fig_pallet_json": fig_pallet_json,
        "fig_layer_json": fig_layer_json,
        "fig_carton_json": fig_carton_json,
        "placements_json": safe_json(layout["placements"]) if layout else "[]",
        "carton_dims_json": safe_json({"h": instr.carton_h, "l": instr.carton_l, "w": instr.carton_w}),
    })

@require_POST
@_md_role
def planner_instruction_delete(request, pk: int):
    get_object_or_404(PalletizationInstruction, pk=pk).delete()
    messages.success(request, "Instrukcja usunięta.")
    return redirect("ui:planner_instructions")

@_planner
def planner_instruction_panel(request, pk: int):
    instr = get_object_or_404(PalletizationInstruction, pk=pk)
    layout_name = request.GET.get("layout", "") or instr.selected_layout
    render_layers = max(1, min(_as_int(request.GET.get("render_layers"), 3), 20))
    layer_idx = max(1, _as_int(request.GET.get("layer_idx"), 1))
    meta = instr.get_meta()
    carton_var = CartonVariant(
        sku=instr.product.code, variant=f"v{instr.version}",
        dims=Dimensions(l_cm=instr.carton_l, w_cm=instr.carton_w, h_cm=instr.carton_h),
        unit_weight_kg=instr.unit_weight, pieces_per_carton=instr.pcs_per_carton,
        demand_pieces=max(1, instr.demand_pcs), carton_tare_kg=instr.carton_tare,
        allow_rotation=True,
    ).validate()
    _layouts = instr.layouts or []
    layout = next((l for l in _layouts if l["name"] == layout_name), None) or (_layouts[0] if _layouts else None)
    if not layout:
        return HttpResponse("<div class='card p-4'>Brak layoutów. Przelicz instrukcję.</div>")
    if "cog" not in layout:
        layout["cog"] = _compute_cog(layout["placements"], meta)
    if "hints" not in layout:
        layout["hints"] = _optimization_hints(layout, instr.carton_h, meta["cargo_max_height_cm"], meta["max_weight_kg"])
    layer_idx = max(1, min(layer_idx, layout.get("layers_used", 1)))

    # Unit dimensions from linked product
    prod = instr.product
    unit_dims = None
    if prod.unit_length_cm and prod.unit_width_cm and prod.unit_height_cm:
        unit_dims = (prod.unit_length_cm, prod.unit_width_cm, prod.unit_height_cm)

    # Inner pack: prefer instruction-level, fall back to carton-level
    inner_pack = instr.inner_pack or (instr.carton.inner_pack if instr.carton else None)
    packs_per_carton = instr.packs_per_carton or (instr.carton.packs_per_carton if instr.carton else None)

    locations = list(WarehouseLocationType.objects.filter(is_active=True).order_by("name"))

    payload = _render_panel(
        meta, carton_var, instr.layouts, layout_name, render_layers,
        unit_dims=unit_dims, inner_pack=inner_pack, packs_per_carton=packs_per_carton,
        pcs_per_inner_pack=instr.pcs_per_inner_pack,
        locations=locations)
    payload.update({
        "instr": instr, "layout": layout, "layouts": instr.layouts[:15],
        "selected": layout_name, "layer_idx": layer_idx,
        "placements_json": safe_json(layout["placements"]),
        "carton_dims_json": safe_json({"h": instr.carton_h, "l": instr.carton_l, "w": instr.carton_w}),
    })
    return HttpResponse(render_to_string("ui/_panel.html", payload, request=request))

@_planner
def planner_instruction_excel(request, pk: int):
    instr = get_object_or_404(PalletizationInstruction, pk=pk)
    layout = instr.get_selected_layout()
    if not layout:
        messages.error(request, "Brak danych do eksportu.")
        return redirect("ui:planner_instruction_detail", pk=pk)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Instrukcja"

    ws.append(["Produkt", str(instr.product)])
    ws.append(["Wersja", instr.version])
    ws.append(["Paleta", instr.get_pallet_code_display()])
    ws.append(["Layout", layout["name"]])
    ws.append(["Kartonów/paletę", layout["cartons_per_pallet"]])
    ws.append(["Warstw", layout["layers_used"]])
    ws.append(["Waga całkowita [kg]", layout.get("weight_per_pallet_kg", "")])
    ws.append([])
    ws.append(["Kartonów/warstwa", layout.get("cartons_per_layer", "")])
    ws.append(["Wydajność (%)", layout.get("utilization_percent", "")])
    ws.append([])
    ws.append(["Uwagi", instr.notes])

    buf = io.BytesIO()
    neutralize_workbook(wb)  # SEC-007
    wb.save(buf)
    buf.seek(0)
    _safe_code = _re.sub(r'[^\w\-.]', '_', instr.product.code)
    fname = f"instrukcja_{_safe_code}_v{instr.version}.xlsx"
    resp = HttpResponse(buf, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f"attachment; filename=\"{fname}\""
    return resp

__all__ = [
    'warehouse_instruction',
    'planner_instructions',
    'planner_instruction_form',
    'planner_instruction_detail',
    'planner_instruction_delete',
    'planner_instruction_panel',
    'planner_instruction_excel',
]
