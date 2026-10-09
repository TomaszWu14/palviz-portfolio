# Wyceny frachtu: historia ofert, doplaty, odbiorcy zapytan, wysylka i odpowiedz oferty.
from ui.views.core import (
    HttpResponse, Q, QuoteRecipient, Shipment, ShipmentQuoteOffer,
    _pk4, _safe_next,
    _transport_mgr, get_object_or_404, messages, redirect, render,
    require_POST,
)
from transport.pallet_count import shipment_pallet_calc
from django.conf import settings
from core.ratelimit import post_rate_limit
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from decimal import Decimal, InvalidOperation
import logging
from .mailing import (  # noqa: F401
    ROUTE_MAP_CID, _attach_inline_png, _fetch_static_map, _google_route, _quote_mailto,
    _smtp_unconfigured, _transport_desc,
)

logger = logging.getLogger(__name__)


@_transport_mgr
def planner_quote_history(request):
    """Historia wycen — przekrojowy widok wszystkich przesyłek, które otrzymały oferty
    spedycji. Dla każdej: liczba ofert, oferta wybrana lub najtańsza, oraz oszczędność
    względem najdroższej oferty. Filtrowanie po nazwie/odbiorcy/mieście."""
    q = request.GET.get("q", "").strip()
    shipments = (Shipment.objects.filter(is_stock=False, quote_offers__submitted_at__isnull=False)
                 .distinct().prefetch_related("quote_offers"))
    if q:
        shipments = shipments.filter(Q(name__icontains=q) | Q(recipient_name__icontains=q)
                                     | Q(destination_city__icontains=q))
    shipments = list(shipments)
    # Warm NBP rates once per distinct currency (cached, graceful on failure).
    from ui import nbp
    currencies = {o.currency for sh in shipments for o in sh.quote_offers.all()
                  if o.submitted_at and o.amount is not None}
    rates = {c: nbp.get_rate(c) for c in currencies}

    rows, total_selected = [], 0.0
    for sh in shipments:
        offers = [o for o in sh.quote_offers.all() if o.submitted_at and o.amount is not None]
        if not offers:
            continue
        # Compare on the all-in total (base + fuel% + fixed surcharge). When offers span
        # several currencies and we have every NBP rate, normalise to PLN so the cheapest
        # is picked fairly; otherwise compare raw totals (single currency / missing rate).
        cur_set = {o.currency for o in offers}
        normalize = len(cur_set) > 1 and all(rates.get(c) for c in cur_set)
        # Multiple currencies but at least one missing NBP rate → totals aren't comparable.
        mixed_unconverted = len(cur_set) > 1 and not normalize

        def metric(o):
            t = float(o.total_amount)
            return t * float(rates[o.currency]) if normalize else t

        totals = [metric(o) for o in offers]
        # Przy nieporównywalnych walutach (brak kursu NBP) NIE koronujemy „najtańszej" —
        # min() na surowych kwotach wybrałby np. 300 EUR jako tańsze od 1000 PLN. Bez jawnego
        # wyboru bierzemy pierwszą ofertę jako neutralny placeholder (UI oznacza „nieporównywalne").
        chosen = (next((o for o in offers if o.selected), None)
                  or (offers[0] if mixed_unconverted else min(offers, key=metric)))
        best = metric(chosen)
        worst = max(totals)
        total_selected += best
        rate = rates.get(chosen.currency)
        chosen_pln = float(chosen.total_amount) * float(rate) if (rate and chosen.currency != "PLN") else None
        rows.append({
            "sh": sh, "n_offers": len(offers),
            "chosen": chosen, "best": best, "worst": worst,
            "currency": "PLN" if normalize else chosen.currency,
            # Saving is only meaningful when all offers are in one comparable currency.
            "saving": 0.0 if mixed_unconverted else worst - best,
            "has_surcharge": any(o.has_surcharge for o in offers),
            "chosen_pln": chosen_pln, "normalized": normalize,
            "mixed_unconverted": mixed_unconverted,
            "is_selected": chosen.selected})
    rows.sort(key=lambda r: r["sh"].created_at, reverse=True)

    if request.GET.get("export") == "csv":
        from ui.views.core.xlsx import safe_csv_writer  # SEC-007
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="historia_wycen.csv"'
        response.write("﻿")  # UTF-8 BOM for Excel
        w = safe_csv_writer(response)
        w.writerow(["Przesyłka", "Miasto", "Kraj", "Data", "Liczba ofert",
                    "Spedycja", "Wybrana?", "Kwota (z dopł.)", "Waluta", "Najdroższa", "Oszczędność"])
        for r in rows:
            sh = r["sh"]
            w.writerow([sh.name, sh.destination_city, sh.destination_country,
                        sh.created_at.strftime("%Y-%m-%d"), r["n_offers"],
                        r["chosen"].display_name,
                        "tak" if r["is_selected"] else ("nie (waluty nieporównywalne)"
                                                        if r["mixed_unconverted"] else "nie (najtańsza)"),
                        f"{r['best']:.2f}", r["currency"], f"{r['worst']:.2f}", f"{r['saving']:.2f}"])
        return response

    return render(request, "ui/planner/quote_history.html", {
        "rows": rows, "q": q,
        "total_offers": sum(r["n_offers"] for r in rows),
        "total_saving": sum(r["saving"] for r in rows)})


