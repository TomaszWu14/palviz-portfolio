# Import przesylek z Excela + szablon linii.
from ui.views.core import (
    Customer, Product, Shipment, ShipmentLine, _add_example_rows,
    _finalize_xlsx, _make_xlsx_response, _read_table, _style_xlsx_header,
    _transport_mgr, messages, redirect, require_POST, transaction,
)
import logging

from . import shipments_import_parse as parse

logger = logging.getLogger(__name__)


@_transport_mgr
def excel_template_shipment_lines(request):
    wb, ws, response = _make_xlsx_response("PalViz_linie_przesylki_wzor.xlsx")
    ws.title = "Linie przesyłki"
    cols = [
        ("product_code", "Kod produktu / SKU z PalViz",               18, "SKU-001"),
        ("quantity",     "Ilość (wartość liczbowa)",                    12, 100),
        ("unit",         "Jednostka: szt / kar / pal",                  12, "kar"),
        ("notes",        "Uwagi do linii (opcjonalnie)",                24, ""),
    ]
    _style_xlsx_header(ws, cols, "BE185D")
    rows = [
        ["SKU-001",100,"kar","Zamówienie klienta A"],
        ["SKU-002",50,"kar",""],
        ["SKU-003",2,"pal","Pełne palety"],
        ["SKU-004",500,"szt","Sztuki luzem"],
    ]
    _add_example_rows(ws, cols, rows)
    # Units reference
    from openpyxl.styles import Font
    for i, (unit, desc) in enumerate([("szt","Sztuki produktu"),("kar","Kartony zbiorcze"),("pal","Palety")], 1):
        ws.cell(row=len(rows)+3+i, column=1, value=unit).font = Font(bold=True, color="BE185D", size=10)
        ws.cell(row=len(rows)+3+i, column=2, value=desc).font = Font(size=10)
    return _finalize_xlsx(wb, ws, response)


_MAX_FILE_SIZE = 10 * 1024 * 1024


def _uploaded_files(request):
    """Wgrane pliki albo (None, komunikat błędu) — bez pliku / plik > 10 MB."""
    files = request.FILES.getlist("file") or ([request.FILES["file"]] if "file" in request.FILES else [])
    if not files:
        return None, "Nie wybrano pliku."
    for f in files:
        if f.size > _MAX_FILE_SIZE:
            return None, f"Plik „{f.name}” zbyt duży (max 10 MB)."
    return files, None


def _transport_options(request):
    """(konsolidacja, tryby transportu CSV, tryb załadunku) wybrane w formularzu."""
    consolidate = request.POST.get("consolidate") in ("on", "1", "true")
    # Transport type(s) chosen up front (multi-select): what we'll calculate/quote against.
    from palletizer.services.vehicle_load import VEHICLES
    veh_keys = {v["key"] for v in VEHICLES}
    modes = ",".join(m for m in request.POST.getlist("modes") if m in veh_keys)
    load_mode = "loose" if request.POST.get("load_mode") == "loose" else "pallets"
    return consolidate, modes, load_mode


def _read_records(files):
    """Każdy plik → rekordy (kolumny wykrywane per plik, więc różne układy się łączą)."""
    records = []
    for f in files:
        header, body = _read_table(f)
        if body:
            records.extend(parse.file_records(header, body))
    return records


def _preload(records):
    """Produkty po kodzie + istniejący klienci po numerze odbiorcy (hurtowo)."""
    all_codes = {rec["prod"] for rec in records if rec["prod"]}
    prod_by_code = {p.code: p for p in Product.objects.filter(code__in=all_codes)}
    recip_nos = {no for no in (parse.recipient_no(rec["recip_no"]) for rec in records) if no}
    cust_by_code = ({c.code: c for c in Customer.objects.filter(code__in=recip_nos)}
                    if recip_nos else {})
    return prod_by_code, cust_by_code


