# core/ — shared kernel split into layers (base ◅ figures/xlsx/helpers ◅ packing).
# Re-exported wholesale by core/__init__.py so `from .core import *` is unchanged.
from .base import (
    CartonVariant, Dimensions, PALLET_BASE_HEIGHT_CM, PalletCalculator, PalletType,
    PalletizationInstruction, Product, WarehouseLocationType,
    _fig_json, get_pallet_preset, safe_json,
)
from .figures import (
    _fig_carton_3d, _fig_carton_with_packs_3d, _fig_inner_pack_3d, _fig_layer_2d,
    _fig_pallet_3d, _fig_unit_3d,
)
from .helpers import _as_int

def _build_pallet(code: str, max_height_total: int, max_weight_kg: int) -> tuple[PalletType, dict]:
    p = get_pallet_preset(code)
    cargo_max_height = max(0, int(max_height_total) - PALLET_BASE_HEIGHT_CM)
    pallet = PalletType(
        code=p.code,
        dims=Dimensions(l_cm=p.length_cm, w_cm=p.width_cm, h_cm=cargo_max_height),
        max_weight_kg=int(max_weight_kg),
    ).validate()
    meta = {
        "pallet_code": p.code,
        "length_cm": p.length_cm,
        "width_cm": p.width_cm,
        "cargo_max_height_cm": cargo_max_height,
        "max_height_total_cm": int(max_height_total),
        "base_height_cm": PALLET_BASE_HEIGHT_CM,
        "max_weight_kg": int(max_weight_kg),
    }
    return pallet, meta

def _form_max_height(form):
    """Target total height [cm]: from the chosen location type if set, else the manual field."""
    loc_id = form.cleaned_data.get("location")
    if loc_id:
        loc = WarehouseLocationType.objects.filter(pk=loc_id, is_active=True).first()
        if loc:
            return loc.total_height_cm
    return form.cleaned_data["max_height_total"]

def _compute_cog(placements: list, pallet_meta: dict) -> dict:
    if not placements:
        return {"x": 0.0, "y": 0.0, "offset_x": 0.0, "offset_y": 0.0}
    cx = sum(p["x"] + p["dx"] / 2.0 for p in placements) / len(placements)
    cy = sum(p["y"] + p["dy"] / 2.0 for p in placements) / len(placements)
    return {
        "x": round(cx, 1),
        "y": round(cy, 1),
        "offset_x": round(cx - pallet_meta["length_cm"] / 2.0, 1),
        "offset_y": round(cy - pallet_meta["width_cm"] / 2.0, 1),
    }

def _optimization_hints(layout: dict, carton_h: int, cargo_max_height: int, max_weight_kg: int) -> list:
    hints = []
    layers_used = layout["layers_used"]
    layers_by_height = layout["layers_by_height"]
    layers_by_weight = layout["layers_by_weight"]
    remaining_h = cargo_max_height - layers_used * carton_h
    if layers_by_height <= layers_by_weight and 0 < remaining_h < carton_h:
        hints.append(f"Zmniejsz H kartonu o {carton_h - remaining_h} cm → +1 warstwa ({layers_used + 1} łącznie)")
    if layers_by_weight < layers_by_height:
        hints.append(
            f"Limit wagowy ({layout['weight_per_pallet_kg']:.0f}/{max_weight_kg} kg). "
            f"Lżejsze opakowanie → więcej warstw (maks. wg wys.: {layers_by_height})."
        )
    if layout["area_used_pct"] < 65:
        hints.append(f"Niskie wypełnienie pow. ({layout['area_used_pct']}%). Sprawdź inne orientacje.")
    return hints

def _optimal_layout_option(carton: CartonVariant, pallet: PalletType, res):
    """OR-Tools CP-SAT proof of the best cartons-per-layer; returns a LayoutOption to add
    as a candidate when it beats every heuristic, else None (also None without ortools)."""
    try:
        from palletizer.services.ortools_layer import optimal_uniform_layer
        from palletizer.services.pallet_calculator import Placement2D, LayoutOption
    except Exception:
        return None
    L, W = int(pallet.length_cm), int(pallet.width_cm)
    cl, cw = int(carton.dims.l_cm), int(carton.dims.w_cm)
    best = max((o.cartons_per_layer for o in res.all_layouts), default=0)
    pl = optimal_uniform_layer(L, W, cl, cw, allow_rotation=carton.allow_rotation, time_limit_s=3.0)
    if not pl or len(pl) <= best:
        return None   # heuristics already optimal → nothing to add
    placements = tuple(Placement2D(x, y, w, h, w != cl) for (x, y, w, h) in pl)
    util = round(len(pl) * cl * cw / (L * W) * 100.0, 2) if L and W else 0.0
    return LayoutOption("Optymalny (OR-Tools)", len(pl), util, placements)