@_transport_mgr
@require_POST
def planner_offer_surcharge(request, offer_id):
    """Ustaw dopłaty (paliwowa % + stała + opis) dla oferty spedycji."""
    offer = get_object_or_404(ShipmentQuoteOffer, pk=offer_id)

    def _dec(name):
        raw = (request.POST.get(name) or "").strip().replace(",", ".")
        try:
            return max(Decimal("0"), Decimal(raw)) if raw else Decimal("0")
        except (InvalidOperation, ValueError):
            return Decimal("0")

    offer.fuel_pct = _dec("fuel_pct")
    offer.surcharge_fixed = _dec("surcharge_fixed")
    offer.surcharge_note = (request.POST.get("surcharge_note") or "").strip()[:200]
    offer.save(update_fields=["fuel_pct", "surcharge_fixed", "surcharge_note"])
    messages.success(request, f"Zapisano dopłaty dla: {offer.display_name}.")
    return _safe_next(request, reverse("ui:planner_shipment_detail", args=[offer.shipment_id]))


@_transport_mgr
def planner_quote_recipients(request):
    """Manage the predefined address list used for transport quote requests."""
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "add":
            name = request.POST.get("name", "").strip()
            email = request.POST.get("email", "").strip()
            if name and email:
                QuoteRecipient.objects.create(
                    name=name[:120], email=email[:254],
                    is_warehouse=bool(request.POST.get("is_warehouse")),
                    notes=request.POST.get("notes", "").strip()[:200])
                messages.success(request, "Dodano adres do listy.")
            else:
                messages.error(request, "Podaj nazwę i e-mail.")
        elif action == "del":
            qs = QuoteRecipient.objects.filter(pk=_pk4(request.POST.get("rid")))
            if qs.exists():
                qs.delete()
                messages.success(request, "Usunięto adres.")
            else:
                messages.error(request, "Nie znaleziono adresu do usunięcia.")
        return redirect("ui:planner_quote_recipients")
    return render(request, "ui/planner/quote_recipients.html",
                  {"recipients": QuoteRecipient.objects.all()})


