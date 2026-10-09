# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from ui.views.core import (
    _aggregate_stats, _any_role, _build_activity_qs, _md_role,
    get_object_or_404, JsonResponse, messages,
    PickerActivity, PickerActivityBatch, redirect, render, require_POST,
    WarehouseLayout,
)
from ui.views.core.helpers import _parse_loc_code

from . import heatmap_upload_parse as _upload_parse

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_BULK_CHUNK = 500


def _loc_canon(code):
    """Kanoniczny klucz lokalizacji do JOINU aktywność↔komórka: parsuje kod (po upper) do
    krotki (aisle,stack,col,idx,level). Godzi 3-częściowe kody SAP (B0-01-100A) z
    4-częściowymi buildera (B0-01-100-1A) — obie parsują do tej samej krotki. Fallback:
    znormalizowany string, gdy kod nie parsuje się."""
    up = (code or "").strip().upper()
    return _parse_loc_code(up) or up


@_any_role
def heatmap_list(request):
    batches = PickerActivityBatch.objects.all()
    return render(request, "ui/heatmap/list.html", {"batches": batches})


def _upload_error(request, message):
    messages.error(request, message)
    return redirect("ui:heatmap_list")


def _read_upload(f):
    """``((nagłówki, wiersze), None)`` albo ``(None, komunikat błędu)``."""
    if not f:
        return None, "Nie wybrano pliku."
    if f.size > _MAX_UPLOAD_BYTES:
        return None, "Plik jest zbyt duży (max 20 MB)."
    try:
        table = _upload_parse.read_table(f)
    except Exception as e:
        return None, f"Błąd odczytu pliku: {e}"
    if table is None:
        return None, "Plik jest pusty."
    return table, None


class _SaveError(Exception):
    """Błąd bulk_create — oryginalny wyjątek w ``__cause__``."""


def _bulk_insert(activities):
    try:
        PickerActivity.objects.bulk_create(activities)
    except Exception as exc:
        raise _SaveError from exc


def _parse_rows(data_rows, cols):
    """Pola ``PickerActivity`` poprawnych wierszy (pozostałe pomijane)."""
    parsed = (_upload_parse.parse_row(row, cols) for row in data_rows)
    return [fields for fields in parsed if fields is not None]


def _save_activities(batch, parsed_rows):
    """Zapisuje aktywności paczkami po 500; zwraca daty potwierdzeń zapisanych wierszy."""
    activities = []
    dates_seen = []
    for fields in parsed_rows:
        dates_seen.append(fields["confirmed_at"].date())
        activities.append(PickerActivity(batch=batch, **fields))
        if len(activities) >= _BULK_CHUNK:
            _bulk_insert(activities)
            activities.clear()
    if activities:
        _bulk_insert(activities)
    return dates_seen


def _finalize_batch(batch, dates_seen):
    batch.row_count = batch.activities.count()
    if dates_seen:
        batch.date_from = min(dates_seen)
        batch.date_to   = max(dates_seen)
    batch.save(update_fields=["row_count", "date_from", "date_to"])


@_md_role          # writing (bulk-insert) — match heatmap_delete; a read-only viewer/customer
@require_POST       # role must not be able to import thousands of PickerActivity rows.
def heatmap_upload(request):
    f = request.FILES.get("file")
    name = request.POST.get("name", "").strip() or (f.name if f else "Import")
    name = name[:PickerActivityBatch._meta.get_field("name").max_length]
    table, error = _read_upload(f)
    if error:
        return _upload_error(request, error)
    headers, data_rows = table
    cols = _upload_parse.detect_columns(headers)
    error = _upload_parse.column_error(cols, data_rows)
    if error:
        return _upload_error(request, error)

    parsed_rows = _parse_rows(data_rows, cols)
    if not parsed_rows:
        return _upload_error(request, "Nie znaleziono poprawnych wierszy (lokalizacja + "
                                      "data) — nic nie zaimportowano.")

    batch = PickerActivityBatch.objects.create(name=name)
    try:
        dates_seen = _save_activities(batch, parsed_rows)
    except _SaveError as err:
        batch.delete()
        return _upload_error(request, f"Błąd zapisu danych: {err.__cause__}")
    _finalize_batch(batch, dates_seen)

    messages.success(request, f"Zaimportowano {batch.row_count} wierszy.")
    return redirect("ui:heatmap_detail", pk=batch.pk)

