# core/ — shared kernel split into layers (base ◅ figures/xlsx/helpers ◅ packing).
# Re-exported wholesale by core/__init__.py so `from .core import *` is unchanged.
from .base import HttpResponse
from .helpers_parse import _parse_float as _xlsx_float, _parse_int as _xlsx_int  # noqa: F401

# Górny limit wierszy importu z pliku (ochrona pamięci workera) — wspólny dla importerów.
MAX_IMPORT_ROWS = 60_000


def _read_table(file_obj):
    """Read an uploaded CSV/XLSX table (header in row 1) into (header_lower, rows).

    - .xlsx/.xls via openpyxl; otherwise CSV with auto delimiter (';' vs ',').
    - Blank rows are dropped; `header_lower` is the stripped/lowercased first row;
      `rows` excludes the header. Returns ([], []) when there's no data.
    Shared by the shipment and master-data importers."""
    name = (getattr(file_obj, "name", "") or "").lower()
    if name.endswith((".xlsx", ".xls")):
        import openpyxl
        wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
        rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
    else:
        import csv as _csv
        import io as _io
        raw = file_obj.read().decode("utf-8-sig", errors="replace")
        head = raw[:4000]
        delim = ";" if head.count(";") >= head.count(",") else ","
        rows = list(_csv.reader(_io.StringIO(raw), delimiter=delim))
    rows = [r for r in rows if any(str(c).strip() for c in (r or []))]
    if not rows:
        return [], []
    header = [str(h).strip().lower() for h in rows[0]]
    return header, rows[1:]

def _read_xlsx_as_dicts(file_obj, row_numbers=False):
    """Read xlsx: row 1=headers, row 2=descriptions (skipped), row 3+=data. Returns list of dicts.

    row_numbers=True dokłada klucz ``"_row"`` = numer wiersza w arkuszu (1-based) —
    do czytelnych komunikatów importu („wiersz 7, kolumna …”)."""
    import openpyxl
    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        return []
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    # Map column index → header, skipping blank headers and keeping the FIRST of any
    # duplicate header — otherwise a trailing-blank or repeated column silently
    # clobbers a real column's data.
    col_header = {}
    seen_headers = set()
    for i, h in enumerate(rows[0]):
        name = str(h or "").strip()
        if not name or name in seen_headers:
            continue
        seen_headers.add(name)
        col_header[i] = name
    # Nasz szablon ma w 2. wierszu OPISY kolumn (italic, sam tekst). Pomijamy go —
    # ale TYLKO gdy to naprawdę wiersz opisu (sam tekst), inaczej plik zbudowany bez
    # wiersza opisu (nagłówek + dane od 2. wiersza) gubiłby po cichu 1. rekord.
    first = 2
    if rows[1:] and _looks_like_description_row(rows[1]):
        first = 3
    result = []
    for sheet_row, row in enumerate(rows[first - 1:], start=first):
        d = {col_header[i]: (str(v).strip() if v is not None else "")
             for i, v in enumerate(row) if i in col_header}
        if any(v for v in d.values()):  # skip fully empty rows
            if row_numbers:
                d["_row"] = sheet_row
            result.append(d)
    return result


def _looks_like_description_row(row):
    """Wiersz opisu szablonu = same etykiety tekstowe, żadnej liczby. Realny wiersz danych
    master-daty zawsze ma ≥1 komórkę liczbową (wymiar/ilość/waga) → nie zostanie pomylony."""
    has_text = False
    for v in row:
        if v is None or str(v).strip() == "":
            continue
        try:
            float(str(v).strip().replace(",", "."))
            return False          # komórka liczbowa → to dane, nie opis
        except ValueError:
            has_text = True
    return has_text