@_transport_mgr
def planner_shipment_quote(request, pk):
    """Quote-request screen: route map + per-recipient send (each forwarder e-mailed
    separately, summary only — no line items, contact = the logged-in user)."""
    from django.conf import settings
    import urllib.parse
    shipment = get_object_or_404(Shipment, pk=pk)
    calc, sel = shipment_pallet_calc(shipment, stow_eff=shipment.stowage_efficiency_pct)   # BIZ-007

    origin = (request.GET.get("origin") or "").strip() or getattr(settings, "SHIPMENT_ORIGIN_ADDRESS", "")
    dest = shipment.destination_address()
    km, static_url = _google_route(origin, dest) if dest else (None, None)
    # INT-005: URL Static Maps zawiera klucz API — przeglądarka dostaje proxy (ta sama rola),
    # które pobiera obraz po stronie serwera.
    static_map = None
    if static_url:
        static_map = reverse("ui:planner_shipment_route_map", args=[shipment.pk])
        if origin:
            static_map += "?" + urllib.parse.urlencode({"origin": origin})
    maps_url = None
    if dest:
        maps_url = "https://www.google.com/maps/dir/?api=1&destination=" + urllib.parse.quote(dest)
        if origin:
            maps_url += "&origin=" + urllib.parse.quote(origin)
    # No-key route embed (shows the drawn route + distance inside the iframe).
    embed_url = None
    if dest and not static_url:
        embed_url = ("https://maps.google.com/maps?" + urllib.parse.urlencode(
            {"saddr": origin, "daddr": dest, "output": "embed"}))

    sender_name = request.user.get_full_name() or request.user.get_username()
    recipients = []
    for r in QuoteRecipient.objects.filter(is_active=True, is_warehouse=False):
        offer, _ = ShipmentQuoteOffer.objects.get_or_create(
            shipment=shipment, recipient=r, defaults={"carrier_name": r.name})
        # Record who is requesting (shown to the forwarder), unless already submitted.
        if not offer.submitted_at:
            offer.sender_name = sender_name
            offer.sender_email = request.user.email or ""
            offer.save(update_fields=["sender_name", "sender_email"])
        response_url = request.build_absolute_uri(
            reverse("ui:quote_response", args=[offer.token]))
        recipients.append({"obj": r, "offer": offer, "mailto": _quote_mailto(
            shipment, r.email, n_pallets=sel["n_pallets"], height_cm=sel["max_h_cm"],
            n_pallets_low=(sel.get("n_pallets_low") if sel.get("n_range") else None),
            calc=calc, maps_url=maps_url, km=km, sender=request.user, response_url=response_url)})

    return render(request, "ui/planner/shipment_quote.html", {
        "shipment": shipment, "calc": calc, "scenario": sel,
        "origin": origin, "dest": dest, "km": km, "maps_url": maps_url,
        "static_map": static_map, "embed_url": embed_url, "recipients": recipients,
    })


@_transport_mgr
@require_GET
def planner_shipment_route_map(request, pk):
    """Proxy mapy trasy (PNG) dla ekranu wyceny: obraz Static Maps pobierany po stronie
    serwera, więc klucz GOOGLE_MAPS_API_KEY nie trafia do przeglądarki (audyt INT-005)."""
    shipment = get_object_or_404(Shipment, pk=pk)
    origin = (request.GET.get("origin") or "").strip() or getattr(settings, "SHIPMENT_ORIGIN_ADDRESS", "")
    dest = shipment.destination_address()
    _, static_url = _google_route(origin, dest) if dest else (None, None)
    png = _fetch_static_map(static_url)
    if not png:
        return HttpResponse(status=404)
    resp = HttpResponse(png, content_type="image/png")
    resp["Cache-Control"] = "private, max-age=600"
    return resp


def _quote_response_context(request, offer, sh, **extra):
    """Load + route info shown to the forwarder on the response page."""
    import urllib.parse
    calc, sel = shipment_pallet_calc(sh)          # BIZ-007: wysokość wybrana na wysyłce
    origin = getattr(settings, "SHIPMENT_ORIGIN_ADDRESS", "")
    dest = sh.destination_address()
    embed_url = None
    if dest:
        embed_url = "https://maps.google.com/maps?" + urllib.parse.urlencode(
            {"saddr": origin, "daddr": dest, "output": "embed"})
    ctx = {"offer": offer, "shipment": sh, "company": getattr(settings, "COMPANY_NAME", ""),
           "origin": origin, "dest": dest, "embed_url": embed_url,
           "n_pallets": sel["n_pallets"] if sel else 0,
           "height_cm": sel["max_h_cm"] if sel else 0,
           "volume_m3": calc["total_vol_m3"], "weight_kg": calc["total_weight_kg"]}
    ctx.update(extra)
    return ctx


