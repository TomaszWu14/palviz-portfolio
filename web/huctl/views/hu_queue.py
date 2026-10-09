# Kolejka wywolan: call/release/eskalacja, rezerwacje grupowe powiazanych HU odbiorcy.

import logging
from ui.views.core import (
    ControlledWarehouseType, ControllerZone, HandlingUnit, Q, Shipment,
    _controller, _md_or_control, _safe_referer, get_object_or_404, messages,
    redirect, render, require_POST,
)
from django.utils import timezone
from django.db import transaction
from django.urls import reverse
from ui.models import Task
from ui.hu_metrics import hu_metrics
from .hu_helpers import _call_queue, _controllable, _filter_controlled, _log_status, _reserve, _zone_ok  # noqa: F401

log = logging.getLogger(__name__)


@_controller
@require_POST
def hu_call(request, pk):
    """Wywołanie/rezerwacja palety: atomowo, pierwszy wygrywa."""
    with transaction.atomic():
        # of=("self",) — blokujemy TYLKO wiersz HandlingUnit. Bez tego PostgreSQL rzuca
        # „FOR UPDATE cannot be applied to the nullable side of an outer join" (LEFT JOIN
        # po nullowalnym shipment); SQLite ignoruje FOR UPDATE, więc bug był niewidoczny.
        hu = HandlingUnit.objects.select_for_update(of=("self",)).select_related("shipment").get(pk=pk)
        if not _zone_ok(request.user, hu):
            messages.warning(request, f"Brak uprawnień do strefy „{hu.warehouse_type or '—'}”.")
            return redirect("ui:hu_control_menu")
        if not _reserve(hu, request.user, "wywołanie (rezerwacja)"):
            messages.warning(request, "Ta paleta jest już wywołana przez innego kontrolera.")
            return redirect("ui:hu_control_menu")
    return redirect("ui:hu_control_detail", pk=pk)


@_controller
@require_POST
def hu_reserve_takeover(request, pk):
    """Miękkie przejęcie REZERWACJI (paleta 'planned' zarezerwowana przez innego kontrolera,
    jeszcze nie liczona): przenosi rezerwację na bieżącego kontrolera, zostaje 'planned',
    powiadamia poprzedniego. Świadomy akt (POST z karty), nie automat."""
    with transaction.atomic():
        hu = HandlingUnit.objects.select_for_update(of=("self",)).select_related("shipment").get(pk=pk)
        if not _zone_ok(request.user, hu):
            messages.warning(request, f"Brak uprawnień do strefy „{hu.warehouse_type or '—'}”.")
            return redirect("ui:hu_control_menu")
        if hu.status != "planned":
            # Już liczona → to jest przejęcie KONTROLI. NIE przekierowuj na endpoint POST-only
            # (302→GET→405) — wróć na kartę, tam jest formularz „Przejmij kontrolę" z powodem.
            if hu.status in ("in_control", "to_recheck"):
                messages.info(request, "Ta paleta jest już w kontroli — użyj „Przejmij kontrolę” na karcie.")
            return redirect("ui:hu_control_detail", pk=pk)
        prev = hu.assigned_to if hu.assigned_to_id and hu.assigned_to_id != request.user.id else None
        hu.assigned_to = request.user
        hu.called_at = timezone.now()
        hu.snooze_until = None
        hu.save(update_fields=["assigned_to", "called_at", "snooze_until"])
        _log_status(hu, hu.status, hu.status, request.user,
                    f"przejęcie rezerwacji przez {request.user.get_username()}", kind="call", force=True)
    if prev:
        try:
            from ui.notifications import notify
            notify([prev], f"Rezerwacja HU {hu.ref} przejęta przez {request.user.get_username()}",
                   body="Zarezerwowana przez Ciebie paleta została przypisana innemu kontrolerowi.",
                   level="info", url=f"/control/hu/{hu.pk}/")
        except Exception:
            log.exception("Powiadomienie o przejęciu rezerwacji HU nie wysłane")
    return redirect("ui:hu_control_detail", pk=pk)


