# Helpery e-mail/mailto: trasa Google, tresci ofert/anulowania/kierowcy/magazynu, SMTP.
from ui.views.core import (
    QuoteRecipient, Shipment, _transport_mgr, get_object_or_404, messages,
    redirect, require_POST,
)
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

# Content-ID mapy trasy osadzonej w e-mailu (multipart/related, <img src="cid:…">).
ROUTE_MAP_CID = "trasa@groove"
_STATIC_MAP_MAX_BYTES = 2 * 1024 * 1024


def _google_route(origin, dest):
    """Best-effort road distance + a static-map screenshot of the route.

    Needs GOOGLE_MAPS_API_KEY (Directions + Static Maps). Returns (km, static_url)
    or (None, None) when there's no key or the call fails.

    UWAGA (audyt INT-005): static_url ZAWIERA klucz API — tylko do pobrania obrazu po
    stronie serwera (_fetch_static_map); nigdy nie renderuj go w HTML/e-mailu/mailto."""
    from django.conf import settings
    import urllib.parse, urllib.request, json
    key = getattr(settings, "GOOGLE_MAPS_API_KEY", "") or ""
    if not key or not origin or not dest:
        return None, None
    try:
        q = urllib.parse.urlencode({"origin": origin, "destination": dest, "key": key})
        with urllib.request.urlopen(
                "https://maps.googleapis.com/maps/api/directions/json?" + q, timeout=5) as r:
            d = json.load(r)
        route = (d.get("routes") or [None])[0]
        if not route:
            return None, None
        km = round(route["legs"][0]["distance"]["value"] / 1000)
        poly = route["overview_polyline"]["points"]
        static = ("https://maps.googleapis.com/maps/api/staticmap?size=620x300&"
                  + urllib.parse.urlencode({
                      "path": "color:0x6b21a8|weight:4|enc:" + poly,
                      "markers": "color:green|" + origin, "key": key})
                  + "&markers=" + urllib.parse.quote("color:red|" + dest))
        return km, static
    except Exception:
        return None, None


def _fetch_static_map(static_url):
    """Pobiera PNG mapy trasy (Static Maps) PO STRONIE SERWERA — klucz API nie opuszcza
    serwera (audyt INT-005). Zwraca bajty PNG albo None (brak URL-a, błąd, nie-PNG).
    Log bez URL-a: komunikaty wyjątków requests potrafią zawierać URL z kluczem."""
    if not static_url:
        return None
    import requests
    try:
        r = requests.get(static_url, timeout=5)
    except Exception as exc:
        logger.warning("Mapa trasy (Static Maps) niedostępna: %s", type(exc).__name__)
        return None
    ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    body = r.content or b""
    if r.status_code != 200 or ctype != "image/png" or not body or len(body) > _STATIC_MAP_MAX_BYTES:
        logger.warning("Mapa trasy (Static Maps) odrzucona: HTTP %s, %s, %d B",
                       r.status_code, ctype or "brak Content-Type", len(body))
        return None
    return body


def _attach_inline_png(msg, png, cid=ROUTE_MAP_CID, filename="trasa.png"):
    """Dołącza PNG do e-maila jako obraz inline (multipart/related) — HTML wskazuje go
    przez src="cid:<cid>", więc odbiorca nie pobiera niczego z Google (INT-005)."""
    from email.mime.image import MIMEImage
    img = MIMEImage(png, "png")
    img.add_header("Content-ID", f"<{cid}>")
    img.add_header("Content-Disposition", "inline", filename=filename)
    msg.mixed_subtype = "related"
    msg.attach(img)


def _make_mailto(email, subject, body):
    """Build a mailto: URL with an encoded subject + body (shared by all senders)."""
    import urllib.parse
    params = urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)
    # Keep '@' and the ',' address separator literal so multi-recipient mailto stays valid.
    return "mailto:" + urllib.parse.quote(email or "", safe="@,.") + "?" + params


def _all_requirements(shipment):
    """Shipment-level client_requirements + the linked customer's requirement rules, merged
    and de-duplicated — so the warehouse and the forwarder see every client constraint."""
    reqs = []
    if shipment.client_requirements:
        reqs.append(shipment.client_requirements.strip())
    if shipment.customer_id:
        reqs.extend(shipment.customer.requirement_summary())
    seen, out = set(), []
    for r in reqs:
        if r and r not in seen:
            seen.add(r); out.append(r)
    return out


