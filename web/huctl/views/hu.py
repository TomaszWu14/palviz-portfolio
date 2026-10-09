# Handling Unit (paleta) — in-app control module.
# Generation of expected contents from the palletization; the scanner-style
# control transaction lives in hu_control.py.
from ui.views.core import (
    HandlingUnit, HandlingUnitItem, Shipment,
    _after_import_redirect, _build_shipment_three_data, _calc_shipment_data,
    _controller, _md_or_control, _shipment_pallet_height, _read_table, _transport_mgr,
    get_object_or_404, messages, redirect, require_POST, transaction, _pk4,  # _pk4: int4-safe pk → 404, nie 500
)
from huctl.hu_import import (  # noqa: F401  (aliasy: stare nazwy dla wołań/testów)
    _HU_ALIASES, _TRUTHY, _map_hu_columns, _match_product_in_ref, _parse_date_any,
    import_hu_rows as _import_hu_rows, parse_float as _parse_float,
)


def _generate_handling_units(shipment):
    """(Re)build the shipment's handling units from the palletization.

    One HU per pallet from the py3dbp layout; expected items = cartons per REF on
    that pallet, with both KAR (alt) and base-unit (JP) quantities. Existing HUs are
    replaced (pickHU codes and any control state are reset)."""
    calc = _calc_shipment_data(shipment)
    # render_cap=None → generujemy HU dla KAŻDEJ palety (nie tylko pierwszych 200 z widoku 3D).
    # Wysokość wybrana na wysyłce (BIZ-007) — liczba HU = liczba palet z wyceny spedycji.
    data = _build_shipment_three_data(calc, max_h=_shipment_pallet_height(shipment),
                                      render_cap=None)
    if not data:
        return 0

    prod_by_code = {lc["product"].code: lc["product"] for lc in calc["lines"]}
    # Per-REF base-unit conversion (pcs_per_carton) and the source sales unit label.
    ppc, base_unit = {}, {}
    for line in shipment.lines.select_related("product").prefetch_related("product__instructions"):
        code = line.product.code
        instr = line.product.latest_instruction()
        ppc[code] = (instr.pcs_per_carton if instr else 1) or 1
        base_unit[code] = line.source_unit or "szt"

    old_codes = {hu.seq: hu.code for hu in shipment.handling_units.all()}

    with transaction.atomic():
        shipment.handling_units.all().delete()
        for i, pallet in enumerate(data["pallets"], start=1):
            counts = {}
            for box in pallet["boxes"]:
                counts[box["label"]] = counts.get(box["label"], 0) + 1
            hu = HandlingUnit.objects.create(
                shipment=shipment, seq=i, code=old_codes.get(i, ""),
                recipient_type=shipment.recipient_name[:40] if shipment.recipient_name else "")
            HandlingUnitItem.objects.bulk_create([
                HandlingUnitItem(
                    hu=hu, ref_code=ref, product=prod_by_code.get(ref),
                    description=(prod_by_code[ref].name[:120] if prod_by_code.get(ref) else ""),
                    alt_unit="KAR", alt_qty=kar,
                    base_unit=base_unit.get(ref, "szt"), base_qty=kar * ppc.get(ref, 1),
                    expected_qty=kar, unit="kar")
                for ref, kar in sorted(counts.items())
            ])
    return len(data["pallets"])


@_transport_mgr
@require_POST
def planner_shipment_generate_hus(request, pk):
    shipment = get_object_or_404(Shipment, pk=_pk4(pk))
    if shipment.has_controlled_hu():
        messages.error(request, "Nie można wygenerować HU ponownie — palety są już w kontroli "
                                "(regeneracja skasowałaby historię kontroli).")
        return redirect("ui:planner_shipment_detail", pk=pk)
    n = _generate_handling_units(shipment)
    if n:
        messages.success(request, f"Wygenerowano {n} HU (palet) z oczekiwaną zawartością.")
    else:
        messages.error(request, "Brak danych do wygenerowania HU (dodaj linie i instrukcje paletyzacji).")
    return redirect("ui:planner_shipment_detail", pk=pk)


def _import_holes_label(info, prefix=""):
    """Trwały opis dziur wsadu do ImportRun.label (panel statusu importów) —
    messages znikają po odświeżeniu, a diagnoza jakości feedu potrzebuje historii."""
    holes = []
    if info.get("missing_batch"):
        holes.append(f"{info['missing_batch']} bez partii")
    if info.get("missing_expiry"):
        holes.append(f"{info['missing_expiry']} bez daty")
    if info.get("no_delivery") and info.get("with_delivery"):
        holes.append(f"{info['no_delivery']} bez dokumentu")
    if info.get("skipped_locked"):
        holes.append(f"{info['skipped_locked']} pominięto ({info.get('locked_hus', 0)} HU w kontroli)")
    tail = ("dziury: " + ", ".join(holes)) if holes else "bez dziur"
    return f"{prefix}{' · ' if prefix else ''}{tail}"[:200]