@_md_or_control
@require_POST
def hu_call_batch(request):
    """Wywołaj wszystkie GOTOWE (is_completed) palety danej przesyłki dla bieżącego kontrolera."""
    sid = (request.POST.get("shipment_id") or "").strip()
    if not sid.isdigit():   # nienumeryczny sid → Postgres DataError (500); jak w hu_call_selected
        messages.error(request, "Brak/niepoprawna przesyłka.")
        return redirect("ui:hu_control_menu")
    n = 0
    for hu in _call_queue(request).filter(shipment_id=sid, is_completed=True):
        with transaction.atomic():
            locked = HandlingUnit.objects.select_for_update().get(pk=hu.pk)
            if _reserve(locked, request.user, "wywołanie wsadowe"):
                n += 1
    messages.success(request, f"Wywołano {n} palet.")
    return _safe_referer(request, "ui:planner_stock_contents")


@_md_or_control
@require_POST
def hu_call_selected(request):
    """Checkboxy „zaznacz wiele → wywołaj" (spec): rezerwuje zaznaczone palety dla
    bieżącego użytkownika, każdą pod własnym lockiem (pierwszy wygrywa, zajęte pomijane)."""
    ids = [i for i in request.POST.getlist("hu_ids") if i.isdigit()]
    allowed = set(_call_queue(request).filter(pk__in=ids).values_list("pk", flat=True))
    n = 0
    for pk in allowed:
        with transaction.atomic():
            locked = HandlingUnit.objects.select_for_update().get(pk=pk)
            if _reserve(locked, request.user, "wywołanie (zaznaczone)"):
                n += 1
    messages.success(request, f"Wywołano {n} z {len(ids)} zaznaczonych palet.")
    return _safe_referer(request, "ui:planner_stock_contents")


@_md_or_control
@require_POST
def hu_escalate(request):
    """Eskalacja NIEKOMPLETNEJ przesyłki (spec): Task dla lidera pickingu i obszaru wg
    EscalationRoute (typ magazynu → fallback globalny); kierownik zmiany od razu tylko
    przy escalate_after_minutes=0. Dedup per (przesyłka, adresat) — bez dublowania."""
    from ui.models import EscalationRoute
    from ui.notifications import notify
    sid = request.POST.get("shipment_id")
    shipment = Shipment.objects.filter(pk=sid).first() if sid and sid.isdigit() else None
    if not shipment:
        messages.error(request, "Nie znaleziono przesyłki.")
        return redirect("ui:planner_stock_contents")
    if shipment.picking_complete:
        messages.info(request, "Przesyłka jest już skompletowana — eskalacja zbędna.")
        return _safe_referer(request, "ui:planner_stock_contents")
    wt = (shipment.handling_units.exclude(warehouse_type="")
          .values_list("warehouse_type", flat=True).first()) or ""
    route = EscalationRoute.resolve(wt)
    recipients = []
    if route:
        recipients = [u for u in (route.picking_leader, route.area_leader) if u and u.is_active]
        if route.shift_manager and route.shift_manager.is_active and route.escalate_after_minutes == 0:
            recipients.append(route.shift_manager)
    if not recipients:
        messages.error(request, "Brak skonfigurowanej ścieżki eskalacji (panel admina → Ścieżki eskalacji).")
        return _safe_referer(request, "ui:planner_stock_contents")
    # Lider pickingu/obszaru to role magazynowe — planner_shipment_detail jest za bramą
    # Transportu (403). Linkuj do listy HU przefiltrowanej na tę przesyłkę (_md_or_control).
    url = f'{reverse("ui:planner_stock_contents")}?view=hu&container={shipment.pk}'
    ready = shipment.picking_summary().get("ready", 0)
    n = 0
    for u in recipients:
        dedup = f"picking_incomplete:{shipment.pk}:{u.pk}"[:120]
        if Task.objects.filter(dedup_key=dedup).exclude(status="done").exists():
            continue
        Task.objects.create(
            title=f"Niekompletna przesyłka {shipment.name} — {ready} gotowych, czekamy",
            description=f"Eskalacja z kontroli HU (zgłosił: {request.user.get_username()}). "
                        f"Uzupełnij picking lub podaj ETA na przesyłce.",
            category="manual", priority="high", assignee=u,
            created_by=request.user, dedup_key=dedup)
        notify([u], f"Eskalacja: niekompletna przesyłka {shipment.name}",
               body=f"{ready} palet gotowych, picking trwa. Podaj ETA.",
               level="warning", url=url)
        n += 1
    messages.success(request, f"Eskalowano — utworzono {n} zadań." if n
                     else "Eskalacja już otwarta (zadania istnieją).")
    return _safe_referer(request, "ui:planner_stock_contents")


