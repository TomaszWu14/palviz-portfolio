# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from ui.views.core import (
    _md_role, get_object_or_404, messages, redirect, require_POST, transaction,
    WarehouseLayout, WarehouseLayoutCell, WarehouseLocationMaster,
    WarehouseLocationMasterBatch, WarehouseRackType,
)
from ui.views.core.ewm_levels import letter_level

from . import warehouse_combined_parse as combined_parse


def _code_zone_letter(code):
    """„B0-07-300C-1” → („B0”, „C”); kod bez litery → („B0”, „”)."""
    parts = (code or "").strip().upper().split("-")
    p2 = parts[2] if len(parts) > 2 else ""
    return parts[0], (p2[-1] if p2[-1:].isalpha() else "")


@_md_role
@require_POST
@transaction.atomic   # deactivate-others + bulk import is all-or-nothing
def warehouse_master_upload(request):
    """Upload location master data from Excel with columns:
    Adres lokalizacji | Poziom | Typ magazynu | Wysokość [mm] | Max objętość [m³] | Max waga [kg]
    """
    import openpyxl as _openpyxl

    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Brak pliku.")
        return redirect("ui:warehouse_map")
    if f.size > 10 * 1024 * 1024:
        messages.error(request, "Plik zbyt duży (max 10 MB).")
        return redirect("ui:warehouse_map")

    name = request.POST.get("name", "").strip() or f.name

    try:
        wb = _openpyxl.load_workbook(f, read_only=True, data_only=True)
        ws = wb.active
    except Exception as exc:
        messages.error(request, f"Błąd odczytu pliku: {exc}")
        return redirect("ui:warehouse_map")

    # Detect header row — find row where any cell looks like a location header keyword
    _HDR_KEYS = {"adres", "miejsce", "lokalizacj", "location"}
    # Supports direct SAP WMS export (Polish) and custom template headers
    _COL_ALIASES = {
        "location_code": [
            "miejsce składowania", "miejsce sklad", "adres lokalizacji",
            "location_code", "location", "bin", "lokalizacja",
        ],
        "level": [
            "poziom miejsca skł", "poziom miejsca skl", "poziom miejsca",
            "poziom", "level",
        ],
        "warehouse_type": [
            "typ magazynu", "warehouse_type", "warehouse type",
            "typ mag", "rack_type", "typ", "type",
        ],
        # SAP "Całkowite zdolności" = total slot capacity in mm (= height clearance)
        "height_mm": [
            "całkowite zdolności", "calkowite zdolnosci",
            "całkowite zdolno", "calkowite zdolno",
            "wysokość", "wysokosc", "height_mm", "height", "wys", "clearance",
        ],
        # SAP "Maksymalna objętość" — value in m³ with comma decimal, e.g. "2,100"
        "max_volume_m3": [
            "maksymalna objętość", "maksymalna objetosc",
            "max_volume_m3", "objętość", "objetosc", "volume", "obj",
        ],
        # SAP "Maksymalna waga" — value in kg
        "max_weight_kg": [
            "maksymalna waga", "max_weight_kg", "max waga",
            "waga", "weight", "max weight",
        ],
        # SAP blocked flags — stored for reference (not in model yet, silently ignored)
        "_blocked_pick": ["blok. wyd", "blokada wyd", "blocked_pick"],
        "_blocked_put":  ["blokada um", "blocked_put"],
    }

    header_row_idx = None
    col_map = {}  # field_name → 0-based column index
    rows_iter = ws.iter_rows(values_only=True)
    raw_rows = []
    for row in rows_iter:
        raw_rows.append(row)
        if header_row_idx is None:
            cells = [str(c).lower().strip() if c else "" for c in row]
            if any(any(k in c for k in _HDR_KEYS) for c in cells):
                header_row_idx = len(raw_rows) - 1
                for field, aliases in _COL_ALIASES.items():
                    for i, cell in enumerate(cells):
                        if any(alias in cell for alias in aliases):
                            col_map[field] = i
                            break
                # header found — keep consuming the rest of the rows (do NOT break,
                # or raw_rows[header_row_idx+1:] would be empty → 0 rows imported)

    if "location_code" not in col_map:
        messages.error(request, "Nie znaleziono kolumny z adresem lokalizacji. Sprawdź nagłówki.")
        return redirect("ui:warehouse_map")

    batch = WarehouseLocationMasterBatch.objects.create(name=name)
    WarehouseLocationMasterBatch.objects.exclude(pk=batch.pk).update(is_active=False)

    def _clean(v):
        """Normalize SAP number strings: '2 350,000' → '2350.000', '2,100' → '2.100'."""
        s = str(v).strip().replace("\xa0", "").replace(" ", "")  # remove nbsp + space (thousands sep)
        s = s.replace(",", ".")
        return s

    def _int(v):
        try:
            return int(float(_clean(v)))
        except Exception:
            return 0

    def _float(v):
        try:
            return float(_clean(v))
        except Exception:
            return 0.0

    def _get(row, field, default=None):
        idx = col_map.get(field)
        if idx is None or idx >= len(row):
            return default
        return row[idx]

    def _bool(v):
        """Blokada SAP: „X"/„Tak"/„1" = zablokowane; puste = nie."""
        return str(v).strip().lower() in ("x", "tak", "yes", "true", "1") if v else False

    bulk = []
    seen = set()
    for row in raw_rows[header_row_idx + 1:]:
        if not row:
            continue
        loc_raw = _get(row, "location_code")
        loc = str(loc_raw).strip() if loc_raw else ""
        if not loc or loc in seen or loc.lower() in ("none", "nan"):
            continue
        seen.add(loc)
        # Poziom z litery kodu EWM — kolumna „Poziom miejsca skł.” bywa błędna (C-1…D-2).
        level = letter_level(*_code_zone_letter(loc)) or _int(_get(row, "level", 1))
        bulk.append(WarehouseLocationMaster(
            batch=batch,
            location_code=loc,
            level=level,
            warehouse_type=str(_get(row, "warehouse_type", "") or "").strip(),
            height_mm=_int(_get(row, "height_mm", 0)),
            max_volume_m3=_float(_get(row, "max_volume_m3", 0.0)),
            max_weight_kg=_float(_get(row, "max_weight_kg", 0.0)),
            blocked_pick=_bool(_get(row, "_blocked_pick")),
            blocked_put=_bool(_get(row, "_blocked_put")),
        ))
        if len(bulk) >= 2000:
            WarehouseLocationMaster.objects.bulk_create(bulk, ignore_conflicts=True)
            bulk = []

    if bulk:
        WarehouseLocationMaster.objects.bulk_create(bulk, ignore_conflicts=True)

    batch.location_count = batch.locations.count()
    batch.save()

    messages.success(request, f"Master data wgrane: {batch.location_count} lokalizacji.")
    return redirect("ui:warehouse_map")

