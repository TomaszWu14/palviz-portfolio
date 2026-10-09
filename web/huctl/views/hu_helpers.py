# Wspólne helpery Kontroli HU: rezerwacje/kolejka wywolan, strefy, audyt statusow, resolve kodu.
import logging
import math as _math

from ui.views.core import (
    ControlledWarehouseType, ControllerZone, Count, GROUP_ADMIN, GROUP_LEADER,
    GROUP_MASTER_DATA, HandlingUnit, HandlingUnitItem, HUControlAttempt,
    HUStatusEvent, Q, messages, settings,
)
from django.utils import timezone
from django.urls import reverse
from ui.models import Task
from .hu_zone import _active_section, _section_filter, _zone_ok  # noqa: F401



# HU quality photos are uploaded straight from the scanner camera. Validate size +
# type before saving (they land in /media/ and were previously unchecked — a huge or
# non-image file could fill the disk / be served as active content).
_PHOTO_MAX_BYTES = 12 * 1024 * 1024
_PHOTO_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _valid_photo(request, field="photo"):
    """Return the uploaded photo iff it's a reasonable image, else None (a bad file is
    dropped with a warning rather than blocking the quality report itself)."""
    f = request.FILES.get(field)
    if not f:
        return None
    if f.size > _PHOTO_MAX_BYTES or f.content_type not in _PHOTO_CONTENT_TYPES:
        messages.warning(request, "Pominięto zdjęcie: dozwolone JPG/PNG/WEBP do 12 MB.")
        return None
    return f


def _filter_controlled(qs, field="warehouse_type"):
    """Restrict a queryset to the warehouse types currently under control.

    Leaders pick the controlled types on the control hub; an empty config means every
    type is controlled, so the queryset passes through unchanged. `field` pozwala
    filtrować modele powiązane (np. HUControlAttempt: field="hu__warehouse_type") —
    raporty i liczniki liczą TYLKO kontrolowane typy, nie cały magazyn."""
    codes = ControlledWarehouseType.controlled_codes()
    if codes is None:
        return qs
    # Wyjątek: HU klienta VIP zawsze podlega kontroli, niezależnie od typu magazynu.
    from django.db.models import Q
    vip_path = field.replace("warehouse_type", "shipment__customer__is_vip")
    return qs.filter(Q(**{f"{field}__in": codes}) | Q(**{vip_path: True}))


def _controllable(request, qs):
    """Controlled-type filter + per-zone matrix + active control-section filter
    (sekcje: hu_zone). Admins/leaders bypass the per-user layers."""
    qs = _filter_controlled(qs)
    zones = ControllerZone.zones_for(request.user)
    if zones is not None:
        qs = qs.filter(warehouse_type__in=zones)
    return _section_filter(qs, request.user)


def _reserved_max_hours():
    return float(getattr(settings, "HU_RESERVED_MAX_HOURS", 8) or 8)


def _expire_stale_reservations():
    """Auto-wygasanie rezerwacji: paleta 'planned' zarezerwowana (assigned_to+called_at),
    której kontroler nie zaczął liczyć w ciągu HU_RESERVED_MAX_HOURS (domyślnie zmiana = 8 h),
    wraca do puli (assigned_to/called_at=None). Miękka rezerwacja i tak jest przejmowalna;
    to tylko sprząta porzucone rezerwacje, żeby auto-next znów je podał. Bulk UPDATE,
    throttlowany do 1×/min (cache) — bez tego pisał do DB przy KAŻDYM otwarciu menu/next."""
    from datetime import timedelta
    from django.core.cache import cache
    if not cache.add("hu_res_sweep", 1, 60):     # ktoś sprzątał w ostatniej minucie
        return
    cut = timezone.now() - timedelta(hours=_reserved_max_hours())
    stale = list(HandlingUnit.objects
                 .filter(status="planned", assigned_to__isnull=False, called_at__lt=cut)
                 .select_related("assigned_to"))
    if stale:
        (HandlingUnit.objects.filter(pk__in=[h.pk for h in stale])
         .update(assigned_to=None, called_at=None))
        # Rezerwacja znikała PO CICHU z listy operatora (grill 2026-09-05, pyt. 23) —
        # powiadom, żeby nie szukał palety, której już nie ma.
        from .hu_sweep import _notify_release
        for hu in stale:
            _notify_release(hu, f"Rezerwacja palety {hu.ref} wygasła",
                            f"Brak startu kontroli > {_reserved_max_hours():g} h — "
                            "paleta wróciła do wspólnej kolejki.")
    _release_abandoned_incontrol()


