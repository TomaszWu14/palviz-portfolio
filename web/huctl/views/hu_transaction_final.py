# Transakcja kontroli: karta HU, start/next, bramki wymagan, wyjatki MD (Fala 4), finalizacja, reopen.

import logging
from ui.views.core import (
    HandlingUnit, HUQualityIssue, _controller, _leader, get_object_or_404,
    messages, redirect, require_POST,
)
from django.utils import timezone
from django.db import transaction
from django.urls import reverse
from ui.hu_metrics import hu_metrics
from .hu_helpers import _ensure_started  # noqa: F401
from .hu_count import _alt_conv, _annotate_picked_units, _count_tiles, _prefetch_instructions, _unit_factors  # noqa: F401
from .hu_helpers import _call_queue, _is_gls, _log_status, _maybe_escalate_recheck, _notify_groups, _raise_corrective_tasks, _reserve, _type_controlled, _valid_photo, _zone_ok  # noqa: F401
from .hu_quality import _notify_recheck  # noqa: F401
from .hu_queue import _recipient_siblings  # noqa: F401

log = logging.getLogger(__name__)

@_controller
@require_POST
def hu_control_confirm_reqs(request, pk):
    """Controller confirms they've met the customer's special delivery requirements.
    Records who/when; posting (finalize) is blocked until this is done for HUs whose
    customer/shipment carries such requirements."""
    hu = get_object_or_404(
        HandlingUnit.objects.select_related("shipment", "shipment__customer"), pk=pk)
    if not _zone_ok(request.user, hu):
        messages.error(request, f"Brak uprawnień do kontroli w strefie „{hu.warehouse_type or '—'}”.")
        return redirect("ui:hu_control_menu")
    if hu.status == "ok":
        messages.error(request, "HU jest zgodny i zablokowany — tylko podgląd.")
    elif hu.has_client_requirements:
        # F8: potwierdź RAZ na wysyłkę — propaguj na wszystkie jej HU (poza już zaksięgowanymi),
        # żeby nie potwierdzać tego samego na każdej palecie. Znacznik per-HU zostaje dla audytu.
        now = timezone.now()
        (HandlingUnit.objects.filter(shipment_id=hu.shipment_id)
         .exclude(status__in=("ok", "escaped"))
         .update(client_reqs_confirmed_at=now, client_reqs_confirmed_by=request.user))
        messages.success(request, "Potwierdzono wymagania klienta dla całej wysyłki.")
    return redirect("ui:hu_control_detail", pk=pk)


@_controller
@require_POST
def hu_control_ack_short_dated(request, pk):
    """Świadome potwierdzenie pozycji z krótkim terminem ważności (F7). Propaguje na całą
    wysyłkę (F8) — znacznik per-HU zostaje dla audytu."""
    hu = get_object_or_404(HandlingUnit.objects.select_related("shipment"), pk=pk)
    if not _zone_ok(request.user, hu):
        messages.error(request, f"Brak uprawnień do kontroli w strefie „{hu.warehouse_type or '—'}”.")
        return redirect("ui:hu_control_menu")
    if hu.status == "ok":
        messages.error(request, "HU jest zgodny i zablokowany — tylko podgląd.")
    else:
        now = timezone.now()
        (HandlingUnit.objects.filter(shipment_id=hu.shipment_id)
         .exclude(status__in=("ok", "escaped"))
         .update(short_dated_ack_at=now, short_dated_ack_by=request.user))
        messages.success(request, "Potwierdzono krótki termin ważności dla wysyłki.")
    return redirect("ui:hu_control_detail", pk=pk)




def _release_for_recheck(hu):
    """Rekontrolę robi INNY kontroler, więc rezerwacja liczącego (assigned_to + called_at
    z „Weź następną”/wywołania) nie może trzymać palety: kolejka pokazuje innym tylko
    wolne HU, a sweep porzuconych rezerwacji sprząta wyłącznie „planned” — bez tego
    to_recheck wisiała u kontrolera, który nie może jej przeliczyć."""
    hu.assigned_to = None
    hu.called_at = None