def _layers_by_weight(per_layer, carton_weight_kg, max_weight_kg, unlimited):
    """Ile warstw dopuszcza limit wagi palety. Gdy brak sensownej wagi (0) → `unlimited`
    (nie blokuj na braku danych). Jedno źródło wzoru dla _eval_layouts i _optimize_packaging."""
    plw = (per_layer or 0) * (carton_weight_kg or 0)
    return int(max_weight_kg // plw) if plw > 0 else unlimited


def _eval_layouts(carton: CartonVariant, pallet: PalletType, pallet_meta: dict, res,
                  extra_layouts=None) -> list[dict]:
    carton_weight = carton.carton_weight_kg
    cartons_needed = carton.cartons_needed
    cargo_max_height = pallet.max_height_cm
    layers_by_height = cargo_max_height // carton.dims.h_cm if carton.dims.h_cm else 0
    pallet_area = pallet_meta["length_cm"] * pallet_meta["width_cm"]
    carton_base_area = carton.dims.l_cm * carton.dims.w_cm
    carton_vol = carton.dims.l_cm * carton.dims.w_cm * carton.dims.h_cm

    layouts = []
    for o in list(res.all_layouts) + list(extra_layouts or []):
        per_layer = int(o.cartons_per_layer)
        layers_by_weight = _layers_by_weight(per_layer, carton_weight,
                                             pallet.max_weight_kg, layers_by_height)
        layers_used = max(0, min(layers_by_height, layers_by_weight))
        cartons_per_pallet = per_layer * layers_used if layers_used > 0 else 0
        pallets_full = 0
        remainder_cartons = cartons_needed
        if cartons_per_pallet > 0:
            pallets_full = cartons_needed // cartons_per_pallet
            remainder_cartons = cartons_needed % cartons_per_pallet

        cargo_height_used = layers_used * carton.dims.h_cm
        total_height_used = pallet_meta["base_height_cm"] + cargo_height_used
        weight_per_pallet = cartons_per_pallet * carton_weight if cartons_per_pallet > 0 else 0.0
        area_used = per_layer * carton_base_area
        area_used_pct = round((area_used / pallet_area) * 100.0, 2) if pallet_area else 0.0
        used_vol = cartons_per_pallet * carton_vol
        pallet_vol = pallet_area * max(1, cargo_height_used)
        cube_used_pct = round((used_vol / pallet_vol) * 100.0, 2) if pallet_vol else 0.0
        placements_list = [p.__dict__ for p in o.placements]

        layouts.append({
            "name": o.name,
            "cartons_per_layer": per_layer,
            "utilization_percent": float(o.utilization_percent),
            "placements": placements_list,
            "layers_by_height": int(layers_by_height),
            "layers_by_weight": int(layers_by_weight),
            "layers_used": int(layers_used),
            "cartons_per_pallet": int(cartons_per_pallet),
            "pallets_full": int(pallets_full),
            "remainder_cartons": int(remainder_cartons),
            "cargo_height_used_cm": int(cargo_height_used),
            "total_height_used_cm": int(total_height_used),
            "weight_per_pallet_kg": round(float(weight_per_pallet), 3),
            "area_used_pct": float(area_used_pct),
            "cube_used_pct": float(cube_used_pct),
        })

    layouts.sort(key=lambda d: (d["cartons_per_pallet"], d["cartons_per_layer"], d["utilization_percent"]), reverse=True)
    for i, lay in enumerate(layouts):
        lay["is_best"] = (i == 0)
        lay["cog"] = _compute_cog(lay["placements"], pallet_meta)
        lay["hints"] = _optimization_hints(lay, carton.dims.h_cm, int(cargo_max_height), int(pallet.max_weight_kg))
    return layouts

def _meta_from_rec(rec):
    b = rec.batch
    return {
        "pallet_code": b.pallet_code if b else "EU",
        "length_cm": b.pallet_length_cm if b else 120,
        "width_cm": b.pallet_width_cm if b else 80,
        "cargo_max_height_cm": max(0, (b.max_height_total_cm if b else 215) - PALLET_BASE_HEIGHT_CM),
        "max_height_total_cm": b.max_height_total_cm if b else 215,
        "base_height_cm": b.pallet_base_height_cm if b else PALLET_BASE_HEIGHT_CM,
        "max_weight_kg": b.max_weight_kg if b else 1000,
    }

def _carton_from_rec(rec):
    return CartonVariant(
        sku=rec.sku, variant=rec.variant,
        dims=Dimensions(l_cm=rec.carton_l, w_cm=rec.carton_w, h_cm=rec.carton_h),
        unit_weight_kg=rec.unit_weight, pieces_per_carton=rec.pcs_per_carton,
        demand_pieces=rec.demand_pcs, carton_tare_kg=rec.carton_tare, allow_rotation=True,
    ).validate()

def _render_panel(meta, carton, layouts, selected_name, render_layers, pid=None,
                  unit_dims=None, inner_pack=None, packs_per_carton=None,
                  pcs_per_inner_pack=None, locations=None, loc_fits=None):
    if not layouts:
        return {}
    layout = next((l for l in layouts if l["name"] == selected_name), layouts[0])
    fig_pallet = _fig_pallet_3d(meta, carton, layout, render_layers, int(layout["total_height_used_cm"]))
    fig_layer = _fig_layer_2d(meta, layout, carton.sku, 1)

    # Carton chart: show inner packs inside if available
    if inner_pack and packs_per_carton:
        fig_carton = _fig_carton_with_packs_3d(
            carton.dims.l_cm, carton.dims.w_cm, carton.dims.h_cm,
            carton.pieces_per_carton,
            inner_pack.length_cm, inner_pack.width_cm, inner_pack.height_cm,
            packs_per_carton)
    else:
        fig_carton = _fig_carton_3d(carton)

    ctx = {
        "pid": pid, "meta": meta, "carton": carton, "layouts": layouts[:15],
        "selected": selected_name, "layer_idx": 1,
        "fig_pallet_json": _fig_json(fig_pallet),
        "fig_layer_json": _fig_json(fig_layer),
        "fig_carton_json": _fig_json(fig_carton),
        "layout": layout,
        "placements_json": safe_json(layout["placements"]),
        "carton_dims_json": safe_json({"h": carton.dims.h_cm, "l": carton.dims.l_cm, "w": carton.dims.w_cm}),
        "locations": locations or [],
        "loc_fits": loc_fits or [],
        # Realistic Three.js pallet (textured cartons + shadows) for the result panel
        "three_pallet_json": safe_json({
            "type": "pallet",
            "pallet": {"l": meta["length_cm"], "w": meta["width_cm"], "base_h": meta.get("base_height_cm", PALLET_BASE_HEIGHT_CM)},
            "carton": {"l": carton.dims.l_cm, "w": carton.dims.w_cm, "h": carton.dims.h_cm},
            "placements": layout["placements"],
            "layers": max(1, min(int(render_layers or 3), int(layout.get("layers_used", 1)) or 1)),
            "label": "",
            "color": "#C8A87E",
        }),
    }

    # Unit 3D
    if unit_dims:
        ctx["fig_unit_json"] = _fig_json(
            _fig_unit_3d(unit_dims[0], unit_dims[1], unit_dims[2]))

    # Inner pack 3D — units_per_pack from instruction or inner_pack model
    ip_units = pcs_per_inner_pack or (inner_pack.units_per_pack if inner_pack else None)
    if inner_pack and unit_dims and ip_units:
        ctx["fig_inner_pack_json"] = _fig_json(
            _fig_inner_pack_3d(
                inner_pack.length_cm, inner_pack.width_cm, inner_pack.height_cm,
                unit_dims[0], unit_dims[1], unit_dims[2],
                ip_units))
    elif inner_pack:
        ctx["inner_pack"] = inner_pack

    return ctx

def _layout_signature(layout):
    """Geometria warstwy niezależna od nazwy wariantu: posortowane (x, y, dx, dy)."""
    return tuple(sorted((p.get("x"), p.get("y"), p.get("dx"), p.get("dy"))
                        for p in (layout or {}).get("placements") or []))


def _carry_selected_layout(instr, old_layouts):
    """Wybór użytkownika (NAZWA wariantu) po przeliczeniu: gdy nazwy nie ma w nowej liście,
    a ta sama geometria jest pod inną nazwą (zmiana kolejności/deduplikacji w silniku),
    przepnij wybór na nią — zamiast cichego spadku get_selected_layout() na layouts[0]."""
    name = instr.selected_layout
    if not name or any(lay.get("name") == name for lay in instr.layouts):
        return
    old = next((lay for lay in old_layouts or [] if lay.get("name") == name), None)
    sig = _layout_signature(old) if old else None
    hit = next((lay for lay in instr.layouts if sig and _layout_signature(lay) == sig), None)
    if hit:
        instr.selected_layout = hit["name"]


def _recalculate_instruction(instr: PalletizationInstruction):
    """Calculate layouts for an instruction and save."""
    pallet, meta = _build_pallet(instr.pallet_code, instr.max_height_total_cm, instr.max_weight_kg)
    carton_var = CartonVariant(
        sku=instr.product.code, variant=f"v{instr.version}",
        dims=Dimensions(l_cm=instr.carton_l, w_cm=instr.carton_w, h_cm=instr.carton_h),
        unit_weight_kg=instr.unit_weight, pieces_per_carton=instr.pcs_per_carton,
        demand_pieces=instr.demand_pcs, carton_tare_kg=instr.carton_tare,
        allow_rotation=True,
    ).validate()
    res = PalletCalculator.calculate(carton_var, pallet)
    # Add the OR-Tools-proven optimal layer as a candidate (deliberate build path, not the
    # hot quote/bulk path) so the stored cartons_per_pallet that quoting reads is accurate.
    opt = _optimal_layout_option(carton_var, pallet, res)
    old_layouts = instr.layouts
    instr.layouts = _eval_layouts(carton_var, pallet, meta, res,
                                  extra_layouts=[opt] if opt else None)
    _carry_selected_layout(instr, old_layouts)
    instr.pallet_length_cm = meta["length_cm"]
    instr.pallet_width_cm = meta["width_cm"]
    if not instr.selected_layout and instr.layouts:
        instr.selected_layout = instr.layouts[0]["name"]
    # Ręcznego układu (custom_layers) NIE kasujemy przy przeliczeniu — tylko oznaczamy jako
    # potencjalnie nieaktualny (wymiary/wejścia mogły się zmienić). Użytkownik zdecyduje:
    # poprawić w edytorze (czyści flagę) albo przywrócić układ silnika.
    if instr.custom_layers:
        instr.custom_layout_stale = True
    instr.save()
    return pallet, meta, carton_var

def _save_instruction_from_carton(product, carton, post):
    """Create a PalletizationInstruction for product+carton from POST pallet params."""
    from django.db import transaction as _tx
    from django.db.models import Max as _Max
    if not post.get("create_instr"):
        return None
    with _tx.atomic():
        # Lock the product row so concurrent requests get serialized version numbers.
        product_locked = Product.objects.select_for_update().get(pk=product.pk)
        last_v = product_locked.instructions.aggregate(m=_Max("version"))["m"] or 0
        instr = PalletizationInstruction.objects.create(
            product=product_locked, version=last_v + 1,
            name=post.get("instr_name", "").strip(),
            carton=carton,
            pallet_code="EU",
            pallet_length_cm=120, pallet_width_cm=80,
            max_height_total_cm=max(50, _as_int(post.get("pal_height"), 215)),
            pallet_base_height_cm=PALLET_BASE_HEIGHT_CM,
            max_weight_kg=max(1, _as_int(post.get("pal_weight"), 1000)),
            carton_l=carton.length_cm, carton_w=carton.width_cm, carton_h=carton.height_cm,
            unit_weight=carton.unit_weight_kg, pcs_per_carton=carton.pieces_per_carton,
            carton_tare=carton.tare_kg,
            demand_pcs=max(1, _as_int(post.get("demand_pcs"), 1000)),
        )
    try:
        _recalculate_instruction(instr)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("_recalculate_instruction failed for instr %s: %s", instr.pk, exc)
    return instr


__all__ = [
    '_build_pallet', '_carton_from_rec', '_compute_cog', '_eval_layouts',
    '_form_max_height', '_meta_from_rec', '_optimization_hints',
    '_recalculate_instruction', '_render_panel', '_save_instruction_from_carton',
]