from .hu_sweep import _release_abandoned_incontrol  # noqa: F401  (W3: <500 linii)

log = logging.getLogger(__name__)


def _without_cancelled_planned(qs):
    """BIZ-009: nieliczone (planned) HU anulowanej wysyłki poza kolejkami; wiersz HU bez zmian."""
    return qs.exclude(status="planned", shipment__status="cancelled")


def _call_queue(request):
    """JEDEN filtr kolejki wywołań (BIZ-005): strefa/typ, wolne albo moje, bez aktywnego
    snooze. Kolejność nadaje `huctl.queue_rank.order_queue` (przez hu_dashboard._queue) —
    ta sama dla „Następna HU" i „Weź następną"; tu tylko filtr (liczniki, wywołania)."""
    from django.db.models import Exists, OuterRef, Q
    from django.utils import timezone
    _expire_stale_reservations()          # zwolnij porzucone rezerwacje przed budową kolejki
    now = timezone.now()
    qs = _controllable(request, HandlingUnit.objects.select_related(
        "shipment", "shipment__customer")).filter(
        status__in=("planned", "to_recheck"))
    qs = _without_cancelled_planned(qs)
    # wolne: bez rezerwacji albo moje; snooze wygasł
    qs = qs.filter(Q(called_at__isnull=True) | Q(assigned_to=request.user))
    qs = qs.filter(Q(snooze_until__isnull=True) | Q(snooze_until__lte=now))
    # Krótki termin — ta sama reguła co księgowanie i karta HU (huctl.rules, BIZ-007):
    # poniżej min. ważności klienta, a bez wymogu klienta poniżej 6 mies.
    from ..rules import short_dated_q
    short_qs = HandlingUnitItem.objects.filter(
        short_dated_q(timezone.localdate()), hu=OuterRef("pk"))
    qs = qs.annotate(a_shortdated=Exists(short_qs))
    # Bez własnego rankingu SQL (priorytet/status/objętość) — dawał inną kolejność niż
    # „Weź następną" (BIZ-005). Stabilny porządek tylko dla wywołań wsadowych.
    return qs.order_by("id")


def _type_controlled(hu):
    """True if this HU's warehouse type is under control (global leader scope). Egzekwowane
    nie tylko przy scan, ale też przy start/finalize — deep-link nie obchodzi zakresu."""
    codes = ControlledWarehouseType.controlled_codes()
    if codes is None or (hu.warehouse_type or "") in codes:
        return True
    cust = hu.shipment.customer if hu.shipment_id else None
    return bool(cust and cust.is_vip)          # wyjątek: klient VIP zawsze pod kontrolą


def _first_controller(hu):
    """Prawdziwy PIERWSZY kontroler HU (z audytu HUControlAttempt, nie-recheck) — odporny
    na takeover/reopen, które mutują controlled_by. Podstawa guardu 'rekontrolę robi ktoś inny'."""
    a = (HUControlAttempt.objects.filter(hu=hu, is_recheck=False)
         .exclude(controller__isnull=True).order_by("created_at").first())
    return a.controller_id if a else None


def _recheck_by_original(hu, user):
    """True gdy `user` był pierwszym kontrolerem tej HU (a więc nie powinien robić
    rekontroli). Zasada „druga para oczu" obowiązuje KAŻDĄ rolę — lider/admin/superuser
    może rekontrolować cudze HU, ale nigdy własnej pierwotnej kontroli
    (decyzja usera 2026-08-26)."""
    fc = _first_controller(hu)
    return fc is not None and fc == user.id


