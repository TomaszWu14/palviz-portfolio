"""Czyste parsowanie importu snapshotu SAP WMS / EWM (``warehouse_map_upload``).

Wydzielone z widoku (CODE-001) bez zmiany zachowania: odczyt pliku xlsx/csv do listy
słowników z nagłówkami w lower-case oraz zamiana jednego wiersza eksportu na pola
``WarehouseSnapshotRow``. Moduł NIE jest star-eksportowany z ``wh3d.views``.
"""
import csv
import re

from ui.views.core import _SAP_COLS, _sap_get
from ui.views.core.ewm_levels import letter_slot

_SAP_YES = ("tak", "yes", "true", "1", "x")

# col_code → (level, col_idx) — FALLBACK dla liter spoza EWM (legacy J/K/L/M/N/O
# generatora). Litery EWM (A–D, S–W, X/G/H/Y/Z; hala A: A–E) idą przez
# ui.views.core.ewm_levels.letter_slot: poziom z litery, jedna kolumna na stos (col_idx 0).
COL_MAP = {
    "A": (1, 0), "B": (1, 1), "C": (1, 2),
    "X": (2, 0), "J": (2, 1), "K": (2, 2),
    "Y": (3, 0), "L": (3, 1), "M": (3, 2),
    "Z": (4, 0), "N": (4, 1), "O": (4, 2),
}

_LEVEL_SUFFIX_RE = re.compile(r'^(\d+)([A-Za-z]+)$')


# ── odczyt pliku ─────────────────────────────────────────────────────────────
def _read_xlsx_rows(f):
    import openpyxl as _openpyxl

    wb = _openpyxl.load_workbook(f, read_only=True, data_only=True)
    raw_headers = None
    rows_data = []
    for row in wb.active.iter_rows(values_only=True):
        if raw_headers is None:
            raw_headers = [str(c).strip().lower() if c else "" for c in row]
            continue
        if not any(row):
            continue
        rows_data.append(dict(zip(raw_headers, row)))  # noqa: B905 — krótsze wiersze OK
    return rows_data


def _read_csv_rows(f):
    text = f.read().decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    return [{k.strip().lower(): v for k, v in r.items()} for r in reader]


def read_upload_rows(f):
    """Plik → lista wierszy (dict, nagłówki lower-case). ``None`` = nieobsługiwany format.
    Błędy odczytu propagują się (widok zamienia je na „Błąd odczytu pliku”)."""
    fname = f.name.lower()
    if fname.endswith((".xlsx", ".xls")):
        return _read_xlsx_rows(f)
    if fname.endswith(".csv"):
        return _read_csv_rows(f)
    return None


# ── kod lokalizacji ──────────────────────────────────────────────────────────
def _split_four_part(parts):
    """B0-01-100-2B (jawny poziom) lub dzielona B0-01-300C-1 → (level, col_code, stack)."""
    raw_lc = parts[3]
    m4 = _LEVEL_SUFFIX_RE.match(raw_lc)
    # Lokalizacja DZIELONA separatorem: <stos><litera>-<sub>, np. 300C-1 / 300C-2.
    # parts[3] = czysta cyfra (sub-slot), parts[2] kończy się literą boku.
    # Bez tego bay 'C' ląduje w stack, a C-1/C-2 kolidują (druga znika).
    if raw_lc.isdigit() and parts[2] and parts[2][-1].isalpha():
        return None, parts[2][-1].upper(), parts[2][:-1].strip()
    if m4:
        return int(m4.group(1)), m4.group(2).upper(), parts[2].strip()
    return None, (raw_lc[-1].upper() if raw_lc else ""), parts[2].strip()


def _split_three_part(parts, loc, row):
    """B0-01-100A (stos z kolumny SAP albo z kodu) → (col_code, stack)."""
    raw_p2 = parts[2] if len(parts) > 2 else ""
    last_alpha = loc[-1] if loc and loc[-1].isalpha() else ""
    sap_stack = _sap_get(row, "stack")
    if sap_stack:
        return last_alpha, str(sap_stack).strip()
    if raw_p2 and raw_p2[-1].isalpha():
        return raw_p2[-1].upper(), raw_p2[:-1]
    return last_alpha, raw_p2