@_controller
@require_POST
@transaction.atomic
def hu_control_finalize(request, pk):
    # Lock od razu: bramka status=="ok" musi widzieć ŚWIEŻY wiersz, inaczej dwa równoległe
    # finalize oba ją mijają i drugi księguje ponownie (podwójny notify + nadpisany verified_at).
    try:
        # of=("self",) — blokada tylko na wierszu HandlingUnit; inaczej PostgreSQL rzuca
        # „FOR UPDATE cannot be applied to the nullable side of an outer join" (LEFT JOIN
        # po nullowalnym shipment). SQLite ignoruje FOR UPDATE, więc bug był ukryty.
        hu = (HandlingUnit.objects.select_for_update(of=("self",))
              .select_related("shipment", "shipment__customer").get(pk=pk))
    except HandlingUnit.DoesNotExist:
        from django.http import Http404
        raise Http404 from None
    if not _zone_ok(request.user, hu):
        messages.error(request, f"Brak uprawnień do kontroli w strefie „{hu.warehouse_type or '—'}”.")
        return redirect("ui:hu_control_menu")
    # Blokada jak w hu_control_count i offline-sync (hu_locked): 'escaped' też jest ZAMKNIĘTY.
    # Bez 'escaped' kontroler mógł POST-em finalize „odksięgować" wyjechały HU (decyzja lidera)
    # i zaksięgować go jako OK — cofnięcie leader-only decyzji przez endpoint kontrolera.
    if hu.status in ("ok", "escaped"):
        messages.error(request, "HU jest zamknięty (zgodny / wyjechało bez kontroli) — tylko podgląd.")
        return redirect("ui:hu_control_detail", pk=pk)
    if not _type_controlled(hu):                   # spójność bramki typów (deep-link)
        messages.error(request, "Ten typ magazynu nie jest objęty kontrolą HU.")
        return redirect("ui:hu_control_menu")
    _old_status = hu.status
    items = hu.items.all()
    if not items.exists():
        messages.error(request, "Nie zaksięgowano — HU nie ma pozycji.")
        return redirect("ui:hu_control_detail", pk=pk)
    # Liczenie KAŻDEJ pozycji obowiązkowe: nie ma już auto-OK niepoliczonych (dawna
    # furtka confirm_untouched usunięta). Dopóki którakolwiek pozycja jest niepoliczona,
    # HU nie da się zaksięgować — twardy ślad, że kontroler naprawdę policzył wszystko.
    untouched = items.filter(controlled=False).count()
    if untouched:
        messages.error(request, f"Nie zaksięgowano — {untouched} pozycji niepoliczonych. "
                                "Policz każdą pozycję przed zamknięciem HU.")
        return redirect("ui:hu_control_detail", pk=pk)

    # Special customer requirements must be explicitly acknowledged before posting.
    if hu.has_client_requirements and not hu.client_reqs_confirmed_at:
        messages.error(request, "Nie zaksięgowano — najpierw potwierdź spełnienie szczególnych "
                                "wymagań klienta.")
        return redirect("ui:hu_control_detail", pk=pk)

    today = timezone.localdate()
    # Krótki termin ważności (F6/F7): poniżej progu, ale jeszcze ważne. Miękka bramka —
    # świadome potwierdzenie (short_dated_ack) ALBO oflagowanie wrong_expiry. Nie blokuje
    # twardo (inaczej niż w pełni przeterminowane niżej).
    short_dated = hu.short_dated_items(today=today)
    if short_dated and not hu.short_dated_ack_at:
        unflagged = [it for it in short_dated if not (it.error_flags or {}).get("wrong_expiry")]
        if unflagged:
            messages.error(request, f"Nie zaksięgowano — {len(unflagged)} pozycji z krótkim terminem "
                                    "ważności. Potwierdź świadomie albo oflaguj błędną datę.")
            return redirect(reverse("ui:hu_control_detail", args=[pk]) + "?short_dated=1")

    # Expired lots block the HU: raise a quality issue per expired item and send it to
    # re-control (which, combined with the rule below, keeps it blocked until resolved).
    expired = [it for it in items if it.expiry and it.expiry < today]
    if expired:
        for it in expired:
            HUQualityIssue.objects.get_or_create(
                hu=hu, item=it, issue_type="wrong_expiry", status="open",
                defaults={"raised_by": request.user, "note": f"Po terminie ważności: {it.expiry}"})
        hu.status = "to_recheck"
        hu.verified_at = None
        hu.is_priority = True
        _release_for_recheck(hu)
        hu.save(update_fields=["status", "verified_at", "is_priority", "assigned_to", "called_at"])
        _log_status(hu, _old_status, "to_recheck", request.user,
                    f"pozycje po terminie ważności ({len(expired)})")
        _notify_recheck(hu, request.user)
        _maybe_escalate_recheck(hu, request.user)         # F10
        messages.error(request, f"HU zablokowany — pozycje po terminie ważności ({len(expired)}). "
                                f"Utworzono zgłoszenia jakościowe.")
        return redirect("ui:hu_control_detail", pk=pk)

    # An open quality issue blocks posting — it must be resolved/closed first.
    open_q = hu.quality_issues.filter(status="open").count()
    if open_q:
        messages.error(request, f"Nie zaksięgowano — najpierw zamknij otwarte zgłoszenia "
                                f"jakościowe ({open_q}).")
        return redirect("ui:hu_control_detail", pk=pk)

    # Strefa GLS: księgowanie wymaga rozliczenia konsolidacji (kartony → paczki) —
    # zasila raport wydajności lidera. Bramka PRZED mutacjami pozycji (jak pozostałe).
    gls_cartons = gls_parcels = None
    if _is_gls(hu):
        try:
            gls_cartons = int(request.POST.get("gls_cartons", ""))
            gls_parcels = int(request.POST.get("gls_parcels", ""))
        except (TypeError, ValueError):
            gls_cartons = gls_parcels = None
        if not gls_cartons or not gls_parcels or gls_cartons < 1 or gls_parcels < 1:
            messages.error(request, "Nie zaksięgowano — strefa GLS wymaga podania liczby "
                                    "kartonów w przygotowaniu i zrobionych paczek.")
            return redirect("ui:hu_control_detail", pk=pk)

    # Wszystkie bramki przeszły. Każda pozycja jest już policzona (bramka „untouched"
    # wyżej blokuje księgowanie, dopóki tak nie jest) — brak auto-OK niepoliczonych.
    has_error = items.filter(result="error").exists()

    if has_error:
        hu.status = "to_recheck"                 # blocked, awaits re-control
        hu.verified_at = None                    # a blocked HU is not "verified"
        hu.is_priority = True                    # discrepancy → priority for re-control
        _release_for_recheck(hu)
        msg = "Zaksięgowano. Wykryto rozbieżności → status: DO REKONTROLI (HU zablokowany)."
    else:
        hu.status = "ok"
        hu.verified_at = timezone.now()
        hu.is_priority = False                   # cleared once it's verified OK
        msg = "Kontrola zaksięgowana — HU zgodny."
    hu.save(update_fields=["status", "verified_at", "is_priority", "assigned_to", "called_at"])
    _log_status(hu, _old_status, hu.status, request.user, "zaksięgowanie kontroli")
    # Tylko przy PRZEJŚCIU w to_recheck — inaczej re-submit (double-click/back/offline replay)
    # nierozwiązanego to_recheck ponawiał notyfikacje lidera i zadania naprawcze (brak dedupu w _notify_recheck).
    # Notify/mail PO commicie: SMTP pod trzymanym FOR UPDATE blokował wiersz HU innym
    # kontrolerom, a rollback po wysłanym mailu = mail-widmo + drugi mail przy retry.
    if has_error and _old_status != "to_recheck":
        transaction.on_commit(lambda: (_notify_recheck(hu, request.user),
                                       _raise_corrective_tasks(hu, request.user),
                                       _maybe_escalate_recheck(hu, request.user)))
    elif hu.status == "ok":
        # Live readiness: if this scan completed the last pallet, alert the planners once.
        from ui.notifications import notify_shipment_ready
        transaction.on_commit(lambda: notify_shipment_ready(hu.shipment))
    if gls_cartons and gls_parcels:
        from ui.models import GlsPackingEntry
        vol = hu_metrics([hu]).get(hu.pk, {}).get("volume_m3")
        GlsPackingEntry.objects.update_or_create(
            hu=hu, defaults={"controller": request.user, "cartons": gls_cartons,
                             "parcels": gls_parcels, "volume_m3": vol})
    messages.success(request, msg)
    return redirect("ui:hu_control_menu")