def _quote_email_context(request, shipment, offer):
    """Load + route data for the styled HTML quote e-mail."""
    import urllib.parse
    calc, sel = shipment_pallet_calc(shipment)    # BIZ-007: wysokość wybrana na wysyłce
    origin = getattr(settings, "SHIPMENT_ORIGIN_ADDRESS", "")
    dest = shipment.destination_address()
    _, static_url = _google_route(origin, dest) if dest else (None, None)
    # INT-005: obraz pobrany po stronie serwera i dołączony inline (cid:) — URL Static Maps
    # z kluczem API nie trafia do e-maila przewoźnika. Błąd pobrania → e-mail bez mapy.
    route_map_png = _fetch_static_map(static_url)
    maps_url = None
    if dest:
        maps_url = "https://www.google.com/maps/dir/?api=1&destination=" + urllib.parse.quote(dest)
        if origin:
            maps_url += "&origin=" + urllib.parse.quote(origin)
    return {
        "shipment": shipment, "offer": offer, "company": getattr(settings, "COMPANY_NAME", ""),
        "origin": origin, "dest": dest or "—",
        "origin_city": (origin.split(",")[0] if origin else "Warszawa"),
        "dest_city": shipment.destination_city or dest or "—",
        "n_pallets": sel["n_pallets"] if sel else 0, "height_cm": sel["max_h_cm"] if sel else 0,
        "volume_m3": calc["total_vol_m3"], "weight_kg": calc["total_weight_kg"],
        "transport_vehicle": _transport_desc(shipment)[0], "load_mode_label": shipment.get_load_mode_display(),
        "response_url": request.build_absolute_uri(reverse("ui:quote_response", args=[offer.token])),
        "maps_url": maps_url, "static_map": f"cid:{ROUTE_MAP_CID}" if route_map_png else None,
        "route_map_png": route_map_png,
    }


@_transport_mgr
@require_POST
def planner_shipment_send_quote(request, pk, recipient_id):
    """Send the styled HTML quote request to ONE forwarder (server-side SMTP)."""
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    shipment = get_object_or_404(Shipment, pk=pk)
    r = get_object_or_404(QuoteRecipient, pk=recipient_id, is_active=True)
    offer, _ = ShipmentQuoteOffer.objects.get_or_create(
        shipment=shipment, recipient=r, defaults={"carrier_name": r.name})
    if not offer.submitted_at:
        offer.sender_name = request.user.get_full_name() or request.user.get_username()
        offer.sender_email = request.user.email or ""
        offer.carrier_name = r.name
        offer.save(update_fields=["sender_name", "sender_email", "carrier_name"])
    if _smtp_unconfigured():
        messages.error(request, "Serwer poczty (SMTP) nie jest skonfigurowany — ustaw EMAIL_HOST, "
                                "EMAIL_HOST_USER, EMAIL_HOST_PASSWORD na produkcji. Na razie użyj przycisku „mailto”.")
        return redirect("ui:planner_shipment_quote", pk=pk)
    ctx = _quote_email_context(request, shipment, offer)
    route_map_png = ctx.pop("route_map_png", None)
    html = render_to_string("ui/email/quote_request.html", ctx)
    msg = EmailMultiAlternatives(
        f"Zapytanie o wycenę transportu — {shipment.name}",
        "Zapytanie o wycenę transportu — wiadomość w formacie HTML.",
        getattr(settings, "DEFAULT_FROM_EMAIL", "") or "no-reply@palviz.local", [r.email],
        reply_to=[request.user.email] if request.user.email else None)
    msg.attach_alternative(html, "text/html")
    if route_map_png:
        _attach_inline_png(msg, route_map_png)
    try:
        msg.send()
        messages.success(request, f"Wysłano zapytanie do {r.name} ({r.email}).")
        # Zdarzenie do n8n (best-effort) — research przewoźnika. Payload WYŁĄCZNIE
        # nie-wrażliwy: nazwa spedytora + miasto/kraj docelowy. Bez klienta/REF/ceny —
        # to jedyne pola, które wolno posłać do zewnętrznego researchu (Perplexity).
        from ui.notifications import emit_event
        emit_event("carrier_quote", {
            "carrier_name": r.name,
            "dest_city": shipment.destination_city,
            "dest_country": shipment.destination_country,
        })
    except Exception as exc:
        logger.exception("Wysyłka zapytania o wycenę przez SMTP nie powiodła się (host=%s)",
                         settings.EMAIL_HOST)
        messages.error(request, f"Nie udało się wysłać e-maila przez SMTP ({settings.EMAIL_HOST}). "
                                "Sprawdź dane logowania/port serwera poczty lub spróbuj ponownie za chwilę.")
    return redirect("ui:planner_shipment_quote", pk=pk)


