# Carrier selection + driver data + driver pickup confirmation (SMS via Twilio).
import re
from ui.views.core import (
    DriverAssignment, Shipment, ShipmentQuoteOffer, SiteInfo,
    _transport_mgr,
    get_object_or_404, messages, redirect, render, require_POST, transaction,
)
from core.ratelimit import post_rate_limit
from django.urls import reverse
from django.utils import timezone
from transport.pallet_count import shipment_pallet_calc
from transport.sms import send_sms as _send_sms  # alias: stare wołania/patche testów
from ui.views.core.base import _pk4  # int4-safe pk (nienumeryczny/overflow → 404, nie DataError 500)

# Telefon do SMS: E.164-ish (opcjonalny +, 7–15 cyfr). Publiczny driver_form nie może
# słać SMS na dowolny łańcuch — inaczej link (token w mailu) = otwarty relay Twilio.
_PHONE_RE = re.compile(r"^\+?\d{7,15}$")


@_transport_mgr
@require_POST
def planner_shipment_select_offer(request, pk, offer_id):
    """Choose a forwarder's offer → create the driver assignment so the forwarder can
    fill the driver data."""
    shipment = get_object_or_404(Shipment, pk=_pk4(pk))
    offer = get_object_or_404(ShipmentQuoteOffer, pk=_pk4(offer_id), shipment=shipment)
    # BIZ-009: wybór tylko złożonej oferty z kwotą, na nieanulowanej wysyłce.
    if shipment.status == "cancelled":
        messages.error(request, "Przesyłka jest anulowana — nie można wybrać oferty spedycji.")
        return redirect("ui:planner_shipment_detail", pk=pk)
    if offer.amount is None or offer.submitted_at is None:
        messages.error(request, "Nie można wybrać tej oferty — spedycja nie złożyła jeszcze "
                                "wyceny z kwotą.")
        return redirect("ui:planner_shipment_detail", pk=pk)
    # Atomowo: odznacz→zaznacz→przypnij. Padnięcie między krokami zostawiało przesyłkę
    # BEZ żadnej wybranej oferty (flow wyceny/kierowcy jedzie po selected_offer()).
    with transaction.atomic():
        shipment.quote_offers.update(selected=False)
        offer.selected = True
        offer.save(update_fields=["selected"])
        da, _ = DriverAssignment.objects.get_or_create(shipment=shipment)
        da.offer = offer
        da.save(update_fields=["offer"])
        shipment.mark_confirmed()      # BIZ-003: wybór oferty → „Zatwierdzone” (automatycznie)
    label = offer.carrier_name or (offer.recipient.name if offer.recipient else "spedycja")
    # Auto-send the order confirmation + driver-data form to the forwarder. Only the
    # mailto button existed before, so nothing actually left the server on confirm.
    from .shipments import _send_driver_form_email
    ok, info = _send_driver_form_email(request, shipment)
    if ok:
        messages.success(request, f"Wybrano spedycję: {label}. Wysłano potwierdzenie z formularzem "
                                  f"danych kierowcy ({info}).")
        return redirect("ui:planner_shipment_detail", pk=pk)
    # Server didn't send. If the forwarder has an e-mail, fall back to Outlook (?driver_mail=1
    # auto-launches the mailto); otherwise surface the real reason rather than a fake success.
    if offer.recipient and offer.recipient.email:
        messages.success(request, f"Wybrano spedycję: {label}. Otwieram potwierdzenie w Outlooku — wyślij e-mail.")
        return redirect(reverse("ui:planner_shipment_detail", args=[pk]) + "?driver_mail=1")
    messages.error(request, f"Wybrano spedycję: {label}, ale nie wysłano potwierdzenia — {info} "
                            "Uzupełnij adres e-mail spedycji w bazie adresowej wycen.")
    return redirect("ui:planner_shipment_detail", pk=pk)


@_transport_mgr
@require_POST
def planner_shipment_driver_send(request, pk):
    """(Re)send the order confirmation + driver-data form to the chosen forwarder."""
    shipment = get_object_or_404(Shipment, pk=_pk4(pk))
    from .shipments import _send_driver_form_email
    ok, info = _send_driver_form_email(request, shipment)
    if ok:
        messages.success(request, f"Wysłano potwierdzenie do spedycji ({info}).")
    else:
        messages.error(request, f"Nie wysłano e-maila — {info}")
    return redirect("ui:planner_shipment_detail", pk=pk)