# @_md_or_control (nie @_any_role): formularz eta-note żyje na stronie planner_stock_contents
# (też @_md_or_control). @_any_role pozwalał rolom read-only (Podgląd) i Obsłudze klienta
# nadpisywać status pickingu na DOWOLNej przesyłce po pk.
@_md_or_control
@require_POST
def shipment_eta_note(request, pk):
    """Status zwrotny lidera pickingu na niekompletnej przesyłce (spec): „paleta 7 w toku /
    ETA 14:30" — widoczny u kontrolera na banerze niekompletności i w nagłówku grupy."""
    shipment = get_object_or_404(Shipment, pk=pk)
    shipment.picking_eta_note = (request.POST.get("note") or "").strip()[:200]
    shipment.picking_eta_by = request.user
    shipment.picking_eta_at = timezone.now()
    shipment.save(update_fields=["picking_eta_note", "picking_eta_by", "picking_eta_at"])
    messages.success(request, "Zapisano status pickingu.")
    return _safe_referer(request, "ui:planner_stock_contents")


@_controller
@require_POST
def hu_urgent_pull(request, pk):
    """Pilne wyjęcie HU (grill 2026-09-05, pyt. 38): auto czeka na załadunek — lider
    wypycha paletę na czoło kolejki (is_priority, snooze zdjęty) i powiadamia
    przypisanego kontrolera. Świadoma akcja lidera, logowana w audycie."""
    from ui.roles import has_role
    from ui.views.core import GROUP_ADMIN, GROUP_LEADER
    if not (request.user.is_superuser or has_role(request.user, GROUP_ADMIN, GROUP_LEADER)):
        messages.error(request, "Pilne wyjęcie może zlecić tylko lider.")
        return redirect("ui:hu_control_detail", pk=pk)
    with transaction.atomic():
        hu = get_object_or_404(HandlingUnit.objects.select_for_update(of=("self",)), pk=pk)
        if hu.status in ("ok", "escaped"):
            messages.info(request, "HU jest już zakończona — pilne wyjęcie zbędne.")
            return redirect("ui:hu_control_detail", pk=pk)
        hu.is_priority = True
        hu.snooze_until = None
        hu.save(update_fields=["is_priority", "snooze_until"])
        _log_status(hu, hu.status, hu.status, request.user,
                    "pilne wyjęcie — na czoło kolejki", kind="call", force=True)
    who = hu.assigned_to or hu.controlled_by
    if who and who != request.user:
        try:
            from ui.notifications import notify
            notify([who], f"PILNE: HU {hu.ref} do natychmiastowej kontroli",
                   body=f"Lider {request.user.get_username()} oznaczył paletę jako pilną "
                        f"(lok. {hu.location or '—'}).",
                   level="warning", url=f"/control/hu/{hu.pk}/", requires_ack=True)
        except Exception:
            log.exception("Powiadomienie PILNE o HU nie wysłane")
    messages.success(request, f"HU {hu.ref} na czole kolejki (pilne).")
    return redirect("ui:hu_control_detail", pk=pk)


@_controller
@require_POST
def hu_release(request, pk):
    """Odmowa (z powodem, wraca do kolejki) lub snooze (odłożenie na X minut).
    Pod lockiem — check-then-write bez niego gubił równoległą rezerwację."""
    from datetime import timedelta
    with transaction.atomic():
        hu = get_object_or_404(HandlingUnit.objects.select_for_update(), pk=pk)
        return _do_release(request, hu, timedelta)


def _do_release(request, hu, timedelta):
    if hu.assigned_to_id and hu.assigned_to_id != request.user.id:
        messages.error(request, "To nie Twoja rezerwacja.")
        return redirect("ui:hu_control_menu")
    snooze_min = (request.POST.get("snooze_min") or "").strip()
    hu.assigned_to = None
    hu.called_at = None
    if request.POST.get("not_found"):
        # Strukturalny pomiar „duchów" (grill 2026-09-05, pyt. 56/73): paleta w systemie,
        # fizycznie brak. Odkładamy 4 h (nie wraca od razu w auto-next temu samemu
        # operatorowi) + alarm do lidera; stała nota = grepowalna w HUStatusEvent.
        hu.snooze_until = timezone.now() + timedelta(hours=4)
        note = f"brak palety na lokalizacji {hu.location or '—'}"
        from .hu_helpers import _notify_groups
        from ui.views.core import GROUP_ADMIN, GROUP_LEADER
        from django.urls import reverse
        _notify_groups([GROUP_ADMIN, GROUP_LEADER],
                       f"Brak palety: HU {hu.ref}",
                       f"{request.user.get_username()} nie znalazł palety na "
                       f"lokalizacji {hu.location or '—'}.",
                       reverse("ui:hu_control_detail", args=[hu.pk]))
    elif snooze_min.isdigit() and int(snooze_min) > 0:
        hu.snooze_until = timezone.now() + timedelta(minutes=int(snooze_min))
        note = f"odłożono na {snooze_min} min"
    else:
        hu.snooze_until = None
        note = f"odmowa: {(request.POST.get('reason') or '').strip()[:180]}"
    hu.save(update_fields=["assigned_to", "called_at", "snooze_until"])
    _log_status(hu, hu.status, hu.status, request.user, note, kind="release")
    return redirect("ui:hu_control_menu")