def _transport_desc(shipment):
    """(vehicle-names, load-mode-label) for the chosen transport type(s) — so the forwarder
    quotes the right means of transport, not just a pallet count."""
    from palletizer.services.vehicle_load import VEHICLES
    names = {v["key"]: v["name"] for v in VEHICLES}
    keys = shipment.transport_mode_keys()
    veh = ", ".join(names.get(k, k) for k in keys) if keys else "—"
    return veh, shipment.get_load_mode_display()


def _quote_mailto(shipment, recipient_email, *, n_pallets, height_cm, calc, maps_url, km,
                  sender, response_url, n_pallets_low=None):
    """A single-recipient mailto for a quote request — NO line items, contact = the
    logged-in sender. Each forwarder is e-mailed separately (no shared correspondence).
    The body carries a per-forwarder link to enter the price + truck/delivery dates."""
    dest = shipment.destination_address() or "—"
    contact = (sender.get_full_name() or sender.get_username()) if sender else ""
    if sender and sender.email:
        contact = f"{contact} <{sender.email}>".strip()
    # Borderline load → ask the forwarder to price BOTH pallet counts (e.g. "6 lub 7").
    if n_pallets_low and n_pallets_low != n_pallets:
        lo, hi = sorted((n_pallets_low, n_pallets))   # always print the range low→high
        pal_txt = f"{lo} lub {hi} szt (do potwierdzenia po załadunku)"
    else:
        pal_txt = f"{n_pallets} szt"
    veh, lm = _transport_desc(shipment)
    _reqs = _all_requirements(shipment)
    req_line = ("Wymagania klienta: " + "; ".join(_reqs) + "\n") if _reqs else ""
    body = (
        "Dzień dobry,\n\nProszę o wycenę transportu:\n\n"
        f"Środek transportu: {veh} · załadunek: {lm}\n"
        f"{req_line}"
        f"Liczba palet (EUR 120×80, wys. do {height_cm} cm): {pal_txt}\n"
        f"Objętość łączna: {calc['total_vol_m3']} m³ · Waga brutto: {calc['total_weight_kg']} kg\n"
        f"Adres dostawy: {shipment.recipient_name + ', ' if shipment.recipient_name else ''}{dest}\n"
        + (f"Trasa: {maps_url}" + (f" (~{km} km)\n" if km else "\n") if maps_url else "")
        + f"\nProszę o podanie kwoty i terminów w formularzu:\n{response_url}\n"
        + f"\nOsoba kontaktowa: {contact}\n\nPozdrawiam,\n{contact}\n"
    )
    return _make_mailto(recipient_email, f"Zapytanie o wycenę transportu — {shipment.name}", body)


def _cancel_mailto(shipment):
    """mailto notifying the chosen forwarder that the shipment was cancelled."""
    if shipment.status != "cancelled":
        return None
    offer = shipment.selected_offer()
    if not offer or not offer.recipient:
        return None
    body = (f"Dzień dobry,\n\nInformujemy, że transport dostawy {shipment.name} został "
            f"ANULOWANY. Prosimy o wstrzymanie realizacji.\n\nDziękujemy.\n")
    return _make_mailto(offer.recipient.email, f"Anulowanie transportu — {shipment.name}", body)


def _driver_form_mailto(request, shipment):
    """mailto to the chosen forwarder: order confirmation + driver-data form link."""
    da = getattr(shipment, "driver", None)
    if not da or not da.offer or not da.offer.recipient:
        return None
    url = request.build_absolute_uri(reverse("ui:driver_form", args=[da.form_token]))
    body = (
        f"Dzień dobry,\n\nPotwierdzamy zlecenie transportu dostawy {shipment.name}"
        + (f" za {da.offer.amount} {da.offer.currency}" if da.offer.amount else "") + ".\n\n"
        f"Proszę o uzupełnienie danych kierowcy (nr auta, telefon):\n{url}\n\nDziękuję.\n")
    return _make_mailto(da.offer.recipient.email, f"Potwierdzenie zlecenia — {shipment.name}", body)


def _send_driver_form_email(request, shipment):
    """Send the forwarder order-confirmation + driver-data form as a styled HTML e-mail
    (server-side). Returns (ok, info). Falls back to the mailto button when SMTP is off."""
    from django.template.loader import render_to_string
    da = getattr(shipment, "driver", None)
    if not da or not da.offer or not da.offer.recipient or not da.offer.recipient.email:
        return False, "Brak adresu e-mail wybranej spedycji."
    form_url = request.build_absolute_uri(reverse("ui:driver_form", args=[da.form_token]))
    html = render_to_string("ui/email/driver_form.html", {
        "shipment": shipment, "dest": shipment.destination_address() or "",
        "amount": da.offer.amount, "currency": da.offer.currency, "form_url": form_url})
    return _send_html_email(
        request, [da.offer.recipient.email], f"Potwierdzenie zlecenia — {shipment.name}",
        f"Potwierdzamy zlecenie. Uzupełnij dane kierowcy: {form_url}", html)