_HU_MAX_QTY = float(getattr(settings, "HU_MAX_QTY", 1_000_000) or 1_000_000)
_HU_SYNC_MAX_BATCH = int(getattr(settings, "HU_SYNC_MAX_BATCH", 500) or 500)


def _qty_error(*qtys):
    """Zwróć kod błędu (str) gdy któraś ilość jest ujemna / nie-skończona / absurdalnie duża,
    inaczej None. Wspólne dla ścieżki online i offline-sync."""
    for v in qtys:
        if v is None:
            continue
        if not _math.isfinite(v):
            return "qty_not_finite"
        if v < 0:
            return "qty_negative"
        if v > _HU_MAX_QTY:
            return "qty_too_large"
    return None


def _raise_stale_tasks(stale_hus):
    """Podnieś zadanie do lidera dla każdej HU 'vanished' (wyjechała przed kontrolą), dedup
    raz na HU. Lider decyduje: dyspozycja → status 'escaped' (F2)."""
    period = timezone.localdate().strftime("%Y-%m")
    for hu in stale_hus:
        dedup = f"hu_stale:{hu.pk}:{period}"[:120]
        Task.objects.get_or_create(
            dedup_key=dedup,
            defaults=dict(
                title=f"HU {hu.ref} zniknęła z feedu przed kontrolą",
                description=("Handling unit przestała pojawiać się w imporcie SAP, a nie została "
                             "skontrolowana. Zdecyduj: dokończ kontrolę albo oznacz „wyjechało bez "
                             "kontroli”."),
                category="hu_stale", related_hu=hu, url=reverse("ui:hu_control_hub")))


