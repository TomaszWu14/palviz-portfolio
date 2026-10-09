# Transakcja kontroli: karta HU, start/next, bramki wymagan, wyjatki MD (Fala 4), finalizacja, reopen.

from ui.views.core import (
    GROUP_ADMIN, GROUP_LEADER, HandlingUnit, HandlingUnitItem, HttpResponse,
    _controller, get_object_or_404, has_role, messages, redirect, render,
    require_POST,
)
from django.utils import timezone
from django.db import transaction
from django.urls import reverse
from urllib.parse import urlencode
from .hu_helpers import _ensure_started  # noqa: F401
from .hu_count import _alt_conv, _annotate_picked_units, _count_tiles, _prefetch_instructions, _unit_factors  # noqa: F401
from .hu_helpers import _call_queue, _is_gls, _log_status, _maybe_escalate_recheck, _notify_groups, _raise_corrective_tasks, _reserve, _type_controlled, _valid_photo, _zone_ok  # noqa: F401
from .hu_quality import _notify_recheck  # noqa: F401
from .hu_queue import _recipient_siblings  # noqa: F401
from .hu_transaction_final import _dispatched  # noqa: F401

@_controller
def hu_control_detail(request, pk):
    hu = get_object_or_404(
        HandlingUnit.objects.select_related("shipment", "shipment__customer",
                                            "controlled_by", "assigned_to",
                                            "client_reqs_confirmed_by"), pk=pk)
    if not _zone_ok(request.user, hu):
        messages.error(request, f"Brak uprawnień do kontroli w strefie „{hu.warehouse_type or '—'}”.")
        return redirect("ui:hu_control_menu")
    # Kontrola startuje TYLKO na jawny POST (hu_control_start) — GET jest read-only, żeby
    # sam podgląd (lider) ani prefetch nie przejmował HU / nie flipował statusu (P5).
    takeover_by = None
    can_start = hu.status == "planned"          # pokaż przycisk „Rozpocznij kontrolę”
    if hu.status == "in_control" and hu.controlled_by and hu.controlled_by != request.user:
        takeover_by = hu.controlled_by          # someone else holds it → offer take-over
    # Soft-assign: paleta ZAREZERWOWANA (jeszcze nie liczona) przez innego kontrolera —
    # widoczna, z opcją miękkiego przejęcia rezerwacji (zostaje 'planned' u przejmującego).
    reserved_by = None
    if hu.status == "planned" and hu.assigned_to_id and hu.assigned_to_id != request.user.id:
        reserved_by = hu.assigned_to
        can_start = False           # najpierw świadome przejęcie rezerwacji (powiadomi #1)
    recheck = hu.status == "to_recheck"
    locked = hu.status == "ok"                   # verified HUs are view-only (re-open via admin)
    # Show all positions as cards; on re-control show only the erroneous ones,
    # unless the operator asked for a full recount (?full=1).
    full = recheck and request.GET.get("full") == "1"
    items = hu.items.all()
    if recheck and not full:
        items = items.filter(result="error")
    done, total = hu.progress()
    items = list(items)
    _prefetch_instructions(items)                 # jedno zapytanie na ekran zamiast 2× na pozycję
    _annotate_picked_units(items, hu)             # podpowiedź jednostki pobrania (pulsujący kafel)
    from ..rules import expiry_alert
    today = timezone.localdate()
    # Reguły pakowania klient×indeks (np. 25 szt/opak. zamiast luzem) → plakietka
    # przy pozycji. Jedno zapytanie na HU, lookup po product_id.
    _cust = getattr(hu.shipment, "customer", None) if hu.shipment_id else None
    _pack_rules = {}
    if _cust:
        from ui.models import CustomerPackagingRule
        _pack_rules = {r.product_id: r for r in CustomerPackagingRule.objects.filter(
            customer=_cust, is_active=True,
            product_id__in=[it.product_id for it in items if it.product_id])}
    for it in items:                              # live right-hand unit (KAR/OPZ) + factor
        it.pack_rule = _pack_rules.get(it.product_id)
        it.alt_label, it.conv_ppc = _alt_conv(it)
        it.factors = _unit_factors(it)            # OPZ / KAR / PAL converter tiles
        # Expiry alert (huctl.rules): czerwony = krótki termin (próg klienta albo 6 mies.),
        # pomarańczowy = poniżej 12 mies. — ta sama reguła co kolejka i księgowanie.
        it.exp_alert = expiry_alert(it.expiry, _cust, today)
        # Pending discrepancy confirmation (entered qty ≠ HU qty, awaiting "jestem pewien").
        pend = request.session.get(f"hu_confirm_{it.pk}")
        it.needs_confirm = bool(pend)
        # Addytywne: prefill kafli z zapisanych składników (base/opz/kar/pal).
        it.confirm_units = (pend.get("units") or {}) if pend else {}
        it.confirm_flags = set(pend.get("flags") or []) if pend else set()
        it.tiles_shown, it.tiles_hidden = _count_tiles(it)
        it.auto_open = False
    # Auto-otwarcie PIERWSZEJ niesprawdzonej pozycji (grill 2026-09-05, pyt. 2/8):
    # operator nie tapie w każdy wiersz — ekran sam wskazuje, co liczyć teraz.
    # Nie przy pending-confirm (tam otwiera się pozycja z ostrzeżeniem).
    if not any(it.needs_confirm for it in items):
        nxt = next((it for it in items if not it.controlled), None)
        if nxt is not None:
            nxt.auto_open = True
    # Podgląd poziomów opakowań (MATinfo) per pozycja — lokalnie, jeśli ta instancja
    # serwuje moduł phv; inaczej link na instancję, gdzie phv żyje (wariant Kontrola HU
    # nie serwuje phv). Gdy rola nie ma dostępu — brak przycisku (md_preview_base=None).
    from ui.platform_modules import can_open_module, module_external_urls
    md_preview_base = None
    if can_open_module(request.user, "phv"):
        md_preview_base = reverse("ui:phv_home")
    elif can_open_module(request.user, "phv", ignore_variant=True):
        md_preview_base = module_external_urls().get("phv")

    detail_url = reverse("ui:hu_control_detail", args=[hu.pk])
    from ui.notifications import leader_target
    leader_msg_url = reverse("ui:message_compose") + "?" + urlencode({
        "to": leader_target(request.user),   # przypisany lider (G) albo cała grupa
        "ctx": f"HU {hu.code or hu.ref}",
        "url": detail_url, "next": detail_url})
    # Panel „Grupa do odb." — inne HU tego samego odbiorcy (D4/D9).
    recipient_label, recipient_kunnr, sibling_hus = _recipient_siblings(hu, request.user)
    # Wyjaśnianie błędu (spec UX §3): trwające + czy jest potwierdzony błąd do startu.
    from ..models_control import HUErrorInvestigation
    open_investigation = hu.investigations.filter(ended_at__isnull=True)\
        .select_related("confirmed_by", "item").first()
    has_confirmed_error = any(it.controlled and it.result == "error" for it in items)
    return render(request, "ui/scanner/hu_detail.html", {
        "hu": hu, "items": items, "all_items": list(hu.items.all()),
        "open_investigation": open_investigation,
        "has_confirmed_error": has_confirmed_error,
        "investigation_types": HUErrorInvestigation.TYPES,
        # Źródło wejścia tej HU (scan/keyboard) — do formularzy liczenia, żeby akcje
        # kolejkowane OFFLINE niosły input_source do hu_control_sync (audyt + bramka).
        "scan_src": request.session.get(f"hu_scan_src_{hu.pk}", ""),
        "leader_msg_url": leader_msg_url, "md_preview_base": md_preview_base,
        "recipient_label": recipient_label, "recipient_kunnr": recipient_kunnr,
        "sibling_hus": sibling_hus,
        "customer": getattr(hu.shipment, "customer", None),   # shipping conditions → header badges
        "done": done, "total": total, "recheck": recheck, "full": full,
        "locked": locked, "takeover_by": takeover_by, "reserved_by": reserved_by,
        "can_start": can_start,
        "dispatched": _dispatched(hu.shipment) if hu.shipment_id else False,   # G2: blok reopen
        "is_leader": has_role(request.user, GROUP_ADMIN, GROUP_LEADER),
        # Zdjęcia obecności (bramka foto) — tylko dla lidera, do spot-audytu. Ostatnie kilka.
        "control_photos": (list(hu.control_photos.select_related("user")[:8])
                           if has_role(request.user, GROUP_ADMIN, GROUP_LEADER) else []),
        "flags": HandlingUnitItem.ACTIVE_ERROR_FLAGS,   # UI wejścia: bez kodów wygaszonych
        "open_issues": list(hu.quality_issues.filter(status="open").select_related("item")),
        "foreign_count": hu.quality_issues.filter(issue_type="foreign_item").count(),
        "md_aspects": MD_ASPECTS,
        # Special customer requirements the controller must confirm before posting.
        "client_reqs": hu.client_requirement_lines(),
        "reqs_confirmed": bool(hu.client_reqs_confirmed_at),
        # Krótki termin ważności (F6/F7): pozycje poniżej progu + stan potwierdzenia.
        "short_dated": hu.short_dated_items(today=today),
        "short_dated_ack": bool(hu.short_dated_ack_at),
        "min_shelf_months": hu.min_shelf_life_months(),
        # Suma jednostek / waga / objętość NIE trafiają na ekran liczenia — podpowiadały
        # spodziewaną ilość, a kontrola ma być liczeniem, nie potwierdzaniem sugestii.
        # Te liczby zostają na liście HU i w panelu lidera.
        # Fala 5: przesyłka jeszcze w trakcie pickingu (feed SAP) — ostrzeż kontrolera.
        "shipment_incomplete": bool(hu.shipment_id and not hu.shipment.picking_complete),
        # BIZ-009: wysyłka anulowana — HU nie ma w kolejce; kontroler widzi to na karcie.
        "shipment_cancelled": bool(hu.shipment_id and hu.shipment.status == "cancelled"),
        # Strefa GLS: formularz księgowania wymaga rozliczenia kartony→paczki.
        "is_gls": _is_gls(hu),
    })