@_controller
@require_POST
def hu_request_bring(request, pk):
    """„Poproś o przyniesienie" (spec UX §4): HU odbiorcy spoza mojego procesu —
    zamiast przypisania komunikat (wymaga ack) do magazynu/liderów: przynieście
    tę paletę na moją strefę, skontroluję i wyślę razem po konsolidacji."""
    from django.contrib.auth import get_user_model
    from ui.notifications import notify
    from ui.roles import GROUP_LEADER, GROUP_WAREHOUSE
    from .hu_zone import SECTION_TITLES, _active_section
    # Cross-strefowo celowo (spec §4), ale tylko w obrębie typów objętych kontrolą.
    hu = get_object_or_404(_filter_controlled(
        HandlingUnit.objects.select_related("shipment")), pk=pk)
    if hu.status in ("ok", "escaped"):
        messages.info(request, f"HU {hu.ref} jest już domknięta — prośba zbędna.")
        return _safe_referer(request, "ui:hu_control_menu")
    sec = _active_section(request.user)
    dest = SECTION_TITLES.get(sec, sec or "moja strefa")
    targets = list(get_user_model().objects.filter(
        groups__name__in=[GROUP_WAREHOUSE, GROUP_LEADER], is_active=True).distinct())
    if targets:
        notify(targets,
               f"Przynieś HU {hu.ref} na strefę {dest}",
               body=(f"Kontroler {request.user.get_username()} prosi o dostarczenie palety "
                     f"{hu.ref} (stoi: {hu.location or '—'} / {hu.warehouse_type or '—'}) "
                     f"na strefę {dest} — kontrola i wysyłka razem po konsolidacji."),
               level="warning", url=reverse("ui:hu_control_detail", args=[hu.pk]),
               requires_ack=True)
    _log_status(hu, hu.status, hu.status, request.user,
                f"prośba o przyniesienie na strefę {dest}", kind="call", force=True)
    messages.success(request, f"Wysłano prośbę o przyniesienie HU {hu.ref} na strefę {dest}.")
    return _safe_referer(request, "ui:hu_control_menu")