def _wh_mailto(shipment, readiness, response_url):
    """mailto to the warehouse mailbox(es) with the readiness token link.

    Recipients = address-book rows marked as 'magazyn'; falls back to WAREHOUSE_EMAIL."""
    to = ",".join(QuoteRecipient.objects.filter(is_active=True, is_warehouse=True)
                  .values_list("email", flat=True)) or getattr(settings, "WAREHOUSE_EMAIL", "") or ""
    dest = shipment.destination_address() or "—"
    if readiness.kind == "pre":
        urgent = "PILNE — prosimy o odpowiedź do 15 minut.\n"
        when = ""
    else:
        urgent = ""
        when = f"Data odbioru: {readiness.pickup_date}\n" if readiness.pickup_date else ""
    _reqs = _all_requirements(shipment)
    req = ("⚠ WYMAGANIA KLIENTA: " + "; ".join(_reqs) + "\n") if _reqs else ""
    asked = getattr(readiness, "asked_pallets", None)
    pal = (f"Do przygotowania: {asked} palet (układ jak w wycenie — widoczny w linku).\n"
           if asked else "")
    body = (
        f"Dzień dobry,\n\nProśba o potwierdzenie gotowości przygotowania dostawy {shipment.name}.\n"
        f"{urgent}{when}{pal}{req}Adres dostawy: {dest}\n\n"
        f"Potwierdź ilość palet i najszybszy termin gotowości (lub zaproponuj inną ilość):\n"
        f"{response_url}\n\nDziękuję.\n"
    )
    return _make_mailto(to, f"Gotowość magazynu — {shipment.name}", body)


def _smtp_unconfigured():
    """True only for the real SMTP backend with no host — so the locmem backend used
    in tests (and console backend in dev) still sends normally."""
    return "smtp" in getattr(settings, "EMAIL_BACKEND", "") and not getattr(settings, "EMAIL_HOST", "")


def _send_html_email(request, to, subject, text, html):
    """Send a styled HTML e-mail (plaintext fallback) to `to`. Returns (ok, info) —
    shared SMTP-guard + send/error handling for the warehouse and forwarder senders."""
    if not to:
        return False, "Brak adresu odbiorcy."
    if _smtp_unconfigured():
        return False, "SMTP nie jest skonfigurowany (ustaw EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)."
    from django.core.mail import EmailMultiAlternatives
    msg = EmailMultiAlternatives(
        subject, text, getattr(settings, "DEFAULT_FROM_EMAIL", "") or "no-reply@palviz.local", to,
        reply_to=[request.user.email] if request.user.email else None)
    msg.attach_alternative(html, "text/html")
    try:
        msg.send()
        return True, ", ".join(to)
    except Exception as exc:
        return False, str(exc)


def _wh_recipients():
    to = list(QuoteRecipient.objects.filter(is_active=True, is_warehouse=True).values_list("email", flat=True))
    if not to and getattr(settings, "WAREHOUSE_EMAIL", ""):
        to = [settings.WAREHOUSE_EMAIL]
    return [e for e in to if e]


def _send_wh_email(request, shipment, readiness):
    """Send the warehouse-readiness request as a styled HTML e-mail (one clickable
    button, no mailto). Returns (ok, info)."""
    from django.template.loader import render_to_string
    to = _wh_recipients()
    if not to:
        return False, "Brak adresu magazynu (oznacz adres jako „magazyn” lub ustaw WAREHOUSE_EMAIL)."
    response_url = request.build_absolute_uri(reverse("ui:wh_readiness_response", args=[readiness.token]))
    # Fala 5: wymiary/wagi/objętości palet (feed → fallback master data, hu_metrics)
    # + sumy — magazyn widzi, co fizycznie ma przygotować.
    from ui.hu_metrics import hu_metrics
    hus = list(shipment.handling_units.all()[:200])
    hu_rows, tot_w, tot_v = [], 0.0, 0.0
    if hus:
        metrics = hu_metrics(hus)
        for h in hus:
            m = metrics.get(h.pk, {})
            dims = (f"{h.length_cm:g}×{h.width_cm:g}×{h.height_cm:g} cm"
                    if h.length_cm and h.width_cm and h.height_cm else "")
            hu_rows.append({"code": h.code or f"#{h.seq}", "dims": dims,
                            "weight": m.get("weight_kg"), "volume": m.get("volume_m3"),
                            "estimated": m.get("estimated")})
            tot_w += m.get("weight_kg") or 0
            tot_v += m.get("volume_m3") or 0
    html = render_to_string("ui/email/wh_readiness.html", {
        "shipment": shipment, "dest": shipment.destination_address() or "—",
        "urgent": readiness.kind == "pre", "pickup_date": readiness.pickup_date,
        "asked_pallets": readiness.asked_pallets,
        "hu_rows": hu_rows, "total_weight": round(tot_w, 1) if tot_w else None,
        "total_volume": round(tot_v, 3) if tot_v else None,
        "client_requirements": "; ".join(_all_requirements(shipment)), "response_url": response_url})
    return _send_html_email(
        request, to, f"Gotowość magazynu — {shipment.name}",
        f"Potwierdź gotowość: {response_url}", html)