@_md_role
@require_POST
def warehouse_master_delete(request, pk):
    get_object_or_404(WarehouseLocationMasterBatch, pk=pk).delete()
    messages.success(request, "Master data usunięte.")
    return redirect("ui:warehouse_map")

def _combined_read_rows(request, f):
    """Plik → wiersze albo ``None`` (komunikat błędu już dodany).
    CSV czytany poza try — jego błędy propagują się (obecne zachowanie)."""
    if f.name.lower().endswith(".csv"):
        return combined_parse.read_csv_rows(f)
    try:
        return combined_parse.read_xlsx_rows(f)
    except Exception as exc:
        messages.error(request, f"Błąd odczytu pliku: {exc}")
        return None


def _combined_parse_upload(request):
    """Walidacja pliku + parsowanie. Zwraca listę lokalizacji albo ``None``
    (komunikat błędu już dodany)."""
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Brak pliku.")
        return None
    if f.size > 30 * 1024 * 1024:
        messages.error(request, "Plik jest zbyt duży (max 30 MB).")
        return None
    rows = _combined_read_rows(request, f)
    if rows is None:
        return None
    if not rows:
        messages.error(request, "Plik jest pusty.")
        return None
    header_idx, col_map = combined_parse.detect_header(rows)
    if "location_code" not in col_map:
        messages.error(request, "Nie znaleziono kolumny z kodem lokalizacji. Sprawdź nagłówki.")
        return None
    parsed = combined_parse.parse_rows(rows[header_idx + 1:], col_map)
    if not parsed:
        messages.error(request, "Brak danych do wgrania.")
        return None
    return parsed


