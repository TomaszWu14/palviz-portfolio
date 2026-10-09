# Dokumenty przewozowe: WZ, CSV przewoznika, CMR.
from ui.views.core import (
    HttpResponse, Shipment,
    _md_or_tr, _transport_mgr, get_object_or_404,
)
from django.conf import settings
from transport.pallet_count import shipment_pallet_calc
from .mailing import _all_requirements, _transport_desc  # noqa: F401


@_md_or_tr
def planner_shipment_wz(request, pk):
    """Dokument WZ (Wydanie) do wydruku — PDF przez WeasyPrint, fallback HTML (P2 #9).

    Numer WZ pochodzi z SAP (feed HU, `Shipment.wz_number`) — nie numerujemy lokalnie.
    Pozycje = agregacja pozycji wszystkich HU dostawy (indeks × partia). Liczba kopii
    z wymagań klienta (`Customer.wz_copies`, domyślnie 1) — każda kopia od nowej strony."""
    from django.template.loader import render_to_string
    from ui.hu_metrics import hu_metrics
    shipment = get_object_or_404(Shipment, pk=pk)
    hus = list(shipment.handling_units.all())
    # Agregacja (indeks, partia) po wszystkich paletach dostawy.
    lines = {}
    for hu in hus:
        for it in hu.items.all():
            key = (it.ref_code, it.lot)
            row = lines.setdefault(key, {"ref": it.ref_code, "desc": it.description,
                                         "lot": it.lot, "expiry": it.expiry,
                                         "qty": 0.0, "unit": it.base_unit or "szt"})
            row["qty"] += float(it.base_qty or 0)
    metrics = hu_metrics(hus)
    total_weight = round(sum((metrics.get(h.pk, {}).get("weight_kg") or 0) for h in hus), 1)
    copies = (shipment.customer.wz_copies if shipment.customer_id
              and shipment.customer.wz_copies else 1)
    ctx = {
        "shipment": shipment,
        "sender_name": getattr(settings, "COMPANY_NAME", "") or getattr(settings, "APP_NAME", "GROOVE"),
        "sender_addr": getattr(settings, "SHIPMENT_ORIGIN_ADDRESS", "") or "",
        "consignee_name": (shipment.customer.name if shipment.customer_id
                           else shipment.recipient_name) or "—",
        "consignee_country": (shipment.customer.country if shipment.customer_id else "") or "",
        "recipient_name": shipment.recipient_name or "",
        "recipient_phone": (shipment.customer.phone if shipment.customer_id else "") or "",
        "dest_addr": shipment.destination_address() or "",
        "lines": sorted(lines.values(), key=lambda r: (r["ref"], r["lot"])),
        "n_hus": len(hus),
        "hu_codes": [h.code for h in hus if h.code],
        "total_weight_kg": total_weight,
        "copies": range(copies),
        "n_copies": copies,
        "issuer_name": request.user.get_full_name() or request.user.get_username(),
        "issuer_email": request.user.email or "",
    }
    html = render_to_string("ui/wz.html", ctx, request=request)
    try:
        from weasyprint import HTML
        pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    except Exception:
        return HttpResponse(html)        # native libs absent → printable HTML
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="WZ_{shipment.wz_number or shipment.pk}.pdf"'
    return resp


@_md_or_tr
def planner_shipment_carrier_csv(request, pk):
    """Eksport gotowości dla przewoźnika (P2 #8, wariant plikowy): CSV z paletami —
    wymiary, wagi, objętości + adres dostawy i przewoźnik klienta. Do wysłania mailem /
    wgrania do portalu przewoźnika; integrację API dodamy, gdy będzie endpoint."""
    from ui.views.core.xlsx import safe_csv_writer  # SEC-007
    from ui.hu_metrics import hu_metrics
    shipment = get_object_or_404(Shipment, pk=pk)
    hus = list(shipment.handling_units.all())
    metrics = hu_metrics(hus)
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="gotowosc_{shipment.pk}.csv"'
    resp.write("﻿")  # BOM → polskie znaki w Excelu
    w = safe_csv_writer(resp, delimiter=";")
    carrier = (shipment.customer.carrier if shipment.customer_id else "") or "—"
    w.writerow(["Dostawa", shipment.name, "WZ", shipment.wz_number or "—",
                "Przewoźnik", carrier])
    w.writerow(["Odbiorca", (shipment.customer.name if shipment.customer_id
                             else shipment.recipient_name) or "—",
                "Adres", shipment.destination_address() or "—"])
    w.writerow([])
    w.writerow(["Paleta (pickHU)", "Długość [cm]", "Szerokość [cm]", "Wysokość [cm]",
                "Waga [kg]", "Objętość [m³]"])
    for hu in hus:
        m = metrics.get(hu.pk, {})
        w.writerow([hu.code or hu.ref, hu.length_cm or "", hu.width_cm or "",
                    hu.height_cm or "", m.get("weight_kg") or "", m.get("volume_m3") or ""])
    return resp


@_transport_mgr
def planner_shipment_cmr(request, pk):
    """Render a CMR international consignment note for the shipment as a printable PDF
    (WeasyPrint, lazy import → degrades to printable HTML when native libs are absent)."""
    from django.template.loader import render_to_string
    shipment = get_object_or_404(Shipment, pk=pk)
    calc, sc = shipment_pallet_calc(shipment, with_packing=False)   # BIZ-007
    offer = shipment.selected_offer()
    veh, _lm = _transport_desc(shipment)
    ctx = {
        "shipment": shipment,
        "sender_name": getattr(settings, "COMPANY_NAME", "") or getattr(settings, "APP_NAME", "GROOVE"),
        "sender_addr": getattr(settings, "SHIPMENT_ORIGIN_ADDRESS", "") or "",
        "consignee": shipment.customer,
        "consignee_name": (shipment.customer.name if shipment.customer_id else shipment.recipient_name) or "",
        "dest_addr": shipment.destination_address() or "",
        "lines": calc["lines"],
        "n_pallets": sc["n_pallets"] if sc else 0,
        "total_weight_kg": calc["total_weight_kg"],
        "total_vol_m3": calc["total_vol_m3"],
        "vehicle": veh,
        "carrier": (offer.carrier_name if offer else "") or (offer.display_name if offer else ""),
        "pickup_date": offer.truck_date if offer else None,
        "requirements": "; ".join(_all_requirements(shipment)),
    }
    html = render_to_string("ui/cmr.html", ctx, request=request)
    try:
        from weasyprint import HTML
        pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    except Exception:
        return HttpResponse(html)        # native libs absent → printable HTML
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="CMR_{shipment.name}.pdf"'
    return resp

__all__ = [
    "planner_shipment_wz",
    "planner_shipment_carrier_csv",
    "planner_shipment_cmr",
]
