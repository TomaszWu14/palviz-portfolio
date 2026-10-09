"""Czyste parsowanie importu aktywności pickerów (``heatmap_upload``).

Wydzielone z widoku (CODE-001) bez zmiany zachowania: odczyt pliku xlsx/csv do nagłówków
(lower-case) i wierszy, wykrycie kolumn po słowach kluczowych oraz zamiana jednego wiersza
eksportu na pola ``PickerActivity``. Bez dostępu do bazy. Moduł NIE jest
star-eksportowany z ``wh3d.views``.
"""
import csv
import datetime as _dt
import io
import re

from django.utils.timezone import is_aware, make_aware

from ui.views.core import _detect_column, _parse_dt

# Słowa kluczowe kolumn (kolejność = priorytet; _detect_column dopasowuje podciąg).
_LOC_KEYS = [
    "msc. skład", "źr. msc", "storage bin",          # SAP-specific (most precise first)
    "lokalizacja", "location", "adres", "miejsce",    # generic full-word patterns
]
# Primary datetime column (combined) — "czas" removed so it doesn't steal the time column
_DT_KEYS = ["data potw", "conf", "potwier", "timestamp", "datetime"]
_DATE_KEYS = ["data", "date"]
# Companion time column — always detected independently (SAP exports date+time separately)
_TIME_KEYS = ["czas potw", "godzina potw", "time potw"]
_TIME_FALLBACK_KEYS = ["czas", "time", "godzina"]
# Detal pobrania (opcjonalny) → podpowiedź jednostki na ekranie kontroli HU. Kolumny
# nieobecne (stary plik heatmapy) → pola zostają puste, import działa jak dawniej.
_OPTIONAL_KEYS = {
    "pick": ["potwierdzone przez", "picker", "użytkownik", "user", "operator", "pracownik",
             "przez"],
    "task": ["działanie", "typ zad", "task", "type", "zlecen"],
    "mat": ["materiał", "material", "matnr", "indeks", "artykuł", "sku", "towar"],
    "qty": ["ilość potw", "ilosc", "quantity", "qty", "ilość"],
    "unit": ["jm", "jednostka", "unit", "ajm", "me"],
    "lot": ["partia", "seria", "charge", "batch", "lot"],
}
# Pole tekstowe PickerActivity → (klucz kolumny, maks. długość).
_TEXT_FIELDS = {
    "picker_name": ("pick", 100),
    "task_type": ("task", 50),
    "material_code": ("mat", 50),
    "unit": ("unit", 20),
    "lot": ("lot", 32),
}
_TIME_FORMATS = ("%H:%M:%S", "%H:%M")
# Sama data (bez czasu) — SAP eksportuje datę i czas w osobnych kolumnach.
_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y")
# Aliasy tej długości lub krótsze („jm”, „me”, „qty”…) dopasowujemy jako całe słowo —
# jako podciąg „me” trafiało w „timestamp”/„name”/„time”.
_SHORT_ALIAS_LEN = 3


# ── odczyt pliku ─────────────────────────────────────────────────────────────
def _read_all_rows(f):
    if f.name.lower().endswith((".xlsx", ".xls")):
        import openpyxl
        wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        ws = wb.active
        all_rows = list(ws.iter_rows(values_only=True))
        wb.close()
        return all_rows
    raw = f.read()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Eksport SAP z polskiego Windowsa (jak imports_excel_md / ewm_tasks).
        content = raw.decode("cp1250", errors="replace")
    return list(csv.reader(io.StringIO(content)))


def read_table(f):
    """``(nagłówki, wiersze_danych)`` albo ``None`` dla pustego pliku.

    Wyjątki odczytu propagują — widok zamienia je na „Błąd odczytu pliku”."""
    all_rows = _read_all_rows(f)
    if not all_rows:
        return None
    headers = [str(c).lower().strip() if c is not None else "" for c in all_rows[0]]
    return headers, all_rows[1:]


# ── wykrywanie kolumn ────────────────────────────────────────────────────────
def _detect_time(headers, dt_idx):
    time_idx = _detect_column(headers, _TIME_KEYS)
    if time_idx is None:
        time_idx = _detect_column(headers, _TIME_FALLBACK_KEYS)
        if time_idx is not None and time_idx == dt_idx:
            time_idx = None  # same column — not a companion
    return time_idx


def _keyword_matches(keyword, header):
    if len(keyword) > _SHORT_ALIAS_LEN:
        return keyword in header
    return re.search(rf"(?<![^\W\d_]){re.escape(keyword)}(?![^\W\d_])", header) is not None