@_controller
def hu_control_find_recipient(request):
    q = request.GET.get("q", "").strip()
    wh = request.GET.get("wh", "").strip()
    hu_code = request.GET.get("hu", "").strip()
    hus, scanned, recipient_label = [], None, ""
    if hu_code:
        # Skan pickHU → rodzeństwo dla TEGO SAMEGO odbiorcy, także w INNYCH strefach.
        # Ograniczamy tylko do typów podlegających kontroli; filtra strefy NIE nakładamy
        # (kontroler ma zobaczyć całą dostawę do odbiorcy). Klucz odbiorcy: KUNNR, potem
        # nazwa odbiorcy, w ostateczności typ odbiorcy z HU.
        base = _filter_controlled(HandlingUnit.objects.select_related("shipment__customer"))
        scanned = base.filter(code__iexact=hu_code).first()
        if scanned:
            sh = scanned.shipment
            kunnr = (getattr(sh, "kunnr", "") or "").strip() if sh else ""
            rname = (getattr(sh, "recipient_name", "") or "").strip() if sh else ""
            recipient_label = rname or kunnr or scanned.recipient_type or "—"
            if kunnr:
                sib = base.filter(shipment__kunnr=kunnr)
            elif rname:
                sib = base.filter(shipment__recipient_name__iexact=rname)
            else:
                sib = base.filter(recipient_type=scanned.recipient_type)
            hus = list(sib.exclude(pk=scanned.pk)
                       .order_by("warehouse_type", "status", "shipment_id", "seq"))
        else:
            messages.warning(request, f"Nie znaleziono HU „{hu_code}”.")
    elif q or wh:
        qs = _controllable(request, HandlingUnit.objects.select_related("shipment__customer"))
        if q:
            # Szukanie także po NUMERZE klienta (KUNNR) — operatorzy częściej znają numer.
            qs = qs.filter(Q(recipient_type__icontains=q)
                           | Q(shipment__recipient_name__icontains=q)
                           | Q(shipment__kunnr__icontains=q))
        if wh:
            qs = qs.filter(warehouse_type=wh)
        hus = list(qs.order_by("status", "shipment_id", "seq"))
    # Oznacz HU spoza strefy użytkownika — poza strefą = tylko podgląd (bez linku do kontroli).
    zones = ControllerZone.zones_for(request.user)
    for h in hus:
        h.out_of_zone = zones is not None and h.warehouse_type not in zones
    # Lista „Typ magazynu" tylko dla stref objętych kontrolą (dynamicznie z aktywnej
    # kontroli — lider edytuje typy na hubie). Fallback: pełna lista, gdy nieskonfigurowane.
    _controlled = ControlledWarehouseType.controlled_codes()
    wh_types = sorted(t for t in HandlingUnit.objects
                      .order_by().values_list("warehouse_type", flat=True).distinct()
                      if t and (_controlled is None or t in _controlled))
    # Wyniki wyszukiwania GRUPOWANE per klient: nagłówek „KUNNR · Nazwa", sort po numerze
    # klienta (nazwa jako fallback) — operator szuka po numerze, ale widzi też nazwę.
    groups = []
    if hus and not scanned:
        by_cust = {}
        for h in hus:
            sh_ = h.shipment
            g_kunnr = (getattr(sh_, "kunnr", "") or "").strip() if sh_ else ""
            g_name = (getattr(sh_, "recipient_name", "") or "").strip() if sh_ else ""
            key = g_kunnr or g_name or h.recipient_type or "—"
            grp = by_cust.setdefault(key, {"kunnr": g_kunnr, "name": g_name
                                           or h.recipient_type or "", "hus": []})
            grp["hus"].append(h)
        groups = sorted(by_cust.values(), key=lambda g: (g["kunnr"] or "￿", g["name"]))
    # Paginacja per klient (grupy trzymamy w całości) — długa lista wyników nie
    # ładuje setek HU naraz na skanerze.
    from django.core.paginator import Paginator
    page_obj = Paginator(groups, 25).get_page(request.GET.get("page")) if groups else None
    scanned_kunnr = ((getattr(scanned.shipment, "kunnr", "") or "").strip()
                     if scanned and scanned.shipment_id else "")
    return render(request, "ui/scanner/find_recipient.html",
                  {"q": q, "wh": wh, "hu_code": hu_code, "scanned": scanned,
                   "recipient_label": recipient_label, "recipient_kunnr": scanned_kunnr,
                   "hus": hus, "groups": page_obj or [], "page_obj": page_obj,
                   "wh_types": wh_types})


def _recipient_key_filter(hu):
    """(label, kunnr, queryset|None) — HU tego samego odbiorcy (klucz KUNNR→nazwa→typ,
    ten sam co hu_control_find_recipient). Queryset NIE wyklucza jeszcze `hu`."""
    sh = hu.shipment
    kunnr = (getattr(sh, "kunnr", "") or "").strip() if sh else ""
    rname = (getattr(sh, "recipient_name", "") or "").strip() if sh else ""
    label = rname or kunnr or hu.recipient_type or "—"
    base = _filter_controlled(HandlingUnit.objects.select_related(
        "shipment", "assigned_to", "controlled_by"))
    if kunnr:
        return label, kunnr, base.filter(shipment__kunnr=kunnr)
    if rname:
        return label, kunnr, base.filter(shipment__recipient_name__iexact=rname)
    if hu.recipient_type:
        return label, kunnr, base.filter(recipient_type=hu.recipient_type)
    return label, kunnr, None