def _combined_active_batch():
    active_batch = WarehouseLocationMasterBatch.objects.filter(is_active=True).first()
    if active_batch is None:
        active_batch = WarehouseLocationMasterBatch.objects.create(name="Combined upload")
        active_batch.is_active = True
        active_batch.save()
    return active_batch


def _combined_upsert_rack_types(parsed):
    """Typy regałów z wymiarami z importu → liczba NOWYCH typów."""
    created_types = 0
    for code, defaults in combined_parse.rack_type_defaults(parsed).items():
        obj, created = WarehouseRackType.objects.get_or_create(
            code=code, defaults={**defaults, "name": code}
        )
        if not created and defaults:
            # Only update dimension fields — never overwrite the human-readable name
            for field, val in defaults.items():
                setattr(obj, field, val)
            obj.save(update_fields=list(defaults.keys()))
        if created:
            created_types += 1
    return created_types


def _combined_upsert_cells(layout, parsed):
    """Komórki layoutu → ``(nowe, zaktualizowane)``; odświeża licznik layoutu."""
    aisle_ranks, stack_ranks = combined_parse.grid_ranks(parsed)
    created_cells = updated_cells = 0
    for p in parsed:
        grid_col, grid_row = combined_parse.grid_position(p, aisle_ranks, stack_ranks)
        _, created = WarehouseLayoutCell.objects.update_or_create(
            layout=layout,
            location_code=p["loc"],
            defaults={"grid_col": grid_col, "grid_row": grid_row, "level": p["level"]},
        )
        if created:
            created_cells += 1
        else:
            updated_cells += 1
    layout.location_count = layout.cells.count()
    layout.save()
    return created_cells, updated_cells


def _combined_upsert_masters(active_batch, parsed):
    """Rekordy master → ``(nowe, zaktualizowane)``; odświeża licznik batcha."""
    # Słownik typów regałów — surowy eksport EWM nie niesie wymiarów, więc
    # uzupełniamy je z WarehouseRackType po kodzie typu (kind=rack; strefy
    # celowo bez geometrii). Jeden odczyt na cały upload, nie per wiersz.
    rack_types = {t.code: t for t in WarehouseRackType.objects.all()}
    created_masters = updated_masters = 0
    for p in parsed:
        _, created = WarehouseLocationMaster.objects.update_or_create(
            batch=active_batch,
            location_code=p["loc"],
            defaults=combined_parse.master_defaults(p, rack_types),
        )
        if created:
            created_masters += 1
        else:
            updated_masters += 1
    active_batch.location_count = active_batch.locations.count()
    active_batch.save()
    return created_masters, updated_masters


@_md_role
@require_POST
@transaction.atomic   # all-or-nothing: rack types were committed before the inner atomic
                      # block, so a later failure left orphaned WarehouseRackType rows.
def warehouse_combined_upload(request, layout_pk):
    """Combined single-file upload: creates/updates WarehouseLayoutCell + WarehouseLocationMaster + WarehouseRackType."""
    layout = get_object_or_404(WarehouseLayout, pk=layout_pk)
    parsed = _combined_parse_upload(request)
    if parsed is None:
        return redirect("ui:warehouse_map")

    active_batch = _combined_active_batch()
    created_types = _combined_upsert_rack_types(parsed)
    with transaction.atomic():
        created_cells, updated_cells = _combined_upsert_cells(layout, parsed)
        created_masters, updated_masters = _combined_upsert_masters(active_batch, parsed)

    messages.success(
        request,
        f"Kombinowany upload: "
        f"{created_cells} nowych / {updated_cells} zaktualizowanych lokalizacji w layoucie, "
        f"{created_masters} nowych / {updated_masters} zaktualizowanych rekordów master, "
        f"{created_types} typów regałów."
    )
    return redirect("ui:warehouse_map")

__all__ = [
    'warehouse_master_upload',
    'warehouse_master_delete',
    'warehouse_combined_upload',
]
