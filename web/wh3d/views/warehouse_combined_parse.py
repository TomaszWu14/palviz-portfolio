"""Czyste parsowanie kombinowanego importu (``warehouse_combined_upload``).

Wydzielone z widoku (CODE-001) bez zmiany zachowania: odczyt csv/xlsx do listy wierszy,
wykrycie wiersza nagłówka po aliasach ``_COMBINED_COL_ALIASES``, zamiana wierszy na
słowniki lokalizacji, pozycje na siatce layoutu, wymiary typów regałów i backfill
wymiarów ze słownika. Bez zapisu do bazy. Moduł NIE jest star-eksportowany z
``wh3d.views``.

Parser ``warehouse_map_upload_parse`` NIE jest tu reużyty celowo — ma inne zachowanie
(nagłówek zawsze w 1. wierszu, słowniki wierszy, ścisłe utf-8, osobna obsługa .xls).
"""
import csv
import io
import json
from collections import defaultdict

from ui.views.core import _COMBINED_COL_ALIASES, _parse_float, _parse_int, _parse_loc_code
from ui.views.core.ewm_levels import level_height_mm

# Brak/nieparsowalny kod lokalizacji → (aisle, stack, col_code, col_idx, level).
_UNPARSED_LOC = ("00", "0", "A", 0, 1)


# ── odczyt pliku ─────────────────────────────────────────────────────────────
def read_csv_rows(f):
    """CSV → lista list. Dekodowanie z ``errors="replace"``; błędy modułu csv
    (np. pole > limitu) propagują się — obecne zachowanie."""
    text = f.read().decode("utf-8-sig", errors="replace")
    return list(csv.reader(io.StringIO(text)))


def read_xlsx_rows(f):
    """Aktywny arkusz → lista krotek (wartości). Błędy odczytu propagują się."""
    import openpyxl as _openpyxl

    wb = _openpyxl.load_workbook(f, read_only=True, data_only=True)
    return list(wb.active.iter_rows(values_only=True))


# ── nagłówek ─────────────────────────────────────────────────────────────────
def _first_matching_col(cells, aliases):
    """Indeks PIERWSZEJ komórki zawierającej dowolny alias (substring) albo None."""
    for ci, c in enumerate(cells):
        if any(alias in c for alias in aliases):
            return ci
    return None


def _header_cells(row):
    return [str(c).lower().strip() if c is not None else "" for c in row]


def detect_header(rows):
    """Pierwszy wiersz z aliasem kodu lokalizacji = nagłówek.

    Zwraca ``(header_idx, col_map)``; ``col_map`` (pole → indeks kolumny) jest pusty,
    gdy nagłówka nie znaleziono."""
    loc_aliases = _COMBINED_COL_ALIASES["location_code"]
    for ri, row in enumerate(rows):
        cells = _header_cells(row)
        if _first_matching_col(cells, loc_aliases) is None:
            continue
        col_map = {}
        for field, aliases in _COMBINED_COL_ALIASES.items():
            ci = _first_matching_col(cells, aliases)
            if ci is not None:
                col_map[field] = ci
        return ri, col_map
    return None, {}


# ── konwersje ────────────────────────────────────────────────────────────────
def _str(v):
    return str(v).strip() if v is not None else ""


def _cell(row, col_map, field, default=None):
    ci = col_map.get(field)
    if ci is None or ci >= len(row):
        return default
    return row[ci]


