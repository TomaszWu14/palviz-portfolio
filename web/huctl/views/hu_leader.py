# Panel lidera: live-widok, wiadomosci, przydzialy, przejecia, dyspozycje.

import logging
from ui.views.core import (
    GROUP_CONTROLLER, GROUP_LEADER, HandlingUnit, HandlingUnitItem, HUControlAttempt,
    _controller, _leader, _safe_next, get_object_or_404, messages, redirect, render,
    require_POST, settings,
)
from django.http import Http404  # not re-exported by ui.views.core (07-01 anomaly, fixed here)
from django.utils import timezone
from django.db import transaction
from django.urls import reverse
from .hu_helpers import _attach_recheck_age, _filter_controlled, _log_status, _zone_ok  # noqa: F401
from .hu_queue import _recipient_key_filter  # noqa: F401
from .hu_reports import _kpi_period_bounds, _kpi_stats

log = logging.getLogger(__name__)


@_leader
def hu_control_leader(request):
    """Panel lidera (roadmapa Q8): praca zmiany na żywo, przydzielanie HU kontrolerom,
    zaległe rekontrole z wiekiem. Raporty okresowe → istniejące KPI (period=month, CSV)."""
    from datetime import timedelta
    from django.contrib.auth import get_user_model
    now = timezone.now()
    # Północ LOKALNA, nie UTC — inaczej nocna zmiana 00:00–02:00 (Europe/Warsaw) wypadała
    # z „dziś" (now.replace na aware-UTC = 01/02:00 czasu lokalnego).
    today = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)

    # Na żywo: kto co kontroluje teraz + dzisiejsze liczniki per kontroler.
    # Wszystkie liczniki panelu liczą TYLKO kontrolowane typy magazynu.
    live = list(_filter_controlled(HandlingUnit.objects.filter(status="in_control"))
                .select_related("controlled_by", "shipment").order_by("-verified_at"))
    # Liczniki zmiany z JEDNEGO źródła prawdy (_kpi_stats) — te same liczby co pełny ekran KPI,
    # bez drugiej, rozjeżdżającej się pętli. „active" (<15 min od ostatniej próby) to próg
    # prezentacyjny liczony tu, względem `now`.
    _, start, end = _kpi_period_bounds("today", now=now)
    shift_rows, _tot = _kpi_stats(start, end)
    for r in shift_rows:
        r["active"] = bool(r["last"] and (now - r["last"]) < timedelta(minutes=15))
    # Sygnały nadużyć do osobnej karty — tylko kontrolerzy z podejrzanymi wzorcami.
    abuse_rows = [r for r in shift_rows if r["fast"] or r["keyboard"] or r["offline"]]

    # Zaległe rekontrole z wiekiem (najstarsze pierwsze) + przydział.
    max_age_h = float(getattr(settings, "HU_RECHECK_MAX_AGE_HOURS", 24) or 24)
    rechecks = _attach_recheck_age(list(
        _filter_controlled(HandlingUnit.objects.filter(status="to_recheck"))
        .select_related("assigned_to", "shipment")))

    from .hu_helpers import _without_cancelled_planned   # BIZ-009: bez planned anulowanych
    planned_unassigned = _filter_controlled(_without_cancelled_planned(
        HandlingUnit.objects.filter(status="planned", assigned_to__isnull=True))).count()
    # „Kontrolerów dziś" = kto DOTKNĄŁ kontroli dziś, z jednego zbioru HU (spójnie ze stanami,
    # nie tylko z audytu HUControlAttempt — inaczej „0 przy 14 w kontroli"). Zbiór: kontrolerzy
    # trzymający dzisiejsze/aktywne HU (controlled_by/assigned_to) ∪ autorzy prób audytu dziś.
    from django.db.models import Q as _Q
    # in_control = aktywne teraz; to_recheck liczy się do „dziś" tylko gdy start był dziś —
    # inaczej kontroler przypisany do rekontroli sprzed dni zawyżał licznik.
    _today_hus = _filter_controlled(HandlingUnit.objects.filter(
        _Q(control_started_at__gte=today) | _Q(status="in_control")))
    touched = set(_today_hus.exclude(controlled_by__isnull=True)
                  .values_list("controlled_by", flat=True))
    touched |= set(_today_hus.exclude(assigned_to__isnull=True)
                   .values_list("assigned_to", flat=True))
    touched |= set(_filter_controlled(HUControlAttempt.objects.filter(created_at__gte=today),
                                      field="hu__warehouse_type")
                   .exclude(controller__isnull=True).values_list("controller", flat=True))
    controllers_today = len(touched)
    # select_related("profile"): telefon/urządzenie/last_seen czytane per kontroler niżej —
    # bez tego jedno zapytanie o profil NA KAŻDEGO kontrolera (N+1, PERF-006).
    controllers = (get_user_model().objects
                   .filter(groups__name__in=[GROUP_CONTROLLER, GROUP_LEADER], is_active=True)
                   .select_related("profile").distinct().order_by("username"))
    # Komunikacja: kontrolerzy z sygnałem „kto teraz" (zalogowana sesja) + telefonem z
    # profilu do click-to-call (tel:). Skanery nie mają SIM — dzwoni urządzenie lidera na
    # prywatny numer kontrolera.
    from ui.views.admin import _online_user_ids
    online_ids, _tot = _online_user_ids()
    active_names = {r["controller"] for r in shift_rows if r["active"]}
    controller_rows = [{
        "id": u.id, "username": u.get_username(),
        "full_name": u.get_full_name(),
        "phone": (getattr(getattr(u, "profile", None), "phone", "") or ""),
        "online": u.id in online_ids,
        "active": u.id in online_ids or u.get_username() in active_names,
        # Typ urządzenia + ostatnia aktywność (PresenceMiddleware) — lider widzi,
        # czy kontroler pracuje na Zebrze, telefonie czy komputerze.
        "device": (getattr(getattr(u, "profile", None), "get_last_device_display", lambda: "")()
                   if getattr(getattr(u, "profile", None), "last_device", "") else ""),
        "last_seen": getattr(getattr(u, "profile", None), "last_seen_at", None),
    } for u in controllers]
    # Pilne komunikaty bez potwierdzenia odczytu — kto jeszcze nie potwierdził (per wysyłka).
    from ui.models import Notification
    _unacked = (Notification.objects.filter(requires_ack=True, confirmed_at__isnull=True)
                .select_related("recipient").order_by("-created_at")[:200])
    _groups = {}
    for n in _unacked:
        g = _groups.setdefault(n.ack_group or f"n{n.pk}",
                               {"title": n.title, "created_at": n.created_at, "users": []})
        g["users"].append(n.recipient.get_username())
    unacked_groups = sorted(_groups.values(), key=lambda g: g["created_at"], reverse=True)
    # Fala 4: otwarte wyjątki master daty — decyzja lidera (zamknij / usuń pozycję).
    md_exceptions = list(HandlingUnitItem.objects.filter(md_exception=True)
                         .select_related("hu", "hu__shipment").order_by("hu__code")[:100])
    online_count = sum(1 for r in controller_rows if r["online"])   # zalogowani (sesja), nie tylko pracujący
    # Wyjaśnianie błędów (spec UX §5.1): czekające na potwierdzenie + przeterminowane
    # (po oknie HU_INVESTIGATION_CONFIRM_MIN czas i tak wraca do KPI — lider przegląda).
    from ..models_control import HUErrorInvestigation
    inv_pending = list(HUErrorInvestigation.objects
                       .filter(confirmed_at__isnull=True, rejected=False)
                       .select_related("hu__shipment", "controller", "item")
                       .order_by("started_at")[:50])
    for inv in inv_pending:
        inv.overdue = inv.is_overdue()
    return render(request, "ui/control/leader.html", {
        "inv_pending": inv_pending,
        "live": live, "shift_rows": shift_rows, "rechecks": rechecks,
        "controllers": controllers, "controller_rows": controller_rows,
        "planned_unassigned": planned_unassigned, "online_count": online_count,
        "controllers_today": controllers_today,
        "abuse_rows": abuse_rows,
        "unacked_groups": unacked_groups,
        "max_age_h": int(max_age_h),
        "md_exceptions": md_exceptions,
    })


