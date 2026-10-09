# core/ — shared kernel split into layers (base ◅ figures/xlsx/helpers ◅ packing).
# Re-exported wholesale by core/__init__.py so `from .core import *` is unchanged.
from .base import (
    WarehouseLayoutCell, WarehouseLocationMaster, WarehouseLocationType,
    _COL_CODE_MAP, _SAP_COLS, _re,
)
from .ewm_levels import letter_level, letter_slot

def _parse_location_code(code: str):
    """Parse B0-01-300A → (zone, rack, bay, level_letter, level_num)."""
    m = _re.match(r'^([A-Z]\d+)-(\d+)-(\d+)([A-Z])$', code.strip())
    if not m:
        return None
    zone, rack, bay, lvl = m.group(1), m.group(2), m.group(3), m.group(4)
    # Poziom z litery EWM (B/C/D = półki poz. 1, X/G/H = 2, …; hala A: A–E = 1–5).
    # Nieznana litera → dotychczasowa numeracja alfabetyczna (A=1, B=2, …).
    level_num = letter_level(zone, lvl, default=ord(lvl) - ord('A') + 1)
    return zone, rack, bay, lvl, level_num

def _location_dim_groups(limit=None):
    """Defined warehouse location types (managed on /magazyn/lokalizacje/typy/) for the carton
    designer's location dropdown.

    Returns one entry per active `WarehouseLocationType` (dimensions in cm), carrying the
    net usable height the locations page shows so carton fitting matches the planner."""
    out = []
    for loc in WarehouseLocationType.objects.filter(is_active=True).order_by("name"):
        if not (loc.width_cm and loc.depth_cm and loc.total_height_cm):
            continue
        out.append({
            "id": loc.pk,
            "name": loc.name,
            "label": loc.name,
            "loc_class": loc.location_class,
            "is_pallet": bool(loc.is_pallet_location),
            "w_cm": loc.width_cm,            # clearance width  (W axis)
            "d_cm": loc.depth_cm,            # depth into rack  (L axis)
            "h_cm": loc.total_height_cm,     # total opening height (for display)
            "usable_h_cm": loc.usable_height_cm,   # net stackable height (for fitting)
            "max_weight_kg": round(loc.max_load_kg, 1) if loc.max_load_kg else 0.0,
        })
    return out

def _sap_get(row_lower, key):
    for col in _SAP_COLS.get(key, []):
        if col in row_lower and row_lower[col] not in (None, ""):
            return row_lower[col]
    return None

def _as_int(value, default=0):
    """Coerce a JSON/loose value to int, falling back to default on bad data.

    Guards rack-type JSON fields (level_cols / level_heights) that may hold
    non-numeric values if written via Django admin / fixtures rather than the UI.
    """
    try:
        return int(value)
    except (ValueError, TypeError):
        try:                                    # tolerate "3.0" / "40,5" loose input
            return int(float(str(value).replace(",", ".")))
        except (ValueError, TypeError):
            return default

def _parse_float(value, default=0.0):
    """Coerce a loose value to float, tolerant of the European decimal comma
    ('40,5' → 40.5). Bad/empty input → default. Shared by import/edit views."""
    try:
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return default

def _parse_int(value, default=0):
    """Coerce a loose value to int via float ('3,0' / '3.0' → 3). Bad/empty → default."""
    try:
        return int(float(str(value).replace(",", ".")))
    except (ValueError, TypeError):
        return default

def _parse_loc_code(code):
    """Parse a location code like B0-01-100A into (zone, aisle, stack, col_code, col_idx, level).

    Returns (zone, aisle, stack, col_code, col_idx, level) or None if not parseable.
    The zone prefix (e.g. 'B0') is included in the aisle key so that zones
    with identical aisle/stack numbers don't collide on the grid.
    """
    parts = code.split('-')
    # Format 4-częściowy buildera: zone-aisle-stack-{level}{col}, np. B0-01-100-2X (poziom 2, kol X).
    # MUSI iść przed gałęzią 3-częściową — inaczej parts[2]="100" (same cyfry) dawał kol='A',
    # poziom=1 i gubił parts[3] (realny poziom/kolumnę).
    if len(parts) >= 4 and parts[3] and parts[3][-1].isalpha() and parts[3][:-1].isdigit():
        zone = parts[0]
        aisle = f"{zone}-{parts[1]}"
        stack = parts[2]
        col_code = parts[3][-1].upper()
        level = int(parts[3][:-1])
        col_idx, _lvl = _COL_CODE_MAP.get(col_code, (0, 1))
        return aisle, stack, col_code, col_idx, level
    if len(parts) >= 3:
        zone = parts[0]
        aisle = f"{zone}-{parts[1]}"   # e.g. 'B0-01' — keeps zones separate
        stack_raw = parts[2]
        # strip trailing letter
        if stack_raw and stack_raw[-1].isalpha():
            col_code = stack_raw[-1].upper()
            stack = stack_raw[:-1]
        else:
            col_code = 'A'
            stack = stack_raw
        # Litera EWM = poziom (+ półka/część) w JEDNYM stosie → jedna kolumna siatki (0).
        # Nieznana litera (legacy J/K/L/M/N/O) → dotychczasowa mapa kolumn _COL_CODE_MAP.
        slot = letter_slot(zone, col_code)
        col_idx, level = (0, slot.level) if slot else _COL_CODE_MAP.get(col_code, (0, 1))
        return aisle, stack, col_code, col_idx, level
    return None

def _build_rack_cells(layout, master_batch, *, zone, aisle, stacks, columns,
                      local_col_idx, num_levels, level_heights, slot_width,
                      slot_depth, wh_type, max_weight, max_volume, base_row=0):
    """Build (layout_cells, master_locs) for one rack/aisle. No DB writes here.

    Codes follow the 4-part format zone-aisle-stack-levelcol, e.g. B0-01-100-1A.
    Each aisle is laid out on its own grid_row band (base_row) so multiple
    appended aisles don't overlap in the 2D top-down view.
    """
    cells, locs = [], []
    for si, stack in enumerate(stacks):
        for ci, col in enumerate(columns):
            col_idx = local_col_idx.get(col, ci)
            for lv in range(1, num_levels + 1):
                loc_code = f"{zone}-{aisle}-{stack}-{lv}{col}"
                cells.append(WarehouseLayoutCell(
                    layout=layout, location_code=loc_code,
                    grid_col=si * (len(columns) + 1) + col_idx,
                    grid_row=base_row, level=lv,
                ))
                locs.append(WarehouseLocationMaster(
                    batch=master_batch, location_code=loc_code, level=lv,
                    warehouse_type=wh_type, height_mm=level_heights[lv - 1],
                    width_mm=slot_width, depth_mm=slot_depth,
                    max_volume_m3=max_volume, max_weight_kg=max_weight,
                ))
    return cells, locs

# Selectable load heights (cm): 1.5 m … 2.6 m in 10 cm steps. Height is the variable
# the user picks; the per-pallet weight cap is held constant across heights.

__all__ = [
    '_as_int', '_build_rack_cells', '_location_dim_groups', '_parse_float',
    '_parse_int', '_parse_loc_code', '_parse_location_code', '_sap_get',
]
