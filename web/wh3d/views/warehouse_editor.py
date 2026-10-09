# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from ui.views.core import (
    _build_rack_cells, _build_stack_base, _COL_IDX, _ED3D_COMPACT, _ED3D_FLOOR,
    _ED3D_LOC_PAT, _ED3D_PICKING_TYPES, _ED3D_STANDARD, _ED3D_SUFFIX_LEVEL,
    _md_role, _planner, get_object_or_404, json, JsonResponse, messages,
    redirect, render, require_POST, WarehouseLayout, WarehouseLayoutCell,
    WarehouseLocationMaster, WarehouseLocationMasterBatch, WarehouseSnapshot,
    WarehouseSnapshotRow,
)


@_md_role
def warehouse_rack_generator(request):
    """Generate warehouse layout + master data from rack physical dimensions."""
    if request.method == "GET":
        return render(request, "ui/warehouse_map/generator.html", {})

    # -- Collect inputs --------------------------------------------------------
    # Tolerant numeric parsers: a non-numeric value falls back to the default
    # instead of raising ValueError → 500 (this runs before the transaction try).
    def _pint(key, default):
        try:
            return int(request.POST.get(key, default) or default)
        except (ValueError, TypeError):
            return int(default)

    def _pfloat(key, default):
        try:
            return float(request.POST.get(key, default) or default)
        except (ValueError, TypeError):
            return float(default)

    zone        = request.POST.get("zone", "B0").strip().upper() or "B0"
    aisle       = request.POST.get("aisle", "01").strip().zfill(2)
    stack_start = _pint("stack_start", 100)
    stack_count = max(1, _pint("stack_count", 10))
    stack_step  = max(1, _pint("stack_step", 2))
    cols_raw    = request.POST.get("columns", "A,B,C").upper()
    columns     = [c.strip() for c in cols_raw.split(",") if c.strip()]
    if not columns:
        columns = ["A"]
    num_levels  = max(1, min(8, _pint("num_levels", 4)))

    level_heights = []
    for lv in range(1, num_levels + 1):
        try:
            h = int(request.POST.get(f"level_{lv}_height", 2500) or 2500)
        except (ValueError, TypeError):
            h = 2500
        level_heights.append(max(100, h))

    wh_type    = request.POST.get("wh_type", "").strip()
    max_weight = _pfloat("max_weight", 0)
    max_volume = _pfloat("max_volume", 0)
    name       = request.POST.get("name", "").strip() or f"Regał {aisle}"

    # Slot physical dimensions
    try:
        slot_width = max(100, int(request.POST.get("slot_width", 800) or 800))
    except (ValueError, TypeError):
        slot_width = 800
    try:
        slot_depth = max(100, int(request.POST.get("slot_depth", 1100) or 1100))
    except (ValueError, TypeError):
        slot_depth = 1100

    # Build a local column→index mapping for this request only (no global mutation).
    local_col_idx = {col: i for i, col in enumerate(columns)}

    # When "append" is set, add this rack to the active layout/master instead of
    # creating a new one and deactivating the rest — lets you build a multi-aisle
    # (atypical) warehouse from several generator runs without Excel.
    append = request.POST.get("append") in ("1", "on", "true", "True")

    # -- Generate location codes ----------------------------------------------
    # Format: B0-01-100-1A  (4-part: zone-aisle-stack-levelcol)
    stacks = [str(stack_start + i * stack_step) for i in range(stack_count)]

    from django.db import transaction
    from django.db.models import Max
    try:
        with transaction.atomic():
            active_layout = WarehouseLayout.objects.filter(is_active=True).first()
            active_master = WarehouseLocationMasterBatch.objects.filter(is_active=True).first()
            did_append = bool(append and active_layout and active_master)

            if did_append:
                layout = active_layout
                master_batch = active_master
                base_row = (layout.cells.aggregate(m=Max("grid_row"))["m"] or 0) + 2
            else:
                layout = WarehouseLayout.objects.create(name=name)
                WarehouseLayout.objects.exclude(pk=layout.pk).update(is_active=False)
                master_batch = WarehouseLocationMasterBatch.objects.create(name=f"Master {name}")
                WarehouseLocationMasterBatch.objects.exclude(pk=master_batch.pk).update(is_active=False)
                base_row = 0

            layout_cells, master_locs = _build_rack_cells(
                layout, master_batch, zone=zone, aisle=aisle, stacks=stacks,
                columns=columns, local_col_idx=local_col_idx, num_levels=num_levels,
                level_heights=level_heights, slot_width=slot_width, slot_depth=slot_depth,
                wh_type=wh_type, max_weight=max_weight, max_volume=max_volume, base_row=base_row,
            )

            WarehouseLayoutCell.objects.bulk_create(layout_cells, ignore_conflicts=True)
            WarehouseLocationMaster.objects.bulk_create(master_locs, ignore_conflicts=True)

            layout.location_count = layout.cells.count()
            layout.save()
            master_batch.location_count = master_batch.locations.count()
            master_batch.save()

    except Exception as exc:
        messages.error(request, f"Błąd generowania regału: {exc}")
        return redirect("ui:warehouse_map")

    total = len(layout_cells)
    verb = "Dodano do układu" if did_append else "Wygenerowano"
    messages.success(
        request,
        f"{verb} regał «{name}»: {total} lokalizacji "
        f"({stack_count} stosów × {len(columns)} kol. × {num_levels} poz.).",
    )
    return redirect("ui:warehouse_map")