@_transport_mgr
@require_POST
def planner_shipment_select_scenario(request, pk):
    """Confirm one pallet-height scenario for the quote + warehouse build (or clear it).
    The other scenario is then frozen in the UI so the warehouse knows which version to
    prepare. The view's height/efficiency params are preserved across the redirect."""
    shipment = get_object_or_404(Shipment, pk=_pk4(pk))
    h = (request.POST.get("height") or "").strip()
    if h == "clear":
        shipment.selected_pallet_height_cm = None
        shipment.save(update_fields=["selected_pallet_height_cm"])
        messages.success(request, "Wybór wyczyszczony — oba scenariusze znów aktywne.")
    else:
        try:
            cm = int(h)
        except (TypeError, ValueError):
            cm = 0
        if cm:
            shipment.selected_pallet_height_cm = cm
            shipment.save(update_fields=["selected_pallet_height_cm"])
            messages.success(
                request, f"Wybrano scenariusz {cm/100:.1f} m do wyceny i realizacji — "
                         "magazyn przygotuje tę wersję.")
    params = {k: request.POST.get(k) for k in ("h1", "h2", "eff") if request.POST.get(k)}
    url = reverse("ui:planner_shipment_detail", args=[pk])
    if params:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(params)}"
    return redirect(url)


@_transport_mgr
@require_POST
def planner_shipment_send_driver_sms(request, pk):
    """Send (or re-send) the pickup-confirmation SMS to the driver."""
    shipment = get_object_or_404(Shipment, pk=_pk4(pk))
    da = get_object_or_404(DriverAssignment, shipment=shipment)
    if not da.driver_phone:
        messages.error(request, "Brak numeru telefonu kierowcy — spedycja musi uzupełnić formularz.")
        return redirect("ui:planner_shipment_detail", pk=pk)
    link = request.build_absolute_uri(reverse("ui:driver_confirm", args=[da.confirm_token]))
    # Enrich the SMS with the pickup date, road distance (Google, best-effort) and the
    # forwarder's delivery ETA, so the driver gets the whole job in one message.
    from django.conf import settings
    from .shipments import _google_route
    offer = da.offer
    origin = getattr(settings, "SHIPMENT_ORIGIN_ADDRESS", "") or ""
    km, _ = _google_route(origin, shipment.destination_address()) if shipment.destination_address() else (None, None)
    parts = [f"Odbiór {shipment.name}"]
    if offer and offer.truck_date:
        parts.append(f"podstawienie {offer.truck_date:%d.%m.%Y}")
    dest = shipment.destination_city or shipment.destination_country or ""
    if dest:
        parts.append(f"do: {dest}" + (f" (~{km} km)" if km else ""))
    if offer and offer.delivery_date:
        parts.append(f"dostawa ~{offer.delivery_date:%d.%m.%Y}")
    body = ". ".join(parts) + f". Potwierdź gotowość/odbiór: {link}"
    sent = _send_sms(da.driver_phone, body)
    da.sms_count += 1
    da.sms_last_at = timezone.now()
    da.save(update_fields=["sms_count", "sms_last_at"])
    if sent:
        messages.success(request, f"Wysłano SMS do kierowcy ({da.driver_phone}). Ponaglenie #{da.sms_count}.")
    else:
        messages.warning(request, "Brama SMS nieskonfigurowana — skopiuj link i wyślij ręcznie: " + link)
    return redirect("ui:planner_shipment_detail", pk=pk)


# SEC-013: linki kierowcy (formularz spedycji, potwierdzenie odbioru) wygasają,
# gdy przesyłka jest zamknięta albo minęło DRIVER_LINK_TTL_DAYS od przydziału — dane
# kierowcy nie wiszą bezterminowo pod publicznym adresem.
_CLOSED_SHIPMENT = ("sent", "cancelled")


def _link_expired(request, da):
    from datetime import timedelta
    from django.conf import settings as _st
    ttl = int(getattr(_st, "DRIVER_LINK_TTL_DAYS", 30) or 0)
    expired = da.shipment.status in _CLOSED_SHIPMENT or (
        ttl and da.created_at and da.created_at < timezone.now() - timedelta(days=ttl))
    return render(request, "ui/link_expired.html", status=410) if expired else None