@_controller
@require_POST
def hu_control_start(request, pk):
    """Jawny start kontroli (planned→in_control) — zamiast efektu ubocznego na GET (P5).
    Egzekwuje strefę i kontrolowany typ (deep-link nie obchodzi zakresu)."""
    hu = get_object_or_404(HandlingUnit, pk=pk)
    if not _zone_ok(request.user, hu):
        messages.error(request, f"Brak uprawnień do kontroli w strefie „{hu.warehouse_type or '—'}”.")
        return redirect("ui:hu_control_menu")
    if not _type_controlled(hu):
        messages.error(request, "Ten typ magazynu nie jest objęty kontrolą HU.")
        return redirect("ui:hu_control_menu")
    with transaction.atomic():
        hu = HandlingUnit.objects.select_for_update().get(pk=pk)
        if hu.status == "planned":
            hu.status = "in_control"
            hu.controlled_by = request.user
            hu.control_started_at = timezone.now()
            hu.save(update_fields=["status", "controlled_by", "control_started_at"])
            _log_status(hu, "planned", "in_control", request.user, "start kontroli")
    return redirect("ui:hu_control_detail", pk=pk)


@_controller
def hu_control_next(request):
    """Auto-przydział z WSPÓLNEJ kolejki (BIZ-005): ten sam wybór co „Weź następną" —
    moja paleta w kontroli, potem moje rezerwacje, potem ranking. Rezerwuje pod lockiem."""
    from .hu_dashboard import _own_active, _take_first
    # Bilans obciążenia (spec): kontroler trzymający aktywną paletę (in_control) wraca do
    # niej zamiast zdejmować kolejne czoło kolejki — kolejka rozkłada się na wolnych.
    active = _own_active(request.user)
    if active:
        messages.info(request, f"Masz już paletę w kontroli ({active.ref}) — dokończ ją.")
        return redirect("ui:hu_control_detail", pk=active.pk)
    hu = _take_first(request, "wywołanie (następna)")
    if not hu:
        messages.info(request, "Brak HU do kontroli w Twojej strefie.")
        return redirect("ui:hu_control_menu")
    return redirect("ui:hu_control_detail", pk=hu.pk)