@_md_role
def warehouse_editor3d(request):
    """3D CAD-like warehouse layout editor (master-data role required)."""
    active_master = WarehouseLocationMasterBatch.objects.filter(is_active=True).first()

    groups: dict = {}

    if active_master:
        qs = (
            WarehouseLocationMaster.objects
            .filter(batch=active_master)
            .values('location_code', 'warehouse_type', 'width_mm', 'depth_mm')
            .iterator(chunk_size=2000)
        )
        for row in qs:
            code = row['location_code']
            m = _ED3D_LOC_PAT.match(code)
            if not m:
                continue
            zone, aisle, stack_s, suffix = m.groups()
            suffix  = suffix.strip()
            row_key = f"{zone}-{aisle}"
            stack   = int(stack_s)

            if row_key not in groups:
                groups[row_key] = {
                    'name': row_key, 'zone': zone, 'aisle': aisle,
                    'stacks': set(), 'suffixes': set(), 'wh_types': set(),
                    'widths': [], 'depths': [],
                }
            g = groups[row_key]
            g['stacks'].add(stack)
            g['suffixes'].add(suffix)
            if row['warehouse_type']:
                g['wh_types'].add(row['warehouse_type'])
            if row['width_mm']:
                g['widths'].append(row['width_mm'])
            if row['depth_mm']:
                g['depths'].append(row['depth_mm'])

    # Real per-aisle positions from the active layout (cells carry the physical
    # grid taken from the Excel plan). Anchor each stack to its floor cell, then
    # derive the aisle centre + orientation (horizontal vs vertical run).
    POS_SCALE   = 0.8   # metres per grid unit along an aisle run (≈ one stack/bay)
    AISLE_PITCH = 2.6   # metres between adjacent aisle rows: rack depth (~1.1 m) plus a
                        # forklift driveway, so back-to-back racks no longer merge into one
                        # block and the przejazdy (corridors) show as gaps between rows.
    aisle_pos = {}      # row_key -> (posX, posZ, angle_deg)
    active_layout = WarehouseLayout.objects.filter(is_active=True).first()
    if active_layout:
        anchors = {}  # row_key -> list of (row, col) per stack floor
        seen_stack = {}
        for c in WarehouseLayoutCell.objects.filter(layout=active_layout).values(
                'location_code', 'grid_row', 'grid_col'):
            m = _ED3D_LOC_PAT.match(c['location_code'])
            if not m:
                continue
            rk = f"{m.group(1)}-{m.group(2)}"
            sk = (rk, m.group(3))
            # keep the leftmost column (floor anchor) per stack
            if sk not in seen_stack or c['grid_col'] < seen_stack[sk][1]:
                seen_stack[sk] = (c['grid_row'], c['grid_col'])
        for (rk, _stack), (r, col) in seen_stack.items():
            anchors.setdefault(rk, []).append((r, col))
        for rk, pts in anchors.items():
            cols = [p[1] for p in pts]; rows = [p[0] for p in pts]
            col_span = max(cols) - min(cols); row_span = max(rows) - min(rows)
            horizontal = col_span >= row_span
            mean_col = sum(cols) / len(cols)
            mean_row = sum(rows) / len(rows)
            # Stretch the cross-aisle axis (the one perpendicular to the rack run) by the
            # aisle pitch so a driveway opens between rows; keep the along-run axis at the
            # stack scale so rack length stays true to the plan.
            if horizontal:
                cx, cz, angle = mean_col * POS_SCALE, mean_row * AISLE_PITCH, 0
            else:
                cx, cz, angle = mean_col * AISLE_PITCH, mean_row * POS_SCALE, 90
            aisle_pos[rk] = (round(cx, 2), round(cz, 2), angle)

    # Słownik typów: level_heights [mm] z WarehouseRackType to źródło prawdy dla
    # wysokości poziomu; heurystyka sufiksowa (2.2/1.8/1.5 m) zostaje jako fallback,
    # gdy typ lokalizacji nie ma zdefiniowanych poziomów.
    from ui.models import WarehouseRackType as _RT
    _type_level_h = {}
    for _code, _lh in _RT.objects.filter(kind="rack").values_list("code", "level_heights"):
        vals = [v for v in (_lh or {}).values() if isinstance(v, (int, float)) and v > 0]
        if vals:
            _type_level_h[_code] = round(sum(vals) / len(vals) / 1000, 2)  # mm → m, średnia

    rack_library = []
    for key in sorted(groups):
        g    = groups[key]
        suf  = g['suffixes']
        bays = len(g['stacks'])

        has_std     = bool(suf & _ED3D_STANDARD)
        has_compact = bool(suf & _ED3D_COMPACT)
        has_floor   = bool(suf & _ED3D_FLOOR)
        is_picking  = bool(g['wh_types'] & _ED3D_PICKING_TYPES)

        dict_hs = [_type_level_h[t] for t in g['wh_types'] if t in _type_level_h]
        if dict_hs:
            level_h = round(sum(dict_hs) / len(dict_hs), 2)
        elif has_std:
            level_h = 2.2
        elif has_compact:
            level_h = 1.8
        else:
            level_h = 1.5

        # Exclude floor-level suffixes (C/D shelves) from upper-level count
        upper_levels = max(
            (_ED3D_SUFFIX_LEVEL.get(s, 0) for s in suf - _ED3D_FLOOR),
            default=0
        )
        total_levels = upper_levels + (1 if has_floor else 0)

        avg_w = (sum(g['widths']) / len(g['widths']) / 1000) if g['widths'] else 0.0
        avg_d = (sum(g['depths']) / len(g['depths']) / 1000) if g['depths'] else 0.0
        bay_w = round(max(0.5, min(avg_w, 3.0)), 2) if avg_w > 0.1 else 1.0
        depth = round(max(0.5, min(avg_d, 3.0)), 2) if avg_d > 0.1 else 1.1

        px, pz, angle = aisle_pos.get(key, (None, None, 0))
        rack_library.append({
            'key':        key,
            'name':       key,
            'zone':       g['zone'],
            'aisle':      g['aisle'],
            'bays':       bays,
            'bayW':       bay_w,
            'depth':      depth,
            'levels':     max(total_levels, 1),
            'levelH':     level_h,
            'type':       'PA',
            'isPicking':  is_picking,
            'hasCompact': has_compact,
            # C/D shelves are split into ~40 cm half-slots → render two per bay on the floor.
            'hasSplit':   any(s[:1] in ('C', 'D') for s in suf),
            'whTypes':    sorted(g['wh_types']),
            'suffixes':   sorted(suf),
            'posX':       px,      # real position from the layout (Excel plan)
            'posZ':       pz,
            'angle':      angle,   # 0 = horizontal run, 90 = vertical run
        })

    return render(request, "ui/warehouse_map/editor3d.html", {
        # raw object — json_script encodes once (json.dumps would double-encode → black 3D)
        "rack_library_json": rack_library,
    })

