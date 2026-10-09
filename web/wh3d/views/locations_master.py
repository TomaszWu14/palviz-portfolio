# Master-data location browser: filterable (django-filter) + sortable/paginated
# (django-tables2), htmx partial refresh, and XLSX (XlsxWriter) / PDF (WeasyPrint) export.
from ui.views.core import _md_role, render, render_to_string

import io

from django.http import HttpResponse
from django_tables2 import RequestConfig

from ui.tables import LocationMasterTable
from ui.filters import LocationMasterFilter
from wh3d.locations import active_master_qs as _active_master_qs  # alias: stara nazwa


def _filtered(request):
    return LocationMasterFilter(request.GET, queryset=_active_master_qs())


def _stats(qs):
    """Quality overview of the filtered locations: numpy for numeric aggregates,
    polars for the per-type / half-vs-full breakdown. Empty-safe."""
    import numpy as np
    rows = list(qs.values_list("width_mm", "max_volume_m3", "warehouse_type"))
    if not rows:
        return None
    widths = np.array([r[0] for r in rows], dtype=float)
    vols   = np.array([r[1] for r in rows], dtype=float)
    half   = int((widths <= 500).sum())
    stats = {
        "count": len(rows),
        "half_slots": half,
        "full_slots": len(rows) - half,
        "total_volume_m3": round(float(vols.sum()), 2),
        "avg_width_mm": int(round(float(widths.mean()))),
        "median_volume_m3": round(float(np.median(vols)), 3),
    }
    try:
        import polars as pl
        by_type = (pl.DataFrame({"t": [r[2] or "—" for r in rows]})
                   .group_by("t").len().sort("len", descending=True))
        stats["by_type"] = [(r["t"], r["len"]) for r in by_type.head(6).to_dicts()]
    except Exception:
        stats["by_type"] = []
    return stats


@_md_role
def location_master_browse(request):
    """Filterable/sortable master-data table. Returns only the table partial to htmx."""
    f = _filtered(request)
    table = LocationMasterTable(f.qs)
    RequestConfig(request, paginate={"per_page": 50}).configure(table)
    # django-htmx: a filter/sort/paginate request swaps just the table, not the whole page.
    if getattr(request, "htmx", False):
        return render(request, "ui/locations_master/_table.html", {"table": table})
    return render(request, "ui/locations_master/browse.html",
                  {"table": table, "filter": f, "total": f.qs.count(), "stats": _stats(f.qs)})


_EXPORT_COLS = [
    ("location_code", "Lokalizacja"), ("level", "Poziom"), ("warehouse_type", "Typ magazynu"),
    ("width_mm", "Szer. [mm]"), ("depth_mm", "Głęb. [mm]"), ("height_mm", "Wys. [mm]"),
    ("max_volume_m3", "Obj. [m³]"), ("max_weight_kg", "Waga [kg]"),
]


@_md_role
def location_master_export_xlsx(request):
    """Export the filtered locations to a formatted .xlsx (XlsxWriter engine)."""
    import xlsxwriter

    rows = list(_filtered(request).qs.values_list(*[c for c, _ in _EXPORT_COLS]))
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet("Lokalizacje")
    head = wb.add_format({"bold": True, "bg_color": "#1E40AF", "font_color": "white", "border": 1})
    cell = wb.add_format({"border": 1})
    for col, (_, title) in enumerate(_EXPORT_COLS):
        ws.write(0, col, title, head)
    for r, row in enumerate(rows, start=1):
        for col, val in enumerate(row):
            ws.write(r, col, val, cell)
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, len(rows), len(_EXPORT_COLS) - 1)
    ws.set_column(0, 0, 22)
    wb.close()
    buf.seek(0)
    resp = HttpResponse(buf.read(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="lokalizacje_master.xlsx"'
    return resp


@_md_role
def location_master_export_pdf(request):
    """Export the filtered locations to a PDF (WeasyPrint, imported lazily)."""
    rows = list(_filtered(request).qs.values_list(*[c for c, _ in _EXPORT_COLS]))
    # request=request → branding/app_name context processor populates {{ app_name }}.
    html = render_to_string("ui/locations_master/pdf.html", {
        "rows": rows, "columns": [t for _, t in _EXPORT_COLS], "total": len(rows),
    }, request=request)
    try:
        from weasyprint import HTML
        pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    except Exception:
        # WeasyPrint needs native libs (Pango/Cairo); degrade to printable HTML if absent.
        return HttpResponse(html)
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = 'inline; filename="lokalizacje_master.pdf"'
    return resp


__all__ = [
    'location_master_browse',
    'location_master_export_xlsx',
    'location_master_export_pdf',
    '_active_master_qs',  # alias zgodności; kanon: wh3d.locations.active_master_qs (ARCH-001)
]