@_controller
def hu_logistics_label(request, pk):
    """Etykieta logistyczna HU (mini-wywiad 2026-07-30): ZPL 150×100 z zawartością
    palety, danymi odbiorcy z wysyłki i notatkami klienta — drukowana na życzenie
    PO kontroli. Dostępna tylko, gdy klient ma włączoną flagę w master dacie."""
    from ui.labels import zpl_logistics_label
    hu = get_object_or_404(
        HandlingUnit.objects.select_related("shipment", "shipment__customer")
        .prefetch_related("items"), pk=pk)
    if not _zone_ok(request.user, hu):
        messages.error(request, f"Brak uprawnień do strefy „{hu.warehouse_type or '—'}”.")
        return redirect("ui:hu_control_menu")
    if hu.status != "ok":
        # Etykieta dopiero PO zaksięgowaniu (mini-wywiad) — przycisk jest za {% if locked %},
        # ale bezpośredni URL też musi być zamknięty.
        messages.error(request, "Etykietę logistyczną drukuje się po zaksięgowaniu kontroli HU.")
        return redirect("ui:hu_control_detail", pk=pk)
    customer = getattr(hu.shipment, "customer", None)
    if not (customer and customer.requires_logistics_label):
        messages.error(request, "Klient nie ma włączonej etykiety logistycznej (master data).")
        return redirect("ui:hu_control_detail", pk=pk)
    zpl = zpl_logistics_label(hu, customer)
    resp = HttpResponse(zpl, content_type="text/plain; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="etykieta_logistyczna_{hu.ref}.zpl"'
    return resp


# ── Fala 4: niezgodności master daty per pozycja ──────────────────────────────

# „Czego dotyczy" niezgodność master daty (formularz na wzór MATINFO).
MD_ASPECTS = [("waga", "Waga"), ("wymiary", "Wymiary"), ("przelicznik", "Przelicznik"),
              ("ajm", "AJM — wskaż przelicznik (np. 1 KARTON = 10 OP)"),
              ("opis", "Opis / nazwa"), ("inne", "Inne")]

__all__ = [
    "hu_control_detail",
    "hu_control_start",
    "hu_control_next",
    "hu_logistics_label",
    "MD_ASPECTS",
]