def _attach_recheck_age(hus):
    """Dokleja recheck_since / recheck_hours / recheck_overdue do listy HU w statusie
    to_recheck i sortuje najstarsze pierwsze — wspólne dla panelu lidera i listy
    rekontroli na skanerze."""
    from datetime import timedelta
    from django.db.models import Max
    now = timezone.now()
    max_age_h = float(getattr(settings, "HU_RECHECK_MAX_AGE_HOURS", 24) or 24)
    # .order_by() — bez tego Meta.ordering wchodzi do GROUP BY i psuje Max(created_at).
    since = dict(HUStatusEvent.objects.filter(hu__in=hus, to_status="to_recheck")
                 .order_by().values_list("hu_id").annotate(m=Max("created_at")))
    for hu in hus:
        started = since.get(hu.id) or hu.created_at
        hu.recheck_since = started
        hu.recheck_hours = int((now - started).total_seconds() // 3600) if started else None
        hu.recheck_overdue = bool(started and (now - started) > timedelta(hours=max_age_h))
    hus.sort(key=lambda h: h.recheck_since or now)
    return hus


def _notify_groups(group_names, title, body, url, level="warning"):
    """Powiadom aktywnych użytkowników wskazanych grup; celowo nigdy nie wywraca
    ścieżki wywołującej (powiadomienie to dodatek, nie transakcja)."""
    try:
        from django.contrib.auth import get_user_model
        from ui.notifications import notify
        users = (get_user_model().objects
                 .filter(groups__name__in=group_names, is_active=True).distinct())
        if users:
            notify(users, title, body=body, level=level, url=url)
    except Exception:
        log.exception("Powiadomienie grup nie wysłane")


def _raise_corrective_tasks(hu, user):
    """F9: zadanie naprawcze dla magazynu per pozycja z błędem (dedup per hu+item+rodzaj) —
    brak → uzupełnij, nadmiar → zdejmij, uszkodzenie → wymień. Rekontrola po realnej korekcie."""
    url = reverse("ui:hu_control_detail", args=[hu.pk])
    raised = []
    for it in hu.items.filter(result="error").select_related("product"):
        flags = it.error_flags or {}
        counted = it.counted_qty if it.counted_qty is not None else 0
        exp, unit = it.base_qty, (it.base_unit or "OP")
        if flags.get("damaged"):
            kind, action = "damaged", f"Wymień uszkodzony towar {it.ref_code} (HU {hu.ref})"
        elif counted < exp:
            kind, action = "short", f"Uzupełnij brak {exp - counted:g} {unit}: {it.ref_code} (HU {hu.ref})"
        elif counted > exp:
            kind, action = "over", f"Zdejmij nadmiar {counted - exp:g} {unit}: {it.ref_code} (HU {hu.ref})"
        else:
            continue                       # błąd bez ilościowej/uszkodzeniowej przyczyny → bez taska
        dedup = f"hu_fix:{hu.pk}:{it.pk}:{kind}"[:120]
        if not Task.objects.filter(dedup_key=dedup).exclude(status="done").exists():
            Task.objects.create(
                title=action[:200], category="hu_fix", priority="high",
                description=f"Kontrola HU {hu.ref} (lok. {hu.location or '—'}): niezgodność "
                            f"pozycji {it.ref_code}.",
                related_hu=hu, related_product=it.product, dedup_key=dedup,
                created_by=user if getattr(user, "is_authenticated", False) else None,
                url=url[:300])
            raised.append(action)
    # Roadmapa (wywiad Q9): planiści maja dostać sygnał od razu — jedno powiadomienie
    # zbiorcze per HU zamiast ciszy (zadania i tak są dedupowane wyżej).
    if raised:
        _notify_groups([GROUP_ADMIN, GROUP_MASTER_DATA],
                       f"Zadania naprawcze: HU {hu.ref} ({len(raised)})",
                       "; ".join(raised)[:300], url)


def _maybe_escalate_recheck(hu, user):
    """F10: po N nieudanych cyklach rekontroli podnieś eskalację do lidera (dedup per hu)."""
    n = int(getattr(settings, "HU_RECHECK_ESCALATE_AFTER", 2) or 2)
    cycles = HUStatusEvent.objects.filter(hu=hu, to_status="to_recheck").count()
    if cycles < n:
        return
    dedup = f"hu_recheck_escalate:{hu.pk}"[:120]
    if Task.objects.filter(dedup_key=dedup).exclude(status="done").exists():
        return
    Task.objects.create(
        title=f"Eskalacja: HU {hu.ref} nie przechodzi kontroli ({cycles}× rekontrola)"[:200],
        description=f"HU {hu.ref} wracała do rekontroli {cycles} raz(y) — korekty nie rozwiązują "
                    "niezgodności. Wymaga uwagi lidera.",
        category="manual", priority="high", related_hu=hu, dedup_key=dedup,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        url=reverse("ui:hu_control_detail", args=[hu.pk])[:300])
    _notify_groups([GROUP_ADMIN, GROUP_LEADER],
                   f"Eskalacja rekontroli: HU {hu.ref} ({cycles}×)",
                   "Paleta przewlekle nie przechodzi kontroli — sprawdź.",
                   reverse("ui:hu_control_detail", args=[hu.pk]))


def _log_status(hu, from_status, to_status, user, note="", force=False, kind="status"):
    """Record an HU status transition for the audit trail (who / when / why). `force`
    logs even when the status is unchanged (e.g. a take-over changes only controlled_by).
    `kind`: status / call / release — metryki wywołań liczą się z tego pola (spec)."""
    if from_status == to_status and not force and kind == "status":
        return
    HUStatusEvent.objects.create(hu=hu, kind=kind, from_status=from_status or "",
                                 to_status=to_status,
                                 by_user=user if getattr(user, "is_authenticated", False) else None,
                                 note=note[:200])


def _is_gls(hu):
    """Czy paleta jest w strefie GLS (kody z GLS_ZONE_CODES, case-insensitive)."""
    codes = {c.upper() for c in getattr(settings, "GLS_ZONE_CODES", [])}
    return (hu.warehouse_type or "").upper() in codes


def _reserve(locked_hu, user, note):
    """Rezerwacja wywołanej palety pod już założonym select_for_update — wspólny rytuał
    hu_call / hu_call_batch / hu_control_next / hu_call_selected. Zwraca False, gdy trzyma
    ją ktoś inny (pierwszy wygrywa)."""
    if locked_hu.called_at and locked_hu.assigned_to_id not in (None, user.id):
        return False
    locked_hu.assigned_to = user
    locked_hu.called_at = timezone.now()
    locked_hu.snooze_until = None
    locked_hu.save(update_fields=["assigned_to", "called_at", "snooze_until"])
    _log_status(locked_hu, locked_hu.status, locked_hu.status, user, note, kind="call")
    return True


def _live_controls(qs, cap=100):
    """In-control HUs with progress annotated in ONE query (no per-HU progress() N+1).
    Shared by the leader hub and the TV wall dashboard."""
    rows = []
    for hu in (qs.filter(status="in_control").select_related("controlled_by", "shipment")
               .annotate(_n_total=Count("items"),
                         _n_done=Count("items", filter=Q(items__controlled=True)))
               .order_by("control_started_at")[:cap]):
        rows.append({"hu": hu, "controller": hu.controlled_by,
                     "done": hu._n_done, "total": hu._n_total,
                     "started": hu.control_started_at, "location": hu.location})
    return rows


def _stale_hours():
    return float(getattr(settings, "HU_STALE_HOURS", 24) or 24)


def _stale_hus_qs(qs):
    """Uncontrolled HUs that stopped reappearing in the SAP feed — likely booked out
    before control, so nothing silently escapes (Q95/96)."""
    from datetime import timedelta
    cut = timezone.now() - timedelta(hours=_stale_hours())
    return qs.filter(status__in=("planned", "in_control"),
                     last_seen_at__isnull=False, last_seen_at__lt=cut)


def _incontrol_max_hours():
    return float(getattr(settings, "HU_INCONTROL_MAX_HOURS", 2) or 2)


def _stuck_incontrol_qs(qs):
    """HU rozpoczęte w kontroli (in_control), które utknęły — kontrola trwa dłużej niż
    HU_INCONTROL_MAX_HOURS. Sygnał dla lidera, że ktoś zaczął i porzucił paletę."""
    from datetime import timedelta
    cut = timezone.now() - timedelta(hours=_incontrol_max_hours())
    return qs.filter(status="in_control",
                     control_started_at__isnull=False, control_started_at__lt=cut)


def _raise_stuck_tasks(stuck_hus):
    """Podnieś zadanie do lidera dla każdej HU utkniętej w kontroli, dedup raz na HU na
    miesiąc. Alert: kontrola zaczęta i nieukończona ponad próg godzinowy."""
    period = timezone.localdate().strftime("%Y-%m")
    hrs = _incontrol_max_hours()
    for hu in stuck_hus:
        dedup = f"hu_stuck:{hu.pk}:{period}"[:120]
        _, created = Task.objects.get_or_create(
            dedup_key=dedup,
            defaults=dict(
                title=f"HU {hu.ref} utknęła w kontroli (>{hrs:g}h)"[:200],
                description=(f"Kontrola HU {hu.ref} została rozpoczęta, ale nie zakończona od "
                             f"ponad {hrs:g}h. Sprawdź, czy kontroler nie porzucił palety."),
                category="hu_stuck", priority="high", related_hu=hu,
                url=reverse("ui:hu_control_detail", args=[hu.pk])[:300]))
        if created:
            _notify_groups([GROUP_ADMIN, GROUP_LEADER],
                           f"HU {hu.ref} utknęła w kontroli (>{hrs:g}h)",
                           "Kontrola zaczęta i nieukończona — sprawdź.",
                           reverse("ui:hu_control_detail", args=[hu.pk]))


def _status_counts(qs):
    """Per-status HU counts for the control dashboards in ONE query (vs 4 COUNTs).
    Statusy i grupy z huctl.rules.STATUS_GROUPS (BIZ-007): `escaped` liczony zawsze,
    jako osobna grupa — `open` (praca otwarta) nigdy go nie obejmuje."""
    from ..rules import STATUS_GROUPS, STATUSES
    return qs.aggregate(
        **{s: Count("id", filter=Q(status=s)) for s in STATUSES},
        open=Count("id", filter=Q(status__in=STATUS_GROUPS["open"])))


def _resolve_hu(code):
    """Find an HU by its (unique) pickHU code or the PalViz '<shipment>-P<seq>' ref.

    A pickHU code identifies exactly one handling unit — enforced in the DB by the
    hu_code_uniq constraint — so a direct lookup is enough: exact match first, then a
    case-insensitive fallback for scanner/label casing differences, then the PalViz
    fallback ref for HUs without a pickHU code. The iexact fallback is deliberately
    ambiguity-safe: the constraint is case-sensitive, so 'ABC'/'abc' could in theory
    coexist — rather than silently scanning a random one, we refuse the match."""
    code = (code or "").strip()
    if not code:
        return None
    hu = HandlingUnit.objects.filter(code=code).select_related("shipment").first()
    if hu:
        return hu
    ci = list(HandlingUnit.objects.filter(code__iexact=code).select_related("shipment")[:2])
    if len(ci) == 1:
        return ci[0]
    if len(ci) > 1:
        return None
    if "-P" in code:
        sid, _, seq = code.rpartition("-P")
        if sid.isdigit() and seq.isdigit():
            return (HandlingUnit.objects.filter(shipment_id=int(sid), seq=int(seq))
                    .select_related("shipment").first())
    return None


def _strip_get(request, *keys):
    """Kopia requestu z usuniętymi parametrami GET (do liczników zakładek)."""
    from copy import copy
    r = copy(request)
    r.GET = request.GET.copy()
    for k in keys:
        r.GET.pop(k, None)
    return r


def _ensure_started(hu, user):
    """Podnieś planned→in_control, gdy kontroler zaczyna liczyć bez jawnego startu.

    Jawny przycisk „Rozpocznij kontrolę” (P5) chroni przed startem jako EFEKTEM UBOCZNYM
    GET-a. Potwierdzenie pozycji to już jawny akt kontrolera — odrzucenie go zgubiłoby
    pracę (offline nie ma jak nacisnąć startu), a przepuszczenie bez tego wywołania
    zostawiałoby HU bez `controlled_by`/`control_started_at`, czyli niewidoczną w panelu
    lidera i bez podstawy do reguły „rekontrolę robi inny kontroler”.

    Wołane wewnątrz transakcji z założonym select_for_update na HU."""
    if hu.status != "planned":
        return
    hu.status = "in_control"
    hu.controlled_by = user
    hu.control_started_at = timezone.now()
    hu.save(update_fields=["status", "controlled_by", "control_started_at"])
    _log_status(hu, "planned", "in_control", user, "start kontroli (pierwsze liczenie)")

__all__ = [
    "_HU_MAX_QTY",
    "_HU_SYNC_MAX_BATCH",
    "_PHOTO_CONTENT_TYPES",
    "_PHOTO_MAX_BYTES",
    "_active_section",
    "_attach_recheck_age",
    "_call_queue",
    "_controllable",
    "_section_filter",
    "_ensure_started",
    "_expire_stale_reservations",
    "_filter_controlled",
    "_first_controller",
    "_incontrol_max_hours",
    "_is_gls",
    "_live_controls",
    "_log_status",
    "_maybe_escalate_recheck",
    "_notify_groups",
    "_qty_error",
    "_raise_corrective_tasks",
    "_raise_stale_tasks",
    "_raise_stuck_tasks",
    "_recheck_by_original",
    "_release_abandoned_incontrol",
    "_reserve",
    "_reserved_max_hours",
    "_resolve_hu",
    "_stale_hours",
    "_stale_hus_qs",
    "_status_counts",
    "_strip_get",
    "_stuck_incontrol_qs",
    "_type_controlled",
    "_valid_photo",
    "_zone_ok",
]