@_planner
def warehouse_editor(request):
    """2D top-down interactive warehouse editor."""
    active_layout = WarehouseLayout.objects.filter(is_active=True).first()
    if not active_layout:
        return render(request, "ui/warehouse_map/editor.html", {
            "no_layout": True,
        })

    # Get the latest snapshot (for occupancy state)
    latest_snapshot = WarehouseSnapshot.objects.order_by("-uploaded_at").first()

    # Get active master batch (for physical attributes)
    active_master = WarehouseLocationMasterBatch.objects.filter(is_active=True).first()

    # --- Build lookup dicts ---
    # Snapshot rows by location_code
    snap_rows = {}
    if latest_snapshot:
        for row in WarehouseSnapshotRow.objects.filter(snapshot=latest_snapshot).values(
            "location_code", "is_empty", "blocked_pick", "blocked_put",
            "capacity_mm", "warehouse_type", "zone", "aisle", "stack",
            "col_code", "level", "col_idx"
        ):
            snap_rows[row["location_code"]] = row

    # Master data by location_code
    master_rows = {}
    if active_master:
        for m in WarehouseLocationMaster.objects.filter(batch=active_master).values(
            "id", "location_code", "level", "warehouse_type", "height_mm",
            "max_volume_m3", "max_weight_kg"
        ):
            master_rows[m["location_code"]] = m

    # Build corrected stack base positions
    stack_base = _build_stack_base(active_layout)

    # Layout cells
    cells = list(WarehouseLayoutCell.objects.filter(layout=active_layout).values(
        "id", "location_code", "grid_row", "grid_col", "level"
    ))

    # Build combined data array
    locations = []
    for cell in cells:
        loc_code = cell["location_code"]
        snap = snap_rows.get(loc_code, {})
        master = master_rows.get(loc_code, {})

        # Parse aisle/stack from location_code if not in snapshot
        parts = loc_code.split("-")
        # zfill(2) to mirror _build_stack_base's key exactly — it pads the aisle, so a
        # single-digit aisle ("B0-1-100A") must pad here too or `key in stack_base` misses
        # and the cell falls back to raw grid coords, losing the physical-position fix.
        aisle = (snap.get("aisle") or (parts[1] if len(parts) > 1 else "")).zfill(2)
        raw3 = parts[2] if len(parts) > 2 else ""
        stack = snap.get("stack") or (raw3[:-1] if raw3 else "")
        zone = snap.get("zone") or (parts[0] if len(parts) > 0 else "")
        col_code = snap.get("col_code") or (raw3[-1].upper() if raw3 else "A")
        # col_idx 0 (column A) is valid — don't let `or` treat it as missing
        _ci = snap.get("col_idx")
        col_idx = _ci if _ci is not None else _COL_IDX.get(col_code, 0)

        # Corrected physical position: base_col + col_idx for X, base_row for Z
        key = (aisle, stack)
        if key in stack_base:
            base_col, base_row = stack_base[key]
            px = base_col + col_idx
            pz = base_row
        else:
            px = cell["grid_col"]
            pz = cell["grid_row"]

        entry = {
            "id": cell["id"],
            "location_code": loc_code,
            "grid_row": cell["grid_row"],
            "grid_col": cell["grid_col"],
            "px": px,    # corrected X for rendering
            "pz": pz,    # corrected Z for rendering (same for all levels)
            "level": cell["level"],
            "col_idx": col_idx,
            # State from snapshot
            "is_empty": snap.get("is_empty", True),
            "blocked_pick": snap.get("blocked_pick", False),
            "blocked_put": snap.get("blocked_put", False),
            "capacity_mm": snap.get("capacity_mm", 0),
            # Warehouse type: prefer master data
            "warehouse_type": master.get("warehouse_type") or snap.get("warehouse_type", ""),
            # Parsed fields
            "zone": zone,
            "aisle": aisle,
            "stack": stack,
            "col_code": snap.get("col_code", ""),
            # Master attributes
            "height_mm": master.get("height_mm", 0),
            "max_volume_m3": master.get("max_volume_m3", 0.0),
            "max_weight_kg": master.get("max_weight_kg", 0.0),
            # Flags
            "has_snapshot": bool(snap),
            "has_master": bool(master),
        }
        locations.append(entry)

    return render(request, "ui/warehouse_map/editor.html", {
        "no_layout": False,
        "active_layout": active_layout,
        "latest_snapshot": latest_snapshot,
        "active_master": active_master,
        # Pass the list itself — json_script encodes it once. (Pre-dumping to a string
        # here caused double-encoding: JSON.parse then returned a string, not an array,
        # so the editor saw 0 locations and rendered a black screen.)
        "locations": locations,
        "location_count": len(locations),
    })

@_md_role
@require_POST
def warehouse_layout_cell_move(request, cell_id: int):
    """AJAX: move a layout cell to a new grid position."""
    cell = get_object_or_404(WarehouseLayoutCell, pk=cell_id)
    try:
        data = json.loads(request.body)
        grid_row = int(data["grid_row"])
        grid_col = int(data["grid_col"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        return JsonResponse({"error": f"Nieprawidłowe dane: {e}"}, status=400)

    # Check collision — same layout, same grid pos, same level
    conflict = WarehouseLayoutCell.objects.filter(
        layout=cell.layout,
        grid_row=grid_row,
        grid_col=grid_col,
        level=cell.level,
    ).exclude(pk=cell_id).first()
    if conflict:
        return JsonResponse({"error": f"Pozycja zajęta przez {conflict.location_code}"}, status=409)

    cell.grid_row = grid_row
    cell.grid_col = grid_col
    cell.save(update_fields=["grid_row", "grid_col"])
    return JsonResponse({"ok": True})

__all__ = [
    'warehouse_rack_generator',
    'warehouse_editor3d',
    'warehouse_editor',
    'warehouse_layout_cell_move',
]