def _make_xlsx_response(filename: str):
    """Return (workbook, worksheet, HttpResponse) ready for styled xlsx export."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return wb, ws, response

def _style_xlsx_header(ws, columns, title_color="1A56DB"):
    """
    columns: list of (header, description, width, example_value)
    Row 1 = column names (bold, coloured)
    Row 2 = descriptions (italic grey)
    Row 3+ = example data
    """
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side

    hdr_fill   = PatternFill("solid", fgColor=title_color)
    desc_fill  = PatternFill("solid", fgColor="F1F5F9")
    hdr_font   = Font(bold=True, color="FFFFFF", size=10)
    desc_font  = Font(italic=True, color="64748B", size=9)
    example_font = Font(color="374151", size=10)
    thin = Side(style="thin", color="CBD5E1")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for col_idx, (name, desc, width, _) in enumerate(columns, 1):
        # Header
        c1 = ws.cell(row=1, column=col_idx, value=name)
        c1.fill = hdr_fill; c1.font = hdr_font
        c1.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c1.border = border
        # Description
        c2 = ws.cell(row=2, column=col_idx, value=desc)
        c2.fill = desc_fill; c2.font = desc_font
        c2.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        c2.border = border
        # Column width
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width

    ws.row_dimensions[1].height = 28
    ws.row_dimensions[2].height = 42
    ws.freeze_panes = "A3"

def _add_example_rows(ws, columns, rows):
    """Add example data rows starting at row 3."""
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

    even_fill = PatternFill("solid", fgColor="F8FAFC")
    thin = Side(style="thin", color="CBD5E1")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for row_idx, row_data in enumerate(rows, 3):
        fill = even_fill if row_idx % 2 == 0 else None
        for col_idx, value in enumerate(row_data, 1):
            c = ws.cell(row=row_idx, column=col_idx, value=value)
            c.font = Font(size=10)
            c.alignment = Alignment(vertical="center")
            c.border = border
            if fill:
                c.fill = fill
        ws.row_dimensions[row_idx].height = 18

# SEC-007: neutralizacja formuł (CSV/formula injection). Tekst z danych (nazwa klienta,
# odbiorcy, REF…) zaczynający się od tych znaków Excel potraktuje jak formułę.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(v):
    """Tekst zaczynający się od = + - @ TAB CR → prefiks `'`. Liczby (też ujemne) bez zmian."""
    if isinstance(v, str) and v.startswith(_FORMULA_PREFIXES):
        return "'" + v
    return v


class _SafeCsvWriter:
    def __init__(self, f, **kw):
        import csv
        self._w = csv.writer(f, **kw)

    def writerow(self, row):
        return self._w.writerow([safe_cell(v) for v in row])

    def writerows(self, rows):
        for r in rows:
            self.writerow(r)


def safe_csv_writer(f, **kw):
    """Zamiennik csv.writer dla eksportów z danymi użytkowników — każda komórka przez safe_cell."""
    return _SafeCsvWriter(f, **kw)


def neutralize_workbook(wb):
    """Przed wb.save(): komórki, które openpyxl uznał za formułę (tekst od '='), dostają `'`.
    Tylko formuły — w XLSX zwykły tekst od '-'/'+' nie jest liczony, więc go nie ruszamy."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.data_type == "f" and isinstance(c.value, str):
                    c.value = safe_cell(c.value)
    return wb


def _finalize_xlsx(wb, ws, response):
    import io
    neutralize_workbook(wb)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    response.content = buf.read()
    return response

def _detect_column(headers, keywords):
    """Return index of first header matching any keyword (case-insensitive), or None."""
    lowers = [h.lower() for h in headers]
    for kw in keywords:
        kw_l = kw.lower()
        for i, h in enumerate(lowers):
            if kw_l in h:
                return i
    return None

__all__ = [
    '_read_table',
    '_read_xlsx_as_dicts',
    '_xlsx_float',
    '_xlsx_int',
    '_make_xlsx_response',
    '_style_xlsx_header',
    '_add_example_rows',
    '_finalize_xlsx',
    '_detect_column',
    'safe_cell',
    'safe_csv_writer',
    'neutralize_workbook',
]