def _client_loaded_body(shipment, eta):
    eta_txt = eta.strftime("%d.%m.%Y") if eta else "do potwierdzenia"
    name = shipment.recipient_name or (shipment.customer.name if shipment.customer_id else "")
    return (f"Dzień dobry{', ' + name if name else ''},\n\n"
            f"Informujemy, że towar w ramach dostawy {shipment.name} został załadowany.\n"
            f"Przybliżona data dostawy: {eta_txt}.\n\n"
            f"W razie pytań prosimy o kontakt.\n\nPozdrawiamy.")


def _client_loaded_mailto(shipment, eta):
    """Outlook/mailto fallback for the 'goods loaded' note when server SMTP is unavailable."""
    to = (shipment.customer.contact_email if shipment.customer_id else "") or ""
    return _make_mailto(to, f"Towar załadowany — {shipment.name}", _client_loaded_body(shipment, eta))


@_transport_mgr
@require_POST
def planner_shipment_notify_client(request, pk):
    """Operator decision: tell the client the goods are loaded + the approx delivery date
    (defaults to the forwarder's date on the chosen offer; overridable here)."""
    shipment = get_object_or_404(Shipment, pk=pk)
    from datetime import datetime as _dt
    raw = (request.POST.get("client_eta") or "").strip()
    eta = None
    if raw:
        try:
            eta = _dt.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            eta = None
    if eta is None:
        offer = shipment.selected_offer()
        eta = offer.delivery_date if offer else shipment.client_eta
    to = (shipment.customer.contact_email if shipment.customer_id else "") or ""
    if not to:
        messages.error(request, "Brak e-maila klienta — uzupełnij kontakt u odbiorcy w bazie klientów.")
        return redirect("ui:planner_shipment_detail", pk=pk)
    from django.template.loader import render_to_string
    html = render_to_string("ui/email/client_loaded.html", {
        "shipment": shipment, "eta": eta, "company": getattr(settings, "COMPANY_NAME", "")})
    ok, info = _send_html_email(request, [to], f"Towar załadowany — {shipment.name}",
                                _client_loaded_body(shipment, eta), html)
    shipment.client_eta = eta
    if ok:
        shipment.client_loaded_notified_at = timezone.now()
        shipment.save(update_fields=["client_eta", "client_loaded_notified_at"])
        from ui.notifications import notify, creator_and_transport
        eta_txt = eta.strftime("%d.%m.%Y") if eta else "do potwierdzenia"
        notify(creator_and_transport(shipment), f"Klient powiadomiony o załadunku — {shipment.name}",
               f"Wysłano do {to}. Przybliżona data dostawy: {eta_txt}.", level="info",
               url=reverse("ui:planner_shipment_detail", args=[pk]))
        messages.success(request, f"Powiadomiono klienta o załadunku ({info}).")
    else:
        shipment.save(update_fields=["client_eta"])
        messages.error(request, f"Nie wysłano e-maila — {info} Użyj „✉ Do klienta (Outlook)”.")
    return redirect("ui:planner_shipment_detail", pk=pk)

__all__ = [
    "ROUTE_MAP_CID",
    "_google_route",
    "_fetch_static_map",
    "_attach_inline_png",
    "_make_mailto",
    "_all_requirements",
    "_transport_desc",
    "_quote_mailto",
    "_cancel_mailto",
    "_driver_form_mailto",
    "_send_driver_form_email",
    "_wh_mailto",
    "_smtp_unconfigured",
    "_send_html_email",
    "_wh_recipients",
    "_send_wh_email",
    "_client_loaded_body",
    "_client_loaded_mailto",
    "planner_shipment_notify_client",
]