def _int_or_none(value):
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def parse_location(loc, row):
    """Kod lokalizacji (+ kolumny SAP) → zone/aisle/stack/col_code/level/col_idx."""
    # Supports 3-part: B0-01-100A and 4-part: B0-01-100-2B (explicit level)
    parts = loc.split("-")
    zone = parts[0] if len(parts) > 0 else ""
    aisle = str(_sap_get(row, "aisle") or (parts[1] if len(parts) > 1 else "")).zfill(2)
    if len(parts) >= 4:
        level_explicit, col_code, stack = _split_four_part(parts)
    else:
        level_explicit = None
        col_code, stack = _split_three_part(parts, loc, row)
    slot = letter_slot(zone, col_code) if level_explicit is None else None
    if slot:
        # Kod EWM: poziom ZAWSZE z litery (kolumna „Poziom miejsca skł.” bywa błędna).
        level, col_idx = slot.level, 0
    else:
        level_default, col_idx = COL_MAP.get(col_code, (1, 0))
        level = level_explicit or _int_or_none(_sap_get(row, "level")) or level_default
    return {"zone": zone, "aisle": aisle, "stack": stack, "col_code": col_code,
            "level": level, "col_idx": col_idx}


# ── zajętość / blokady / pojemność ───────────────────────────────────────────
def _occupancy_fallback_empty(row):
    """Brak kolumny „Puste” → zajętość z HU / produktu / ilości."""
    hu_val = _sap_get(row, "handling_unit")
    if hu_val and str(hu_val).strip():
        return False    # stock-at-location list: a handling unit ⇒ occupied
    product_val = _sap_get(row, "product")
    if product_val and str(product_val).strip():
        return False
    qty_val = _sap_get(row, "quantity")
    if qty_val is None:
        return True
    try:
        return float(qty_val) <= 0
    except (ValueError, TypeError):
        return True


def row_is_empty(row):
    # „Puste miejsce skład.": 'X' = PUSTE, puste pole = ZAJĘTE (SAP). _sap_get
    # pomija "", więc zajęte wiersze (blank) myliły się z brakiem kolumny i całość
    # wychodziła pusta. Czytamy surowo: gdy kolumna ISTNIEJE, blank = zajęte.
    empty_key = next((k for k in _SAP_COLS["is_empty"] if k in row), None)
    if empty_key is not None:
        return str(row[empty_key] or "").strip().lower() in _SAP_YES
    return _occupancy_fallback_empty(row)


def sap_flag(row, key):
    # Blokady z EWM są flagą „X” (jak „Puste miejsce skład.”) — bez „x” każda
    # zablokowana lokalizacja wchodziła do snapshotu jako wolna.
    val = _sap_get(row, key)
    return str(val).strip().lower() in _SAP_YES if val is not None else False


def capacity_mm(row):
    cap_raw = _sap_get(row, "capacity")
    try:
        return int(float(str(cap_raw))) if cap_raw is not None else 0
    except (ValueError, TypeError):
        return 0


def snapshot_row_fields(loc, row):
    """Pola ``WarehouseSnapshotRow`` (bez FK snapshotu) dla kodu ``loc`` (już UPPER)."""
    geo = parse_location(loc, row)
    # Przycięcie do max_length — długa nazwa grupy składowania / sekcji z eksportu
    # SAP wywalała import na PostgreSQL („value too long for varchar(N)"); SQLite tolerował.
    return {
        "location_code": loc[:50],
        "warehouse_type": str(_sap_get(row, "wh_type") or "")[:10],
        "section": str(_sap_get(row, "section") or "")[:20],
        "storage_group": str(_sap_get(row, "storage_group") or "")[:50],
        "is_empty": row_is_empty(row),
        "blocked_pick": sap_flag(row, "blocked_pick"),
        "blocked_put": sap_flag(row, "blocked_put"),
        "capacity_mm": capacity_mm(row),
        "zone": geo["zone"][:10],
        "aisle": geo["aisle"][:10],
        "stack": geo["stack"][:10],
        "col_code": geo["col_code"][:5],
        "level": geo["level"],
        "col_idx": geo["col_idx"],
    }