@post_rate_limit("driver_form", 20, 3600, by="token")   # SEC-014
def driver_form(request, token):
    """Public form for the chosen forwarder to enter driver data (few required)."""
    da = get_object_or_404(DriverAssignment.objects.select_related("shipment"), form_token=token)
    if (gone := _link_expired(request, da)):
        return gone
    if request.method == "POST":
        # Zapamiętaj telefon SPRZED zapisu — SMS wysyłamy tylko przy pierwszym wypełnieniu
        # albo gdy numer się zmienił. Inaczej odświeżenie/podwójny klik = drugi SMS (koszt Twilio).
        _prev_phone = da.driver_phone
        _already_smsd = da.sms_count > 0
        da.driver_name = (request.POST.get("driver_name") or "")[:120]
        da.driver_plate = (request.POST.get("driver_plate") or "")[:20]
        da.driver_phone = (request.POST.get("driver_phone") or "")[:32]
        da.driver_language = (request.POST.get("driver_language") or "pl")[:20]
        from django.utils.dateparse import parse_datetime
        eta = parse_datetime(request.POST.get("truck_eta") or "")
        if eta and timezone.is_naive(eta):
            eta = timezone.make_aware(eta)
        da.truck_eta = eta
        da.filled_at = timezone.now()
        da.save()
        # SMS tylko: pierwszy raz LUB zmieniony numer, ale ZAWSZE pod twardym capem per
        # assignment (anty-pumping — publiczny formularz z tokenem nie może być relayem SMS)
        # i tylko na poprawny numer E.164.
        from django.conf import settings as _st
        _cap = getattr(_st, "DRIVER_FORM_SMS_MAX", 3)
        _phone_ok = bool(_PHONE_RE.match((da.driver_phone or "").replace(" ", "")))
        if (da.driver_phone and _phone_ok and da.sms_count < _cap
                and not (_already_smsd and da.driver_phone == _prev_phone)):
            # Atomowy warunkowy UPDATE (F+filter) — równoległe POSTy na publicznym
            # formularzu czytały ten sam sms_count i KAŻDY wysyłał SMS mimo capa.
            # SMS idzie tylko, gdy to MY podbiliśmy licznik poniżej capa.
            from django.db.models import F as _F
            claimed = (type(da).objects
                       .filter(pk=da.pk, sms_count__lt=_cap)
                       .update(sms_count=_F("sms_count") + 1, sms_last_at=timezone.now()))
            if claimed:
                link = request.build_absolute_uri(reverse("ui:driver_confirm", args=[da.confirm_token]))
                _send_sms(da.driver_phone, f"Odbiór przesyłki {da.shipment.name} "
                          f"({da.shipment.destination_city}). Szczegóły i potwierdzenie: {link}")
            da.refresh_from_db(fields=["sms_count", "sms_last_at"])
        return render(request, "ui/driver_form.html", {"da": da, "shipment": da.shipment, "saved": True})
    return render(request, "ui/driver_form.html", {"da": da, "shipment": da.shipment})


def _driver_info_context(request, da):
    """The info packet shown to the driver: loading address + ramp + pallet count +
    delivery address + warehouse-leader contacts + site map + movement instructions."""
    from django.conf import settings
    sh = da.shipment
    _, sc = shipment_pallet_calc(sh)              # BIZ-007: ta sama liczba co w wycenie
    return {
        "da": da, "shipment": sh,
        "n_pallets": sc["n_pallets"] if sc else 0,
        "pickup_address": getattr(settings, "SHIPMENT_ORIGIN_ADDRESS", ""),
        "delivery_address": sh.destination_address(),
        "ramp": sh.ramp,
        "site": SiteInfo.current(),
    }


@post_rate_limit("driver_confirm", 20, 3600, by="token")   # SEC-014
def driver_confirm(request, token):
    """Public page (SMS link): the driver's info packet + pickup confirmation."""
    da = get_object_or_404(DriverAssignment.objects.select_related("shipment"), confirm_token=token)
    if (gone := _link_expired(request, da)):
        return gone
    ctx = _driver_info_context(request, da)
    if request.method == "POST":
        ans = request.POST.get("answer")
        if ans in ("confirmed", "declined"):
            da.pickup_status = ans
            da.confirmed_at = timezone.now()
            da.save(update_fields=["pickup_status", "confirmed_at"])
            if ans == "confirmed":
                da.shipment.mark_sent()    # BIZ-003: odbiór potwierdzony → „Wysłane”
        ctx["saved"] = True
    return render(request, "ui/driver_confirm.html", ctx)


__all__ = [
    "planner_shipment_select_offer",
    "planner_shipment_select_scenario",
    "planner_shipment_driver_send",
    "planner_shipment_send_driver_sms",
    "driver_form",
    "driver_confirm",
]