@_any_role
def heatmap_detail(request, pk):
    batch = get_object_or_404(PickerActivityBatch, pk=pk)
    layout = WarehouseLayout.objects.filter(is_active=True).first()

    qs = _build_activity_qs(batch, request.GET)

    from django.db.models import Count as _Count
    # Klucz znormalizowany (case/spacje) — kody aktywności (SAP) i komórek layoutu pochodzą
    # z różnych źródeł; bez tego różnica wielkości liter dawała pustą (zimną) mapę mimo aktywności.
    _ln = _loc_canon        # klucz kanoniczny (case + 3/4-czesciowe kody)
    loc_counts = {}
    for code, c in qs.values_list("location_code").annotate(c=_Count("id")).values_list("location_code", "c"):
        loc_counts[_ln(code)] = loc_counts.get(_ln(code), 0) + c

    cells = []
    max_count = max(loc_counts.values(), default=1) or 1
    if layout:
        for cell in layout.cells.all():
            cnt = loc_counts.get(_ln(cell.location_code), 0)
            level = 0
            if cnt > 0:
                level = min(7, int(cnt / max_count * 7) + 1)
            cells.append({
                "location_code": cell.location_code,
                "grid_row": cell.grid_row,
                "grid_col": cell.grid_col,
                "count": cnt,
                "level": level,
            })

    hourly, weekly, top_locs, top_pickers = _aggregate_stats(qs)

    pickers = list(batch.activities.values_list("picker_name", flat=True).distinct().order_by("picker_name"))
    pickers = [p for p in pickers if p]
    task_types = list(batch.activities.values_list("task_type", flat=True).distinct().order_by("task_type"))
    task_types = [t for t in task_types if t]

    total = sum(loc_counts.values())
    active_locs = len([c for c in loc_counts.values() if c > 0])
    top_picker = top_pickers[0][0] if top_pickers else "—"
    peak_hour_data = max(hourly, key=lambda x: x[1]) if hourly else [0, 0]
    peak_hour = f"{peak_hour_data[0]:02d}:00"

    return render(request, "ui/heatmap/detail.html", {
        "batch": batch,
        "layout": layout,
        # raw objects — json_script encodes once (json.dumps here would double-encode → JSON.parse string)
        "layout_cells_json": cells,
        "max_count": max_count,
        "pickers": pickers,
        "task_types": task_types,
        "filter_date_from": request.GET.get("date_from", ""),
        "filter_date_to":   request.GET.get("date_to", ""),
        "filter_picker":    request.GET.get("picker", ""),
        "filter_task_type": request.GET.get("task_type", ""),
        "filter_hour_from": request.GET.get("hour_from", ""),
        "filter_hour_to":   request.GET.get("hour_to", ""),
        "stats_total": total,
        "stats_active_locs": active_locs,
        "stats_top_picker": top_picker,
        "stats_peak_hour": peak_hour,
        "hourly_json": hourly,
        "weekly_json": weekly,
        "top_locs_json": [[loc, cnt] for loc, cnt in top_locs],
        "pickers_json": [[p, c] for p, c in top_pickers],
    })

@_any_role
def heatmap_data_json(request, pk):
    batch = get_object_or_404(PickerActivityBatch, pk=pk)
    layout = WarehouseLayout.objects.filter(is_active=True).first()

    qs = _build_activity_qs(batch, request.GET)

    from django.db.models import Count as _Count
    _ln = _loc_canon        # klucz kanoniczny (case + 3/4-czesciowe kody)
    loc_counts = {}
    for code, c in qs.values_list("location_code").annotate(c=_Count("id")).values_list("location_code", "c"):
        loc_counts[_ln(code)] = loc_counts.get(_ln(code), 0) + c

    max_count = max(loc_counts.values(), default=1) or 1
    cells = []
    if layout:
        for cell in layout.cells.all():
            cnt = loc_counts.get(_ln(cell.location_code), 0)
            cells.append({"code": cell.location_code, "row": cell.grid_row, "col": cell.grid_col, "count": cnt})

    hourly, weekly, top_locs, top_pickers = _aggregate_stats(qs)

    return JsonResponse({
        "cells": cells,
        "max_count": max_count,
        "hourly": hourly,
        "weekly": weekly,
        "top_locs": [[loc, cnt] for loc, cnt in top_locs],
        "pickers": [[p, c] for p, c in top_pickers],
    })

@_md_role
@require_POST
def heatmap_delete(request, pk):
    batch = get_object_or_404(PickerActivityBatch, pk=pk)
    batch.delete()
    messages.success(request, "Import usunięty.")
    return redirect("ui:heatmap_list")

__all__ = [
    'heatmap_list',
    'heatmap_upload',
    'heatmap_detail',
    'heatmap_data_json',
    'heatmap_delete',
]