def _resolve_customer(rs, recip_no, recip_nm, cust_by_code, stats):
    """Podpina istniejącego odbiorcę albo tworzy go (kind=consignee); None bez numeru."""
    if not recip_no:
        return None
    customer = cust_by_code.get(recip_no)
    if customer is None:
        customer, was_created = Customer.objects.get_or_create(
            code=recip_no, kind="consignee",
            defaults={"name": (recip_nm or recip_no)[:200],
                      "city": parse.first(rs, "city")[:100],
                      "postal": parse.first(rs, "postal")[:20]})
        cust_by_code[recip_no] = customer
        stats["created_customers"] += 1 if was_created else 0
    stats["linked"] += 1
    return customer


def _create_shipment(rs, agg, ctx, stats):
    """Zapisuje jedną przesyłkę grupy wraz z liniami."""
    first = parse.first
    recip_no, recip_nm = parse.recipient_fields(rs)
    customer = _resolve_customer(rs, recip_no, recip_nm, ctx["cust_by_code"], stats)
    name, notes = parse.shipment_name_notes(rs, ctx["consolidate"], recip_nm, recip_no,
                                            max_len=Shipment._meta.get_field("name").max_length)
    country, bad_country = parse.country_code(first(rs, "country"))
    if bad_country:
        ctx["bad_countries"].add(bad_country)
    shipment = Shipment.objects.create(
        name=name, status="draft", notes=notes,
        author=first(rs, "author")[:120], author_email=first(rs, "mail")[:200],
        recipient_name=recip_nm, customer=customer,
        client_requirements=first(rs, "req")[:300],
        actual_hu_count=parse.actual_hu_count(rs),
        transport_modes=ctx["modes"], load_mode=ctx["load_mode"],
        destination_country=country,
        destination_city=first(rs, "city")[:100],
        destination_postal=first(rs, "postal")[:20])
    for order, (code, (qty, unit, src)) in enumerate(agg.items()):
        ShipmentLine.objects.create(
            shipment=shipment, product=ctx["prod_by_code"][code],
            quantity=qty, unit=unit, source_unit=src, order=order)
        stats["lines"] += 1
    stats["shipments"] += 1


def _import_groups(grouped, ctx, missing):
    """Tworzy przesyłki dla grup (atomowo); zwraca liczniki do komunikatu."""
    stats = {"shipments": 0, "lines": 0, "created_customers": 0, "linked": 0}
    with transaction.atomic():
        for rs in grouped.values():
            agg = parse.aggregate_lines(rs, ctx["prod_by_code"], missing)
            if agg:
                _create_shipment(rs, agg, ctx, stats)
    return stats


@_transport_mgr
@require_POST
def planner_shipments_import(request):
    """Import shipments from one or more files.

    Columns (auto-detected, any order): Dokument, Produkt (kod/SKU), Ilość, JS (jednostka),
    odbiorca (nr + nazwa), miasto, kod pocztowy, autor/e-mail, oraz "Wymagania klienta".
    Domyślnie tworzy jedną przesyłkę na numer dokumentu. Z opcją „Konsoliduj po odbiorcy"
    łączy WSZYSTKIE wgrane dokumenty trafiające do tego samego odbiorcy w jedną przesyłkę
    (sumując ilości per produkt) — pod konsolidacje wysyłkowe."""
    files, error = _uploaded_files(request)
    if error:
        messages.error(request, error)
        return redirect("ui:planner_shipments")
    consolidate, modes, load_mode = _transport_options(request)
    try:
        records = _read_records(files)
        if not records:
            messages.error(request, "Pliki nie zawierają danych.")
            return redirect("ui:planner_shipments")
        prod_by_code, cust_by_code = _preload(records)
        ctx = {"consolidate": consolidate, "modes": modes, "load_mode": load_mode,
               "prod_by_code": prod_by_code, "cust_by_code": cust_by_code,
               "bad_countries": set()}
        missing = set()
        stats = _import_groups(parse.group_records(records, consolidate), ctx, missing)
        messages.success(request, parse.summary_message(
            stats, consolidate, len(files), missing, ctx["bad_countries"]))
    except Exception:
        logger.exception("Import przesyłek z Excela nie powiódł się")
        messages.error(request, "Nie udało się zaimportować pliku. Sprawdź, czy to poprawny "
                                "eksport przesyłek (nagłówki, format kolumn) i spróbuj ponownie.")
    return redirect("ui:planner_shipments")


__all__ = [
    "excel_template_shipment_lines",
    "planner_shipments_import",
]