def _dispatched(shipment):
    """True gdy wysyłka już wyjechała (status 'sent' albo odbiór potwierdzony) — towaru
    fizycznie nie ma, więc re-kontrola nie ma sensu (G2)."""
    if getattr(shipment, "status", "") == "sent":
        return True
    da = getattr(shipment, "driver", None)
    return bool(da and getattr(da, "pickup_status", "") == "confirmed")


def _maybe_withdraw_readiness(shipment, user):
    """G3: gdy wysyłka była zgłoszona jako gotowa, a już nie jest (np. po reopen) — cofnij
    znacznik (ponowne ukończenie znowu powiadomi) i alertuj planistów/Transport."""
    if not shipment.ready_notified_at or shipment.hu_checked_ready():
        return
    shipment.ready_notified_at = None
    shipment.save(update_fields=["ready_notified_at"])
    try:
        from ui.notifications import notify, creator_and_transport
        notify(creator_and_transport(shipment),
               f"Wysyłka już NIE gotowa — {shipment.name}",
               body="HU otwarto ponownie do kontroli — wysyłka wróciła do przygotowania.",
               level="warning", url=reverse("ui:planner_shipment_detail", args=[shipment.id]))
    except Exception:
        log.exception("Powiadomienie o cofnięciu gotowości wysyłki nie wysłane")