@_leader
@require_POST
def hu_control_message(request):
    """Lider wysyła wiadomość do kontrolera (in-app, dzwonek skanera). `target` = id
    użytkownika albo 'all_active' (wszyscy zalogowani kontrolerzy/liderzy). Reuse notify()."""
    from django.contrib.auth import get_user_model
    from ui.notifications import notify
    U = get_user_model()
    text = (request.POST.get("text") or "").strip()[:400]
    if not text:
        messages.error(request, "Wpisz treść wiadomości.")
        return redirect("ui:hu_control_leader")
    target = (request.POST.get("target") or "").strip()
    base = U.objects.filter(groups__name__in=[GROUP_CONTROLLER, GROUP_LEADER], is_active=True)
    if target == "all_active":
        from ui.views.admin import _online_user_ids
        online_ids, _tot = _online_user_ids()
        recipients = list(base.filter(id__in=online_ids).distinct())
    else:
        if not target.isdigit():   # nienumeryczny target → 404 świadomie (Postgres DataError)
            raise Http404("Niepoprawny odbiorca.")
        recipients = list(base.filter(pk=target).distinct())
    if not recipients:
        messages.warning(request, "Brak odbiorców — nikt nie jest teraz zalogowany.")
        return redirect("ui:hu_control_leader")
    sender = request.user.get_full_name() or request.user.get_username()
    # Wiadomość tworzy WĄTEK komunikatora — odbiorca może wejść i odpowiedzieć.
    # (Wcześniej: goły notify z url na płaską listę powiadomień — kontroler nie miał
    # jak otworzyć wiadomości ani odpowiedzieć, bo wątek nie istniał.)
    from ui.models import MessageThread, Message
    thread = MessageThread.objects.create(
        subject=f"Wiadomość od lidera: {sender}"[:160], created_by=request.user)
    thread.participants.add(request.user, *recipients)
    Message.objects.create(thread=thread, sender=request.user, body=text[:500])
    turl = reverse("ui:message_thread", args=[thread.pk])
    # Komunikaty lider→kontroler są zawsze pilne → wymagają jawnego potwierdzenia odczytu.
    notify(recipients, f"Wiadomość od {sender}", body=text, level="warning",
           url=turl, requires_ack=True)
    messages.success(request, f"Wysłano wiadomość do {len(recipients)} os.")
    # Odbiorca imienny offline → poinformuj lidera (wiadomość i tak poszła — przeczyta
    # po zalogowaniu). >8 h od ostatniej aktywności = prawdopodobnie poza pracą.
    if target != "all_active":
        from ui.views.admin import _online_user_ids
        from django.utils import timezone
        online_ids, _tot = _online_user_ids()
        for r in recipients:
            if r.id in online_ids:
                continue
            seen = getattr(getattr(r, "profile", None), "last_seen_at", None)
            who = r.get_full_name() or r.get_username()
            if seen is None:
                messages.warning(request, f"{who} nie jest zalogowany(-a) — brak danych o ostatniej aktywności.")
                continue
            mins = int((timezone.now() - seen).total_seconds() // 60)
            ago = f"{mins} min temu" if mins < 120 else f"{mins // 60} godz. temu"
            extra = " Prawdopodobnie nie ma go/jej w pracy." if mins >= 8 * 60 else ""
            messages.warning(request, f"{who} nie jest zalogowany(-a) — ostatnio online {ago}.{extra}")
    return redirect("ui:hu_control_leader")


@_leader
@require_POST
def hu_control_photo_task(request):
    """Lider wysyła zadanie „Zrób zdjęcie produktu" — odbiorca dostaje pilne
    powiadomienie z indeksem; klik otwiera MATinfo z danymi (standardowe /phv/?q=REF),
    gdzie kafel poziomu bez mediów ma przycisk „📷 Zgłoś zdjęcie"."""
    from django.contrib.auth import get_user_model
    from ui.notifications import notify
    from ui import product_codes
    U = get_user_model()
    ref = (request.POST.get("ref_code") or "").strip()[:50]
    target = (request.POST.get("target") or "").strip()
    if not ref or not target.isdigit():
        messages.error(request, "Podaj REF i wybierz odbiorcę.")
        return redirect("ui:hu_control_leader")
    recipient = (U.objects.filter(pk=target, is_active=True,
                                  groups__name__in=[GROUP_CONTROLLER, GROUP_LEADER])
                 .distinct().first())
    if recipient is None:
        raise Http404("Niepoprawny odbiorca.")
    product = product_codes.resolve_product_code(ref)
    if product is None:
        messages.error(request, f"Nie znaleziono indeksu „{ref}”.")
        return redirect("ui:hu_control_leader")
    sender = request.user.get_full_name() or request.user.get_username()
    notify([recipient], f"📷 Zrób zdjęcie produktu: {product.code}",
           body=(f"{sender} prosi o zdjęcie jednostki {product.code} — otwórz MATinfo "
                 f"i użyj „📷 Zgłoś zdjęcie” przy poziomie bez renderu."),
           level="warning", url=f"/phv/?q={product.code}", requires_ack=True)
    messages.success(request, f"Zadanie „Zrób zdjęcie {product.code}” wysłane do "
                     f"{recipient.get_full_name() or recipient.get_username()}.")
    return redirect("ui:hu_control_leader")


@_leader
@require_POST
def hu_control_assign(request, pk):
    """Lider przydziela HU konkretnemu kontrolerowi (albo zdejmuje przydział)."""
    from django.contrib.auth import get_user_model
    hu = get_object_or_404(HandlingUnit, pk=pk)
    uid = (request.POST.get("assignee") or "").strip()
    from ui.notifications import notify
    if uid:
        if not uid.isdigit():   # get_object_or_404 nie łapie Postgres DataError → 404 świadomie
            raise Http404("Niepoprawny użytkownik.")
        user = get_object_or_404(get_user_model(), pk=uid, is_active=True)
        # Lock + re-check jak w assign_group: wyścig z finalize przypinał kontrolera
        # do już domkniętej HU (assigned_to na statusie ok/escaped).
        with transaction.atomic():
            hu = HandlingUnit.objects.select_for_update(of=("self",)).get(pk=pk)
            if hu.status in ("ok", "escaped"):
                messages.error(request, "HU została w międzyczasie domknięta — przydział anulowany.")
                return redirect(_safe_next(request, "ui:hu_control_leader"))
            prev = hu.assigned_to or hu.controlled_by
            hu.assigned_to = user
            # Świeży called_at = rezerwacja (jak assign_group): bez niego _call_queue/_reserve
            # traktowały przydzieloną paletę jako wolną i mógł ją wziąć inny kontroler.
            hu.called_at = timezone.now()
            fields = ["assigned_to", "called_at"]
            # Gdy HU jest w kontroli — reasignacja przenosi też kontrolę (inaczej rozjazd:
            # assigned_to=nowy, ale liczy stary). Spójne z przejęciem.
            if hu.status == "in_control" and hu.controlled_by_id != user.id:
                hu.controlled_by = user
                hu.control_started_at = timezone.now()
                fields += ["controlled_by", "control_started_at"]
            hu.save(update_fields=fields)
        try:
            notify([user], f"Przydzielono Ci HU {hu.ref}",
                   body=f"Lokalizacja: {hu.location or '—'}. Znajdziesz ją przez „Następna HU”.",
                   level="info", url=f"/control/hu/{hu.pk}/")
            if prev and prev.pk != user.pk:         # powiadom poprzedniego, że lider przepiął (D6)
                notify([prev], f"HU {hu.ref} przepięta przez lidera",
                       body=f"Paleta przypisana teraz do {user.get_username()}.",
                       level="info", url=f"/control/hu/{hu.pk}/")
        except Exception:
            log.exception("Powiadomienie o przepięciu HU przez lidera nie wysłane")
        messages.success(request, f"HU {hu.ref} przydzielona: {user.username}.")
    else:
        prev = hu.assigned_to or hu.controlled_by
        hu.assigned_to = None
        hu.called_at = None
        hu.save(update_fields=["assigned_to", "called_at"])
        if prev:
            try:
                notify([prev], f"HU {hu.ref}: przydział zdjęty",
                       body="Lider zdjął przydział tej palety.", level="info",
                       url=f"/control/hu/{hu.pk}/")
            except Exception:
                log.exception("Powiadomienie o zdjęciu przydziału HU nie wysłane")
        messages.success(request, f"HU {hu.ref}: przydział zdjęty.")
    return _safe_next(request, "ui:hu_control_leader")


@_leader
@require_POST
def hu_control_assign_group(request, pk):
    """Lider przepina CAŁĄ grupę odbiorcy (wszystkie 'planned'/'in_control' danego odbiorcy)
    na wskazanego kontrolera. Przenosi assigned_to + controlled_by (gdy in_control),
    powiadamia nowego i wypartych. Rozszerzenie reasignacji per-HU o działanie grupowe."""
    from django.contrib.auth import get_user_model
    hu = get_object_or_404(HandlingUnit.objects.select_related("shipment"), pk=pk)
    uid = (request.POST.get("assignee") or "").strip()
    if not uid.isdigit():
        raise Http404("Niepoprawny użytkownik.")
    from ui.notifications import notify
    target = get_object_or_404(get_user_model(), pk=uid, is_active=True)
    _, _, qs = _recipient_key_filter(hu)
    ids = {pk}
    if qs is not None:
        ids |= {h.pk for h in qs.filter(status__in=("planned", "in_control"))}
    displaced, n = set(), 0
    for hid in ids:
        with transaction.atomic():
            h = HandlingUnit.objects.select_for_update(of=("self",)).get(pk=hid)
            if h.status not in ("planned", "in_control"):
                continue
            prev = h.assigned_to or h.controlled_by
            if prev and prev.pk != target.pk:
                displaced.add(prev)
            fields = ["assigned_to"]
            h.assigned_to = target
            # ZAWSZE świeży znacznik — sweep 8h traktuje called_at jako czas rezerwacji;
            # zachowanie starego kasowałoby przydział lidera przy następnym menu.
            h.called_at = timezone.now()
            fields.append("called_at")
            if h.status == "in_control" and h.controlled_by_id != target.id:
                h.controlled_by = target
                h.control_started_at = timezone.now()
                fields += ["controlled_by", "control_started_at"]
            h.save(update_fields=fields)
            _log_status(h, h.status, h.status, request.user,
                        f"grupowa reasignacja lidera → {target.get_username()}", kind="call", force=True)
            n += 1
    try:
        notify([target], f"Przydzielono Ci grupę odbiorcy ({n} HU)",
               body="Lider przepiął na Ciebie palety jednego odbiorcy.", level="info",
               url=f"/control/hu/{pk}/")
        if displaced:
            notify(list(displaced), "Grupa odbiorcy przepięta przez lidera",
                   body=f"Palety przypisane teraz do {target.get_username()}.", level="info")
    except Exception:
        log.exception("Powiadomienie o przepięciu grupy odbiorcy nie wysłane")
    messages.success(request, f"Przepięto {n} palet(y) odbiorcy na {target.username}.")
    return _safe_next(request, "ui:hu_control_leader")


@_leader
@require_POST
def hu_control_disposition(request, pk):
    """Dyspozycja lidera dla 'vanished' HU: oznacz 'wyjechało bez kontroli' (escaped, F2).
    Zapisuje fakt ominięcia i wyklucza HU z gotowości wysyłki.

    BIZ-011: HU w trakcie liczenia (in_control) wymaga jawnego `confirm_in_progress=1` —
    bez niego odmowa, żeby lider nie „zwolnił" palety, którą kontroler właśnie liczy (też
    gdy weszła w kontrolę już po wyrenderowaniu panelu). Po dyspozycji ta sama logika
    gotowości co finalize: ostatnia rozliczona paleta → alert „wszystkie palety gotowe"."""
    hu = get_object_or_404(HandlingUnit, pk=pk)
    if hu.status not in ("ok", "escaped"):
        _old = hu.status
        interrupted = None                            # kontroler, któremu przerwano liczenie
        note = (request.POST.get("note") or "").strip()[:200]
        with transaction.atomic():
            hu = HandlingUnit.objects.select_for_update().get(pk=pk)
            if hu.status in ("ok", "escaped"):        # TOCTOU: status zmienił się przed lockiem
                return _safe_next(request, "ui:hu_control_hub")
            _old = hu.status                          # świeży status spod locka
            if _old == "in_control" and request.POST.get("confirm_in_progress") != "1":
                messages.error(request, f"HU {hu.ref}: trwa liczenie (kontroler: "
                                        f"{hu.controlled_by or '—'}). Nie zmieniono statusu — "
                                        "potwierdź jawnie przerwanie kontroli.")
                return _safe_next(request, "ui:hu_control_hub")
            hu.status = "escaped"
            hu.is_priority = False
            hu.save(update_fields=["status", "is_priority"])
            note = note or "wyjechało bez kontroli (dyspozycja lidera)"
            if _old == "in_control":
                note = f"{note[:178]} [przerwane liczenie]"
                interrupted = hu.controlled_by
            _log_status(hu, _old, "escaped", request.user, note, force=True)
            # Ta sama gotowość co finalize (sama sprawdza, czy wszystko rozliczone; CAS = raz).
            from ui.notifications import notify_shipment_ready
            transaction.on_commit(lambda: notify_shipment_ready(hu.shipment))
        # Jak przy przejęciu (G1): liczący kontroler dowiaduje się od razu, a nie dopiero
        # przy finalize, który dla 'escaped' jest zablokowany.
        if interrupted and interrupted != request.user:
            try:
                from ui.notifications import notify
                notify([interrupted], f"HU {hu.ref} — liczenie przerwane przez lidera",
                       body=(f"{request.user.get_username()} oznaczył paletę jako „wyjechało "
                             "bez kontroli”. Nie kończ jej liczenia."),
                       level="warning", url=reverse("ui:hu_control_menu"))
            except Exception:
                log.exception("Powiadomienie o przerwaniu liczenia HU nie wysłane")
        messages.success(request, f"HU {hu.ref} oznaczona jako „wyjechało bez kontroli”.")
    return _safe_next(request, "ui:hu_control_hub")


@_controller
@require_POST
def hu_control_takeover(request, pk):
    """Take over control of an HU someone else is currently controlling."""
    hu = get_object_or_404(HandlingUnit, pk=pk)
    if not _zone_ok(request.user, hu):
        messages.error(request, f"Brak uprawnień do kontroli w strefie „{hu.warehouse_type or '—'}”.")
        return redirect("ui:hu_control_menu")
    if hu.status in ("planned", "in_control", "to_recheck"):
        reason = (request.POST.get("reason") or "").strip()[:120]     # G1: powód do audytu
        with transaction.atomic():
            hu = HandlingUnit.objects.select_for_update().get(pk=pk)
            # kto tracił HU (kontrola lub rezerwacja) — czytane POD lockiem, nie przed nim
            displaced = hu.controlled_by or hu.assigned_to
            if hu.status not in ("planned", "in_control", "to_recheck"):
                return redirect("ui:hu_control_detail", pk=pk)   # TOCTOU: domknięta przed lockiem
            _old = hu.status
            hu.controlled_by = request.user
            hu.assigned_to = request.user            # rezerwacja podąża za kontrolą (spójność soft-assign)
            if hu.status == "planned":
                hu.status = "in_control"
            hu.control_started_at = timezone.now()
            hu.save(update_fields=["controlled_by", "assigned_to", "status", "control_started_at"])
            note = f"przejęcie kontroli przez {request.user.get_username()}"
            if reason:
                note += f" — {reason}"
            _log_status(hu, _old, hu.status, request.user, note, force=True)
        # G1: powiadom przejmowanego kontrolera, żeby nie znikała mu praca spod ręki.
        if displaced and displaced != request.user:
            try:
                from ui.notifications import notify
                notify([displaced], f"HU {hu.ref} przejęta przez {request.user.get_username()}",
                       body=(reason or "Kontrolę przejął inny operator."),
                       level="warning", url=reverse("ui:hu_control_detail", args=[hu.pk]))
            except Exception:
                log.exception("Powiadomienie o przejęciu HU nie wysłane")
        messages.success(request, "Przejąłeś kontrolę nad tym HU.")
    return redirect("ui:hu_control_detail", pk=pk)

__all__ = [
    "hu_control_leader",
    "hu_control_message",
    "hu_control_photo_task",
    "hu_control_assign",
    "hu_control_assign_group",
    "hu_control_disposition",
    "hu_control_takeover",
]