def _parse_json_field(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _clean_dict(raw, lo, hi):
    """Normalizuj level_cols/level_heights jak formularz interaktywny: dict{str:int}
    w zakresie [lo, hi]. Śmieci (lista, wartości nienumeryczne) → None, żeby nie trafiły
    do modelu i nie wywaliły renderu 3D (editor3d/rack_library zakładają dict{str:int})."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for k, v in raw.items():
        try:
            out[str(k)] = max(lo, min(hi, int(v)))
        except (ValueError, TypeError):
            pass
    return out or None


# ── wiersze → lokalizacje ────────────────────────────────────────────────────
def parse_row(row, col_map, loc):
    """Jeden wiersz danych (kod ``loc`` już wyczyszczony) → słownik lokalizacji."""
    def get(field, default=None):
        return _cell(row, col_map, field, default)

    fields = {
        "wh_type": _str(get("warehouse_type", "")),
        "height_mm": _parse_int(get("height_mm", 0)),
        "width_mm": _parse_int(get("width_mm", 0)),
        "depth_mm": _parse_int(get("depth_mm", 0)),
        "max_weight_kg": _parse_float(get("max_weight_kg", 0.0)),
        "max_volume_m3": _parse_float(get("max_volume_m3", 0.0)),
        # Ta sama normalizacja co formularz interaktywny — import nie może wpisać
        # śmieciowego JSON do level_cols/level_heights (wywala render 3D).
        "level_heights": _clean_dict(_parse_json_field(get("level_heights")), 100, 100_000),
        "level_cols": _clean_dict(_parse_json_field(get("level_cols")), 1, 3),
    }
    # Pozycja na siatce z kodu lokalizacji (po polach — kolejność jak w oryginale).
    aisle, stack, col_code, col_idx, level = _parse_loc_code(loc) or _UNPARSED_LOC
    return {"loc": loc, "zone": aisle.split("-")[0], "col_code": col_code,
            "aisle": aisle, "stack": stack, "col_idx": col_idx, "level": level, **fields}


def parse_rows(data_rows, col_map):
    """Wiersze danych → lista lokalizacji; puste kody i duplikaty (pierwszy wygrywa)
    są pomijane."""
    parsed = []
    seen = set()
    for row in data_rows:
        if not row:
            continue
        raw_loc = _cell(row, col_map, "location_code")
        if not raw_loc:
            continue
        loc = _str(raw_loc)
        if not loc or loc in seen:
            continue
        seen.add(loc)
        parsed.append(parse_row(row, col_map, loc))
    return parsed


# ── siatka layoutu ───────────────────────────────────────────────────────────
def grid_ranks(parsed):
    """Kolejność alej i stosów (w aleji) wg pierwszego wystąpienia."""
    aisle_ranks = {}
    stack_ranks = {}
    aisle_stack_counter = defaultdict(int)
    for p in parsed:
        aisle, stack = p["aisle"], p["stack"]
        if aisle not in aisle_ranks:
            aisle_ranks[aisle] = len(aisle_ranks)
        key = (aisle, stack)
        if key not in stack_ranks:
            stack_ranks[key] = aisle_stack_counter[aisle]
            aisle_stack_counter[aisle] += 1
    return aisle_ranks, stack_ranks


def grid_position(p, aisle_ranks, stack_ranks):
    """→ ``(grid_col, grid_row)`` komórki layoutu."""
    stack_rank = stack_ranks[(p["aisle"], p["stack"])]
    return stack_rank * 4 + p["col_idx"], aisle_ranks[p["aisle"]] * 3


# ── typy regałów ─────────────────────────────────────────────────────────────
def _has_dims(p):
    return any([p["width_mm"], p["depth_mm"], p["height_mm"],
                p["level_heights"] is not None, p["level_cols"] is not None])


def _type_defaults(p):
    defaults = {}
    if p["width_mm"]:
        defaults["width_mm"] = p["width_mm"]
    if p["depth_mm"]:
        defaults["depth_mm"] = p["depth_mm"]
    if p["level_heights"] is not None:
        defaults["level_heights"] = p["level_heights"]
    if p["level_cols"] is not None:
        defaults["level_cols"] = p["level_cols"]
    return defaults


def rack_type_defaults(parsed):
    """Kod typu → pola wymiarów do zapisu (pierwsze wystąpienie z wymiarami wygrywa).
    Wysokość sama w sobie kwalifikuje typ, ale nie trafia do pól (obecne zachowanie)."""
    first = {}
    for p in parsed:
        wt = p["wh_type"]
        if wt and wt not in first and _has_dims(p):
            first[wt] = p
    return {code: _type_defaults(p) for code, p in first.items()}


# Pola master uzupełniane 1:1 z typu regału (gdy puste w imporcie) + wartość „brak”.
_TYPE_FALLBACK_FIELDS = (("width_mm", 0), ("depth_mm", 0),
                         ("max_weight_kg", 0.0), ("max_volume_m3", 0.0))


def dims_from_type(p, rack_types):
    """Brakujące wymiary lokalizacji ze słownika typów (tylko ``kind=rack``)."""
    rt = rack_types.get(p["wh_type"])
    if rt is None or rt.kind != "rack":
        return {}
    filled = {}
    if not p["height_mm"]:
        # Klucz = numer poziomu 1..5; półki B/C/D (i części G/H) najpierw „1B”/„2G”…
        filled["height_mm"] = level_height_mm(rt.level_heights, p["zone"],
                                              p["col_code"], p["level"])
    for field, empty in _TYPE_FALLBACK_FIELDS:
        if not p[field]:
            filled[field] = getattr(rt, field) or empty
    return filled


def master_defaults(p, rack_types):
    """Pola ``WarehouseLocationMaster`` (defaults dla update_or_create)."""
    return {
        "level": p["level"],
        "warehouse_type": p["wh_type"],
        "height_mm": p["height_mm"],
        "width_mm": p["width_mm"],
        "depth_mm": p["depth_mm"],
        "max_weight_kg": p["max_weight_kg"],
        "max_volume_m3": p["max_volume_m3"],
        **dims_from_type(p, rack_types),
    }