@_leader
@require_POST
def hu_control_reopen(request, pk):
    """Service privilege (leader/admin): re-open a verified (ok) HU for re-control (G2)."""
    hu = get_object_or_404(HandlingUnit.objects.select_related("shipment", "shipment__driver"), pk=pk)
    if hu.status != "ok":
        return redirect("ui:hu_control_detail", pk=pk)
    if _dispatched(hu.shipment):                          # towar wyjechał → nie da się re-kontrolować
        messages.error(request, "Nie można otworzyć ponownie — wysyłka została już wysłana / odebrana.")
        return redirect("ui:hu_control_detail", pk=pk)
    reason = (request.POST.get("reason") or "").strip()[:120]
    if not reason:                                        # od-weryfikowanie jest istotne → wymagaj powodu
        messages.error(request, "Podaj powód ponownego otwarcia (od-weryfikowanie HU).")
        return redirect("ui:hu_control_detail", pk=pk)
    with transaction.atomic():
        hu = HandlingUnit.objects.select_for_update().get(pk=pk)
        # Re-check pod lockiem (TOCTOU): równoległa dyspozycja/wysyłka między bramką a lockiem.
        if hu.status != "ok" or _dispatched(hu.shipment):
            messages.error(request, "Stan HU zmienił się w międzyczasie — otwarcie anulowane.")
            return redirect("ui:hu_control_detail", pk=pk)
        hu.status = "in_control"
        hu.controlled_by = request.user
        hu.verified_at = None
        # Świeży start kontroli — stary control_started_at (sprzed godzin) sprawiał, że
        # sweep porzuconych kontroli (4 h) od razu cofał otwartą HU do 'planned'.
        hu.control_started_at = timezone.now()
        hu.save(update_fields=["status", "controlled_by", "verified_at", "control_started_at"])
        _log_status(hu, "ok", "in_control", request.user, f"otwarcie ponowne przez lidera — {reason}")
    _maybe_withdraw_readiness(hu.shipment, request.user)  # G3
    messages.success(request, "HU otwarty ponownie do kontroli.")
    return redirect("ui:hu_control_detail", pk=pk)

__all__ = [
    "hu_control_confirm_reqs",
    "hu_control_ack_short_dated",
    "hu_control_finalize",
    "hu_control_reopen",
    "_dispatched",
    "_maybe_withdraw_readiness",
]