@post_rate_limit("quote_response", 20, 3600, by="token")   # SEC-014
def quote_response(request, token):
    """Public, tokenised form for a forwarder to enter the price + dates."""
    offer = get_object_or_404(ShipmentQuoteOffer.objects.select_related("shipment"), token=token)
    sh = offer.shipment
    # Fillable only once — once submitted the form is locked (read-only).
    if offer.submitted_at:
        return render(request, "ui/quote_response.html", _quote_response_context(request, offer, sh, locked=True))
    if request.method == "POST":
        from decimal import Decimal, InvalidOperation
        from datetime import datetime as _dt

        def _date(s):
            try:
                return _dt.strptime((s or "").strip(), "%Y-%m-%d").date()
            except ValueError:
                return None
        try:
            offer.amount = Decimal((request.POST.get("amount") or "0").replace(",", ".").replace(" ", ""))
        except (InvalidOperation, ValueError):
            offer.amount = None
        offer.currency = (request.POST.get("currency") or "PLN")[:3].upper()
        offer.truck_date = _date(request.POST.get("truck_date"))
        offer.delivery_date = _date(request.POST.get("delivery_date"))
        offer.notes = (request.POST.get("notes") or "")[:300]
        att = request.FILES.get("attachment")
        if att:
            import os as _os
            _ok_types = {"application/pdf", "image/jpeg", "image/png"}
            _ok_exts = {".pdf", ".jpg", ".jpeg", ".png"}
            if att.size > 30 * 1024 * 1024:
                return render(request, "ui/quote_response.html", _quote_response_context(
                    request, offer, sh, error="Załącznik zbyt duży (max 30 MB)."))
            # PUBLIC (token-only) upload — restrict to PDF/JPG/PNG so a forwarder can't
            # stash active content (.html/.svg) that would be served from our origin.
            if (att.content_type not in _ok_types
                    or _os.path.splitext(att.name)[1].lower() not in _ok_exts):
                return render(request, "ui/quote_response.html", _quote_response_context(
                    request, offer, sh, error="Dozwolone tylko PDF / JPG / PNG."))
            offer.attachment = att
        # A missing/garbled amount must NOT lock the form — let the forwarder correct it.
        if offer.amount is None:
            return render(request, "ui/quote_response.html", _quote_response_context(
                request, offer, sh,
                error="Podaj poprawną kwotę oferty (bez separatora tysięcy, np. 1234,56)."))
        # Delivery date can't be earlier than the truck-spotting date.
        if offer.truck_date and offer.delivery_date and offer.delivery_date < offer.truck_date:
            return render(request, "ui/quote_response.html", _quote_response_context(
                request, offer, sh,
                error="Data dostawy nie może być wcześniejsza niż data podstawienia auta."))
        offer.submitted_at = timezone.now()
        offer.save()
        return render(request, "ui/quote_response.html", _quote_response_context(request, offer, sh, saved=True))
    return render(request, "ui/quote_response.html", _quote_response_context(request, offer, sh))

__all__ = [
    "planner_quote_history",
    "planner_offer_surcharge",
    "planner_quote_recipients",
    "planner_shipment_quote",
    "planner_shipment_route_map",
    "_quote_response_context",
    "_quote_email_context",
    "planner_shipment_send_quote",
    "quote_response",
]