@_controller
@require_POST
def planner_hu_import(request):
    """Import handling units (with LOT/EXP/pickHU/location) from a PowerBI/SAP export.

    Columns are matched fuzzily; rows are grouped by (delivery, pickHU). Each HU's
    items are replaced from the file. The delivery is matched/created by name."""
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Nie wybrano pliku.")
        return redirect("ui:hu_control_hub")
    if f.size > 10 * 1024 * 1024:
        messages.error(request, "Plik zbyt duży (max 10 MB).")
        return redirect("ui:hu_control_hub")

    header, rows = _read_table(f)
    ok, info = _import_hu_rows(header, rows)
    if not ok:
        messages.error(request, info)
        return redirect("ui:hu_control_hub")
    from ui.notifications import run_stock_discrepancy_checks
    n = run_stock_discrepancy_checks(created_by=request.user)
    extra = f" Wykryto {n} nowych niezgodności (Zadania)." if n else ""
    messages.success(request, f"Zaimportowano {info['hu']} HU i {info['items']} pozycji.{extra}")
    from ui.models import ImportRun
    ImportRun.record("hu_stock_file", rows=info["hu"], user=request.user,
                     label=_import_holes_label(info, prefix=f.name))
    # Feed z dostawami, w którym część wierszy nie ma Dokumentu → błąd wsadu do sprawdzenia.
    if info.get("no_delivery") and info.get("with_delivery"):
        messages.warning(request, f"Uwaga: {info['no_delivery']} wierszy bez Dokumentu (dostawy) "
                                  "— trafiły do „Stock magazynowy”. Sprawdź wsad.")
    if info.get("skipped_locked"):
        messages.warning(request, f"Pominięto {info['skipped_locked']} pozycji z {info.get('locked_hus', 0)} HU "
                                  "już w kontroli — zawartość, liczenie, metadane i dostawa bez zmian.")
    # Partia i termin ważności są w spec-u wsadu obowiązkowe — braki wchodzą, ale głośno.
    holes = []
    if info.get("missing_batch"):
        holes.append(f"{info['missing_batch']} bez partii dostawcy")
    if info.get("missing_expiry"):
        holes.append(f"{info['missing_expiry']} bez terminu ważności")
    if holes:
        messages.warning(request, "Dziury wsadu: " + ", ".join(holes) +
                                  " — pozycje weszły, ale kontrola nie potwierdzi partii/daty.")
    return _after_import_redirect(request, "ui:hu_control_hub")


@_md_or_control
@require_POST
def planner_stock_powerbi_import(request):
    """Pull warehouse stock straight from the Power BI / SAP BW dataset (no file upload)
    and feed it through the same HU importer. Lands in the 'Stock magazynowy' container."""
    from ui import powerbi
    if not powerbi.is_configured():
        messages.error(request, "Power BI nie jest skonfigurowany — ustaw zmienne POWERBI_* "
                                "(tenant, client, secret, workspace, dataset).")
        return _after_import_redirect(request, "ui:planner_stock")
    try:
        header, rows = powerbi.fetch_table()
    except Exception as exc:
        # Zapisz błąd na tokenie — dzięki temu widać go też na panelu i w powerbi_diag,
        # a nie tylko w ulotnym komunikacie.
        powerbi.record_error(exc)
        # INT-006: bez str(exc) — szczegół (URL z ID workspace/dataset, MSAL) loguje record_error.
        messages.error(request, "Nie udało się pobrać danych z Power BI — spróbuj ponownie lub zgłoś administratorowi.")
        return _after_import_redirect(request, "ui:planner_stock")
    if not rows:
        messages.warning(request, "Power BI zwrócił 0 wierszy — sprawdź nazwę tabeli (POWERBI_STOCK_TABLE).")
        return _after_import_redirect(request, "ui:planner_stock")
    ok, info = _import_hu_rows(header, rows)
    if ok:
        powerbi.clear_error()
        from ui.notifications import run_stock_discrepancy_checks
        n = run_stock_discrepancy_checks(created_by=request.user)
        extra = f" Wykryto {n} nowych niezgodności (Zadania)." if n else ""
        if info.get("skipped_locked"):
            extra += f" Pominięto {info['skipped_locked']} pozycji z {info.get('locked_hus', 0)} HU już w kontroli."
        messages.success(request, f"Pobrano stan z Power BI: {info['hu']} HU, {info['items']} pozycji.{extra}")
        from ui.models import ImportRun
        ImportRun.record("hu_stock_powerbi", rows=info["hu"], user=request.user,
                         label=_import_holes_label(info))
    else:
        messages.error(request, info)
    return _after_import_redirect(request, "ui:planner_stock")


__all__ = [
    "_generate_handling_units",
    "_import_hu_rows",
    "planner_shipment_generate_hus",
    "planner_hu_import",
    "planner_stock_powerbi_import",
]