def _detect_optional(headers, keywords, used):
    """Jak ``_detect_column``, ale krótkie aliasy tylko jako całe słowo i bez kolumn
    już przypisanych (``used``)."""
    lowers = [h.lower() for h in headers]
    for kw in keywords:
        kw_l = kw.lower()
        for i, h in enumerate(lowers):
            if i not in used and _keyword_matches(kw_l, h):
                return i
    return None


def detect_columns(headers):
    """Indeksy kolumn: ``loc``, ``when`` (data+czas lub sama data), ``time``
    i opcjonalne ``pick/task/mat/qty/unit/lot`` (``None`` = brak kolumny)."""
    dt_idx = _detect_column(headers, _DT_KEYS)
    date_idx = None if dt_idx is not None else _detect_column(headers, _DATE_KEYS)
    cols = {
        "loc": _detect_column(headers, _LOC_KEYS),
        "when": dt_idx if dt_idx is not None else date_idx,
        "time": _detect_time(headers, dt_idx),
    }
    used = {idx for idx in cols.values() if idx is not None}
    for key, kws in _OPTIONAL_KEYS.items():
        cols[key] = _detect_optional(headers, kws, used)
        if cols[key] is not None:
            used.add(cols[key])
    return cols


def column_error(cols, data_rows):
    """Komunikat błędu walidacji kolumn/wierszy albo ``None``."""
    if cols["loc"] is None:
        return "Nie znaleziono kolumny lokalizacji (lok/location/adres/msc.skład)."
    if cols["when"] is None:
        return "Nie znaleziono kolumny daty/czasu (data potw/conf/date)."
    if not data_rows:
        return "Plik nie zawiera wierszy danych (tylko nagłówek)."
    return None


# ── parsowanie wiersza ───────────────────────────────────────────────────────
def _cell(row, idx):
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _date_part(dt_val):
    if isinstance(dt_val, _dt.datetime):
        return dt_val.date()
    if isinstance(dt_val, _dt.date):
        return dt_val
    # CSV: dt_val is a string — parse just its date component.
    parsed = _parse_dt(dt_val)
    if parsed:
        return parsed.date()
    return _parse_date_only(dt_val)


def _parse_date_only(val):
    s = str(val).strip()
    for fmt in _DATE_FORMATS:
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _time_part(time_val):
    if isinstance(time_val, _dt.time):
        return time_val
    # CSV: time_val is a string like "08:15:00" / "8:15" — parse it.
    for fmt in _TIME_FORMATS:
        try:
            return _dt.datetime.strptime(str(time_val).strip(), fmt).time()
        except ValueError:
            continue
    return None


def parse_when(dt_val, time_val):
    """Znacznik czasu wiersza (aware) albo ``None``. SAP eksportuje datę i czas
    w osobnych kolumnach — łączymy je, gdy obie są obecne i parsowalne."""
    dt = None
    if dt_val is not None and time_val is not None:
        date_part, time_part = _date_part(dt_val), _time_part(time_val)
        if date_part and time_part:
            dt = _dt.datetime.combine(date_part, time_part)
    if dt is None:
        dt = _parse_dt(dt_val)
    if dt is None and dt_val is not None:
        date_only = _parse_date_only(dt_val)          # sama data bez czasu → północ
        if date_only:
            dt = _dt.datetime.combine(date_only, _dt.time())
    if dt is not None and not is_aware(dt):
        dt = make_aware(dt)
    return dt


def parse_qty(val):
    """Ilość jako float albo ``None``. Liczby z xlsx (także 0) wprost; tekst w formacie
    europejskim („1.234,5”, „1 234,5”) i angielskim („1,234.5”) — separator występujący
    jako ostatni jest dziesiętny. Istniejące helpery (``palletizer.io.parsing``,
    ``_parse_float`` z ``ui.views.core``) nie obsługują separatora tysięcy."""
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    s = str(val if val is not None else "").strip()
    s = s.replace(" ", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        thousands = "." if s.rfind(",") > s.rfind(".") else ","
        s = s.replace(thousands, "")
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_row(row, cols):
    """Pola ``PickerActivity`` (bez partii) dla wiersza albo ``None`` (wiersz pomijany:
    brak lokalizacji lub nieparsowalna data)."""
    loc = str(_cell(row, cols["loc"]) or "").strip()
    if not loc:
        return None
    dt = parse_when(_cell(row, cols["when"]), _cell(row, cols["time"]))
    if dt is None:
        return None
    fields = {name: str(_cell(row, cols[key]) or "").strip()[:limit]
              for name, (key, limit) in _TEXT_FIELDS.items()}
    fields.update(
        location_code=loc[:50],
        confirmed_at=dt,
        qty=parse_qty(_cell(row, cols["qty"])),
    )
    return fields