def _recipient_siblings(hu, user):
    """(label, kunnr, [siblings]) — inne HU tego samego odbiorcy z metrykami (pozycje,
    objętość) i flagami (out_of_zone, holder, reservable). Panel „Grupa do odb." + akcje."""
    from django.db.models import Count
    label, kunnr, qs = _recipient_key_filter(hu)
    if qs is None:
        return label, kunnr, []
    sib = list(qs.exclude(pk=hu.pk).annotate(n_items=Count("items"))
               .order_by("warehouse_type", "status", "shipment_id", "seq"))
    if not sib:
        return label, kunnr, []
    metrics = hu_metrics(sib)
    zones = ControllerZone.zones_for(user)
    for h in sib:
        h.out_of_zone = zones is not None and h.warehouse_type not in zones
        h.vol_m3 = (metrics.get(h.pk) or {}).get("volume_m3")
        h.holder = h.assigned_to or h.controlled_by
        h.holder_name = ((h.holder.get_full_name() or h.holder.username) if h.holder else "")
        h.is_done = h.status in ("ok", "escaped")
        # BIZ-009: planned anulowanej wysyłki są poza kolejką — bez akcji rezerwacji w grupie.
        h.cancelled = h.status == "planned" and bool(h.shipment) and h.shipment.status == "cancelled"
        mine = bool(h.holder and h.holder.pk == user.id)
        # Akcja per HU (D3/D9): w-strefie planned wolne → rezerwuj; planned cudze → przejmij
        # rezerwację; moje → „moje"; reszta (inna strefa / domknięte / cudza kontrola) → info.
        if h.out_of_zone or h.is_done or h.cancelled or h.status not in ("planned",):
            h.action = "mine" if mine else None
        elif not h.holder:
            h.action, h.action_url, h.action_label = "act", reverse("ui:hu_call", args=[h.pk]), "Przypisz do mnie"
        elif mine:
            h.action = "mine"
        else:
            h.action, h.action_url, h.action_label = "act", reverse("ui:hu_reserve_takeover", args=[h.pk]), "Przejmij"
    return label, kunnr, sib


@_controller
@require_POST
def hu_reserve_group(request, pk):
    # ponytail: pętla lock→przepnij→log→notify powtarza się w hu_control_assign_group,
    # ale semantyka różni się (tylko planned+strefa vs planned+in_control+controlled_by) —
    # wyciągnąć wspólny _reassign_hus dopiero przy trzecim wariancie.
    """Przypisz do siebie CAŁĄ grupę odbiorcy: wszystkie 'planned' w strefie kontrolera
    (wolne albo zarezerwowane przez innych → miękkie przejęcie), plus tę HU. Powiadamia
    wypartych rezerwujących. Cross-strefa pomijane (D3)."""
    hu = get_object_or_404(HandlingUnit.objects.select_related("shipment"), pk=pk)
    if not _zone_ok(request.user, hu):
        messages.warning(request, f"Brak uprawnień do strefy „{hu.warehouse_type or '—'}”.")
        return redirect("ui:hu_control_detail", pk=pk)
    _, _, qs = _recipient_key_filter(hu)
    ids = [pk]
    if qs is not None:
        zones = ControllerZone.zones_for(request.user)
        from .hu_helpers import _without_cancelled_planned   # BIZ-009: anulowane poza grupą
        ids += [h.pk for h in _without_cancelled_planned(qs.filter(status="planned"))
                if zones is None or h.warehouse_type in zones]
    displaced, n = set(), 0
    for hid in set(ids):
        with transaction.atomic():
            h = HandlingUnit.objects.select_for_update(of=("self",)).get(pk=hid)
            if h.status != "planned" or not _zone_ok(request.user, h):
                continue
            if h.assigned_to_id and h.assigned_to_id != request.user.id:
                displaced.add(h.assigned_to)
            h.assigned_to = request.user
            h.called_at = timezone.now()
            h.snooze_until = None
            h.save(update_fields=["assigned_to", "called_at", "snooze_until"])
            _log_status(h, h.status, h.status, request.user,
                        "przypisanie grupy odbiorcy", kind="call", force=True)
            n += 1
    if displaced:
        try:
            from ui.notifications import notify
            notify(list(displaced), "Rezerwacja przejęta (grupa odbiorcy)",
                   body=f"{request.user.get_username()} przejął rezerwację części palet odbiorcy.",
                   level="info", url=f"/control/hu/{pk}/")
        except Exception:
            log.exception("Powiadomienie o przejęciu rezerwacji grupy odbiorcy nie wysłane")
    messages.success(request, f"Przypisano do Ciebie {n} palet(y) odbiorcy.")
    return redirect("ui:hu_control_detail", pk=pk)

__all__ = [
    "hu_call",
    "hu_request_bring",
    "hu_reserve_takeover",
    "hu_call_batch",
    "hu_call_selected",
    "hu_escalate",
    "shipment_eta_note",
    "hu_release",
    "hu_urgent_pull",
    "hu_control_find_recipient",
    "hu_reserve_group",
    "_do_release",
    "_recipient_key_filter",
    "_recipient_siblings",
]
