"""Parsowanie eksportu SAP MARM dla importu materiałów referencyjnych
(widok ``planner_ref_materials_import``).

Czysta logika (bez ORM): wiersze arkusza → słownik ``kod materiału → pola
MaterialReference``. Wiersz jednostki ``KAR`` daje pełny rekord (wymiary, waga,
szt/karton, EAN); pozostałe jednostki tylko nazwę/dostawcę — i to wyłącznie dla
materiału, którego jeszcze nie ma w wyniku.

Moduł NIE jest star-eksportowany przez ``ui.views`` (brak ``__all__`` widoków).
"""

# (klucz, nagłówek eksportu SAP MARM, domyślny indeks pozycyjny)
_COLUMNS = (
    ("mat", "Materiał", 0),
    ("alt", "Alternatywna jednostka miary", 1),
    ("num", "Licznik", 3),
    ("w", "Szerokość", 5),
    ("h", "Wysokość", 6),
    ("l", "Długość", 7),
    ("gross", "Waga brutto", 9),
    ("ean", "Kod EAN/UPC", 13),
    ("sup_sh", "Szukany ciąg zn.", 18),
    ("name", "Krótki tekst materiału", 16),
)


def resolve_ref_columns(header_row):
    """Indeksy kolumn wg nagłówka (pierwszy wiersz); brak nagłówka → indeks domyślny."""
    header = [str(c).strip() if c else "" for c in header_row]
    col = {h: i for i, h in enumerate(header)}
    cols = {key: col.get(label, default) for key, label, default in _COLUMNS}
    cols["sup_fl"] = col.get("Nazwa ", 19) if "Nazwa " in col else col.get("Nazwa", 19)
    return cols


def _text(value, limit=None):
    return (str(value).strip() if value is not None else "")[:limit]


def _float_or_none(value):
    return float(value) if value else None


def _positive(value):
    return value if value and value > 0 else None


def _texts(row, cols):
    return {
        "name": _text(row[cols["name"]], 250),
        "supplier_short": _text(row[cols["sup_sh"]], 50),
        "supplier_full": _text(row[cols["sup_fl"]], 500),
    }


def _carton_entry(row, cols):
    """Pełny rekord z wiersza KAR (wyjątek przy złej wartości → wiersz pominięty)."""
    w = _float_or_none(row[cols["w"]])
    h = _float_or_none(row[cols["h"]])
    l = _float_or_none(row[cols["l"]])  # noqa: E741
    gross = _float_or_none(row[cols["gross"]])
    num = row[cols["num"]]
    # int(row[..]) blows up on a "24.0"/"24,0" cell (skipping the whole row);
    # coerce via float like the dimension columns above.
    pcs = int(float(str(num).replace(",", "."))) if num else None
    ean_raw = row[cols["ean"]]
    ean = (str(ean_raw).strip() if ean_raw else "")[:30]
    return {
        **_texts(row, cols),
        "width_cm": _positive(w),
        "height_cm": _positive(h),
        "length_cm": _positive(l),
        "gross_kg": _positive(gross),
        "pieces_per_carton": pcs,
        "ean": ean,
    }


def _apply_row(refs, row, cols):
    mat = _text(row[cols["mat"]], 100)
    alt = _text(row[cols["alt"]])
    if mat and alt == "KAR":
        refs[mat] = _carton_entry(row, cols)
        return
    # Also capture name/supplier from any row for this material
    if mat and mat not in refs:
        texts = _texts(row, cols)
        if texts["name"]:
            refs[mat] = texts


def parse_ref_rows(rows, cols):
    """Wiersze danych → (refs: kod → pola, liczba pominiętych wierszy)."""
    refs = {}
    skipped = 0
    for row in rows:
        try:
            _apply_row(refs, row, cols)
        except Exception:
            skipped += 1
    return refs, skipped
