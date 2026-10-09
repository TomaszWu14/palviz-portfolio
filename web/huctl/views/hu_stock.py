# Stock magazynowy (SAP): lista HU, zawartosc, synchronizacja importu, Power BI.

from ui.views.core import (
    GROUP_ADMIN, GROUP_CONTROLLER, GROUP_LEADER, GROUP_MASTER_DATA, HandlingUnit,
    HandlingUnitItem, HUControlAttempt, JsonResponse, Paginator, Q, Shipment, _controller,
    _md_or_control, _md_role, has_role, render, require_POST, settings,
)
from django.db.models import F
from django.db import transaction, DataError as _DataError
from .hu_count import _apply_item_count, _counted_base, _counted_from_units
from .hu_helpers import _HU_SYNC_MAX_BATCH, _qty_error, _recheck_by_original, _strip_get, _type_controlled, _zone_ok
from .hu_hub import _maybe_log_photo_skip, _needs_photo
from ..count_policy import count_gate, is_blind_recount_needed, photo_required_for_flags, whole_units_ok
from .hu_transaction import _ensure_started


@_md_or_control
def planner_stock(request):
    """Dedicated 'Stock magazynowy' tab — the imported warehouse stock (HU containers).

    Stock is a shared dataset reused by several processes (HU control and 3D-map
    modelling), so it lives on its own tab rather than buried in one module. Import
    here; drill into control or the warehouse map from each container."""
    from django.db.models import Count, Q as _Q
    containers = list(
        Shipment.objects.filter(is_stock=True).annotate(
            n=Count("handling_units"),
            n_ok=Count("handling_units", filter=_Q(handling_units__status="ok")),
            n_recheck=Count("handling_units", filter=_Q(handling_units__status="to_recheck")),
            n_planned=Count("handling_units", filter=_Q(handling_units__status="planned")),
        ).order_by("-created_at"))
    # Totals are just the column sums of the already-fetched container annotations — no
    # extra COUNT queries.
    totals = {
        "hu": sum(c.n for c in containers),
        "ok": sum(c.n_ok for c in containers),
        "to_recheck": sum(c.n_recheck for c in containers),
        "planned": sum(c.n_planned for c in containers),
    }
    from ui import powerbi
    return render(request, "ui/stock.html", {
        "containers": containers, "totals": totals,
        "can_control": has_role(request.user, GROUP_ADMIN, GROUP_CONTROLLER, GROUP_LEADER),
        "powerbi_configured": powerbi.is_configured(),
        "powerbi_account": powerbi.connected_account(),
        # Workspace/dataset set but not yet logged in → offer the in-browser "Połącz" flow
        # (no server console needed). Connecting is a Master-Data/Admin action.
        "powerbi_dataset_config": powerbi.has_dataset_config(),
        "can_powerbi_connect": has_role(request.user, GROUP_ADMIN, GROUP_MASTER_DATA),
    })


# Sortowania trybu HU — tylko pola indeksowane (lista miewa setki tysięcy wierszy).
_HU_SORTS = {
    "created":  ["shipment__outbound_created_date", "created_at"],
    "-created": ["-shipment__outbound_created_date", "-created_at"],
    "vip":      ["-shipment__customer__is_vip", "-shipment__outbound_created_date"],
    # Pilne najpierw (is_priority z kolejki wywołań), potem ranga klienta, potem data.
    # (F().desc(nulls_last=True): patrz komentarz w _call_queue — NULL-e na Postgresie.)
    "priority": [F("is_priority").desc(),
                 F("shipment__customer__priority_rank").desc(nulls_last=True),
                 F("shipment__outbound_created_date").desc(nulls_last=True)],
}
# Grupowania trybu HU: pole SQL + parametr filtra rozwijającego grupę.
# week/month agregują TruncWeek/TruncMonth — wiersze bez drill-downu (param None):
# rozwinięcie wymagałoby filtrów zakresu dat, dodamy gdy ktoś o nie poprosi.
_HU_GROUPS = {
    "date":     ("shipment__outbound_created_date", "day"),
    "week":     ("shipment__outbound_created_date", None),
    "month":    ("shipment__outbound_created_date", None),
    "customer": ("shipment__customer__name", "customer"),
    "zone":     ("warehouse_type", "type"),
    "vip":      ("shipment__customer__is_vip", "vip"),
    "recipient": ("shipment__recipient_name", "recipient"),
}


def _hu_queryset(request):
    """Wspólne filtry trybu HU (widok palet): tekst, kontener, strefa, status,
    zakładka kompletacji + filtry rozwinięć grup (day/customer/vip)."""
    q = (request.GET.get("q") or "").strip()
    container = (request.GET.get("container") or "").strip()
    wtype = (request.GET.get("type") or "").strip()
    status = (request.GET.get("status") or "").strip()
    tab = (request.GET.get("tab") or "").strip()
    hus = HandlingUnit.objects.select_related("shipment", "shipment__customer")
    if q:
        hus = hus.filter(Q(code__icontains=q) | Q(location__icontains=q)
                         | Q(recipient_type__icontains=q) | Q(shipment__name__icontains=q)
                         | Q(shipment__wz_number__icontains=q)
                         | Q(shipment__customer__name__icontains=q))
    if container.isdigit():
        hus = hus.filter(shipment_id=int(container))
    if wtype:
        hus = hus.filter(warehouse_type=wtype)
    if status in dict(HandlingUnit.STATUS):
        hus = hus.filter(status=status)
    if tab == "open":
        hus = hus.filter(is_completed=False)
    elif tab == "completed":
        hus = hus.filter(is_completed=True)
    # Rozwinięcia grup (klik w wiersz zgrupowany).
    day = (request.GET.get("day") or "").strip()
    if day:
        from .hu import _parse_date_any
        d = _parse_date_any(day)
        if d:
            hus = hus.filter(shipment__outbound_created_date=d)
    customer = (request.GET.get("customer") or "").strip()
    if customer.isdigit():
        hus = hus.filter(shipment__customer_id=int(customer))
    vip = (request.GET.get("vip") or "").strip()
    if vip in ("0", "1"):
        hus = hus.filter(shipment__customer__is_vip=(vip == "1"))
    return hus


@_md_or_control
def planner_stock_contents(request):
    """Podgląd stocku i widok HU (Fala 2 roadmapy HU).

    Dwa tryby (?view=): "items" (domyślny — jak dotąd: pozycje stocku) oraz "hu" —
    jedna karta na paletę ze WSZYSTKICH dostaw, z sortowaniem (?sort= data utworzenia
    dostawy / VIP), grupowaniem (?group= date/customer/zone/vip — agregacja w SQL,
    wiersz grupy linkuje do rozwinięcia) i zakładkami kompletacji (?tab= open/completed)."""
    q = (request.GET.get("q") or "").strip()
    container = (request.GET.get("container") or "").strip()
    wtype = (request.GET.get("type") or "").strip()
    status = (request.GET.get("status") or "").strip()
    mode = "hu" if request.GET.get("view") == "hu" else "items"
    tab = (request.GET.get("tab") or "").strip()
    sort = (request.GET.get("sort") or "").strip()
    group = (request.GET.get("group") or "").strip()

    # Preserve the active filters across pagination/tab/sort links.
    qs = request.GET.copy()
    qs.pop("page", None)
    def _qs_without(*keys):
        c = qs.copy()
        for k in keys: c.pop(k, None)
        return c.urlencode()
    ctx = {"q": q, "container": container, "wtype": wtype, "status": status,
           "statuses": HandlingUnit.STATUS, "qs": qs.urlencode(), "mode": mode,
           "tab": tab, "sort": sort, "group": group,
           # Warianty do linków przełączających JEDEN wymiar bez gubienia reszty filtrów.
           "qs_no_tab": _qs_without("tab", "view"),
           "qs_no_status": _qs_without("status", "view"),
           "qs_no_sort": _qs_without("sort", "view")}

    if mode == "hu":
        hus = _hu_queryset(request)
        ctx["containers"] = list(Shipment.objects.order_by("-created_at")
                                 .values("id", "name")[:200])
        ctx["types"] = sorted(t for t in (HandlingUnit.objects.order_by()
                              .values_list("warehouse_type", flat=True).distinct()) if t)
        # Liczniki zakładek (na pełnym zbiorze filtrów poza samą zakładką).
        base = _hu_queryset(_strip_get(request, "tab"))
        ctx["n_open"] = base.filter(is_completed=False).count()
        ctx["n_completed"] = base.filter(is_completed=True).count()
        # Liczniki statusów KONTROLI (osobny wymiar od kompletacji-pickingu wyżej):
        # liczone na zbiorze bez filtra ?status=, żeby chipy pokazywały pełny rozkład.
        from django.db.models import Count
        base_ctrl = _hu_queryset(_strip_get(request, "status"))
        counts = dict(base_ctrl.order_by().values_list("status").annotate(n=Count("id")))
        ctx["ctrl_counts"] = [(key, label, counts.get(key, 0))
                              for key, label in HandlingUnit.STATUS]
        if group in _HU_GROUPS:
            from django.db.models import Count, Min
            field, param = _HU_GROUPS[group]
            if group in ("week", "month"):
                # Kubełki tygodniowe/miesięczne (P2 #2) — agregacja w SQL po Trunc*.
                from django.db.models.functions import TruncWeek, TruncMonth
                trunc = TruncWeek if group == "week" else TruncMonth
                hus = hus.annotate(bucket=trunc(field))
                field = "bucket"
            rows = hus.order_by().values(field).annotate(n=Count("id"))
            if group == "recipient":
                # Batch "wywołaj" needs one concrete shipment — recipient names normally
                # map 1:1 to a shipment, but if several shipments share a name the batch
                # button below only targets the first (lowest id) one in the group.
                rows = rows.annotate(shipment_id=Min("shipment_id"))
            # Grupa × sort (P2 #2): sortowanie po dacie porządkuje wiersze grup po ich
            # wartości (chronologicznie); inaczej — jak dotąd — malejąco po liczności.
            if sort in ("created", "-created"):
                rows = rows.order_by(field if sort == "created" else f"-{field}")
            else:
                rows = rows.order_by("-n")
            group_rows = [{"value": r[field], "n": r["n"], "param": param,
                           "shipment_id": r.get("shipment_id")}
                          for r in rows[:200]]
            if group == "recipient":
                # Badge NIEKOMPLETNA + status ETA lidera pickingu w nagłówku grupy (spec).
                sids = [g["shipment_id"] for g in group_rows if g["shipment_id"]]
                ship_info = {s.pk: s for s in Shipment.objects.filter(pk__in=sids)
                             .select_related("picking_eta_by")}
                for g in group_rows:
                    s = ship_info.get(g["shipment_id"])
                    g["incomplete"] = bool(s and not s.picking_complete)
                    g["eta_note"] = (s.picking_eta_note if s else "") or ""
                    g["eta_by"] = (s.picking_eta_by.get_username()
                                   if s and s.picking_eta_by_id else "")
                    g["eta_at"] = s.picking_eta_at if s else None
            ctx["group_rows"] = group_rows
            ctx["group_param"] = param
            return render(request, "ui/stock_contents.html", ctx)
        order = _HU_SORTS.get(sort, _HU_SORTS["-created"])
        page_obj = Paginator(hus.order_by(*order), 100).get_page(request.GET.get("page", 1))
        from ui.hu_metrics import hu_metrics
        metrics = hu_metrics(page_obj.object_list)
        for h in page_obj.object_list:
            h.metric = metrics.get(h.pk, {})
        ctx["page_obj"] = page_obj
        return render(request, "ui/stock_contents.html", ctx)

    items = (HandlingUnitItem.objects
             .filter(hu__shipment__is_stock=True)
             .select_related("hu", "hu__shipment"))
    if q:
        items = items.filter(Q(ref_code__icontains=q) | Q(description__icontains=q)
                             | Q(lot__icontains=q) | Q(hu__code__icontains=q)
                             | Q(hu__location__icontains=q))
    if container.isdigit():
        items = items.filter(hu__shipment_id=int(container))
    if wtype:
        items = items.filter(hu__warehouse_type=wtype)
    if status in dict(HandlingUnit.STATUS):
        items = items.filter(hu__status=status)
    items = items.order_by("hu__location", "hu__code", "ref_code")

    ctx["page_obj"] = Paginator(items, 100).get_page(request.GET.get("page", 1))
    ctx["containers"] = list(Shipment.objects.filter(is_stock=True).order_by("-created_at")
                             .values("id", "name"))
    ctx["types"] = sorted(t for t in (HandlingUnit.objects.filter(shipment__is_stock=True)
                          .order_by().values_list("warehouse_type", flat=True).distinct()) if t)
    return render(request, "ui/stock_contents.html", ctx)


@_controller
@require_POST
def hu_control_sync(request):
    """Offline sync: replay position counts recorded by the scanner while offline.

    Body: JSON {"actions": [{"client_id", "item", "units": {base,opz,kar,pal} | "qty_base"/
    "qty_alt", "flags": [...], "sure": "1"}, ...]}. Każda akcja przechodzi TE SAME bramki co
    liczenie online (BIZ-006); recheck i źródło skanu liczy serwer (status HU, sesja), nie
    klient. Wynik per akcja: ok albo {"error": kod, "message": powód PL} — skaner trzyma
    odrzucone wpisy do dokończenia online."""
    import json
    import logging
    _log = logging.getLogger("ui.hu_control")
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "bad_json"}, status=400)

    actions = payload.get("actions") or []
    if len(actions) > _HU_SYNC_MAX_BATCH:              # cap batcha — wektor DoS/pamięć (P6)
        return JsonResponse({"ok": False, "error": "batch_too_large",
                             "max": _HU_SYNC_MAX_BATCH}, status=400)

    def _f(v):
        try:
            return float(str(v).replace(",", ".")) if v not in (None, "") else None
        except (ValueError, TypeError):
            return None

    # ACTIVE (nie pełne) — offline replay nie może wskrzeszać wygaszonych kodów błędu,
    # których UI online już nie przyjmuje (soft-delete musi obowiązywać oba kanały).
    valid_flags = dict(HandlingUnitItem.ACTIVE_ERROR_FLAGS)
    results = []

    def _reject(cid, code, message):
        # Odrzucenie PER POZYCJA z powodem po polsku — skaner trzyma wpis w kolejce
        # i pokazuje powód, żeby kontroler dokończył go online (BIZ-006).
        results.append({"client_id": cid, "ok": False, "error": code, "message": message})

    for a in actions:
        cid = a.get("client_id")
        try:
            # DataError (Postgres, pk nienumeryczny z JSON offline) NIE jest ValueError/TypeError —
            # bez tego cały batch-sync 500-uje i psuje transakcję. SQLite dawał DoesNotExist.
            item = HandlingUnitItem.objects.select_related("hu").get(pk=a.get("item"))
        except (HandlingUnitItem.DoesNotExist, ValueError, TypeError, _DataError):
            _reject(cid, "item_not_found", "Nie znaleziono pozycji HU.")
            continue
        # Replay już zaksięgowanej akcji (zgubiona odpowiedź) = sukces, nie ponowna bramka.
        prev = HUControlAttempt.objects.filter(client_id=cid).first() if cid else None
        if prev is not None:
            results.append({"client_id": cid, "ok": True, "result": prev.result, "item": item.pk})
            continue

        hu = item.hu
        # BIZ-006 („te same reguły + znacznik"): TE SAME bramki co liczenie online
        # (hu_control_count) — count_gate (strefa/typ/statusy/rekontrola innym/scan-enforce),
        # zdjęcie obecności, blokada niezgodności, całe jednostki, zdjęcie przy uszkodzeniu/
        # ułożeniu i przeliczenie „w ciemno". recheck i źródło skanu liczy SERWER: status HU
        # oraz sesja ze skanu HU online — nie samodeklaracja klienta (obejście HU_SCAN_ENFORCE).
        recheck = hu.status == "to_recheck"
        src = request.session.get(f"hu_scan_src_{hu.pk}", "")
        gate = count_gate(
            zone_label=hu.warehouse_type or "—",
            zone_ok=_zone_ok(request.user, hu),
            type_ok=_type_controlled(hu),
            hu_status=hu.status,
            recheck=recheck,
            recheck_by_original=(recheck and _recheck_by_original(hu, request.user)),
            scan_src=src,
            enforce=getattr(settings, "HU_SCAN_ENFORCE", False))
        if not gate.ok:
            _reject(cid, gate.code, gate.message)
            continue
        if _needs_photo(request, hu):
            _reject(cid, "photo_required", "Zrób zdjęcie palety (dowód obecności) — dokończ online.")
            continue
        _maybe_log_photo_skip(request, hu)
        # Ten sam error-lock co online: zaksięgowanej NIEZGODNOŚCI nie poprawia się replayem
        # offline — rekontrolę robi inny kontroler po zaksięgowaniu HU.
        if item.controlled and item.result == "error" and not recheck:
            _reject(cid, "position_locked", "Ta pozycja to niezgodność — rekontrolę wykona "
                                            "inny kontroler po zaksięgowaniu HU.")
            continue

        # Nowy klient (addytywny) wysyła `units` {base,opz,kar,pal}; stary — qty_base/qty_alt
        # (konwerter lustrzany), liczone legacy przez `_counted_base` (nie sumować).
        if isinstance(a.get("units"), dict):
            units = {k: _f(a["units"].get(k)) for k in ("base", "opz", "kar", "pal")}
            qtys = list(units.values())
            counted_base = _counted_from_units(item, units)
        else:
            qtys = [_f(a.get("qty_base")), _f(a.get("qty_alt"))]
            counted_base = _counted_base(item, *qtys)
        qerr = _qty_error(*qtys)
        if qerr:
            _reject(cid, qerr, "Nieprawidłowa ilość — podaj liczbę nieujemną w rozsądnym zakresie.")
            continue
        if any(q is not None for q in qtys) and not whole_units_ok(counted_base):
            _reject(cid, "qty_not_whole", "Wpisz całą liczbę jednostek — bez ułamków.")
            continue

        flags = {k: True for k in (a.get("flags") or []) if k in valid_flags}
        # Offline nie przenosi zdjęcia → uszkodzenie/złe ułożenie zawsze do dokończenia online.
        need = photo_required_for_flags(flags, has_photo=False)
        if need:
            _reject(cid, "photo_required", f"Zaznaczono „{need}” — wymagane jest zdjęcie. "
                                           "Dokończ online.")
            continue
        if is_blind_recount_needed(counted_base, item.base_qty, sure=str(a.get("sure")) == "1",
                                   has_photo=False, recheck=recheck):
            _reject(cid, "recount_required", "Ilość różni się od ilości na HU — przelicz "
                                             "ponownie online i potwierdź.")
            continue
        # Offline potwierdzenia partii/daty — jeśli klient je poda; brak = potwierdzone
        # (kompatybilność wsteczna ze starym skanerem, który nie ma jeszcze checkboxów).
        b_ok = a.get("batch_ok", True)
        e_ok = a.get("expiry_ok", True)
        try:
            with transaction.atomic():                 # serializuj + idempotencja po client_id
                hu = HandlingUnit.objects.select_for_update().get(pk=hu.pk)
                # TOCTOU: bramki wyżej liczone na wierszu sprzed locka — równoległy finalize
                # mógł domknąć HU / zaksięgować pozycję. Powtórz pod lockiem (jak online).
                if hu.status in ("ok", "escaped"):
                    _reject(cid, "hu_locked", "HU została w międzyczasie zamknięta.")
                    continue
                item.refresh_from_db()
                if item.controlled and item.result == "error" and hu.status != "to_recheck":
                    _reject(cid, "position_locked", "Ta pozycja została już zaksięgowana "
                                                    "jako niezgodność.")
                    continue
                _ensure_started(hu, request.user)      # offline nie ma jak nacisnąć startu
                res = _apply_item_count(request.user, hu, item, counted_base, flags, recheck,
                                        client_id=(cid or ""),
                                        batch_ok=bool(b_ok), expiry_ok=bool(e_ok),
                                        input_source=src,
                                        device_id=request.session.get("hu_device", ""))
            results.append({"client_id": cid, "ok": True, "result": res, "item": item.pk})
        except Exception:                              # never let one bad row 500 the batch
            _log.exception("hu_control_sync apply failed (item=%s)", item.pk)
            _reject(cid, "apply_failed", "Błąd zapisu — spróbuj ponownie.")

    return JsonResponse({"ok": True, "applied": sum(1 for r in results if r["ok"]),
                         "results": results})


@_md_role
@require_POST
def planner_stock_powerbi_connect(request):
    """Start a delegated device-code login from the browser (no server console). Returns
    the short code + URL for the admin to authenticate; a Celery task finishes the login
    and persists the token (w DEBUG/eager: wątek, bo eager zablokowałby request na czas
    wpisywania kodu). The panel polls planner_stock_powerbi_status until done."""
    from django.conf import settings as _settings
    from ui import powerbi
    if not powerbi.has_dataset_config():
        return JsonResponse({"ok": False,
                             "error": "Ustaw najpierw POWERBI_WORKSPACE_ID i POWERBI_DATASET_ID."}, status=400)
    try:
        flow = powerbi.start_device_flow()
    except Exception as exc:
        powerbi.record_error(exc)   # INT-006: szczegół w logu i last_error, nie w odpowiedzi
        return JsonResponse({"ok": False, "error": "Nie udało się rozpocząć logowania do Power BI."}, status=502)

    powerbi.clear_error()
    if getattr(_settings, "CELERY_TASK_ALWAYS_EAGER", False):
        # Eager (DEBUG) wykonałby zadanie synchronicznie i zawiesił request na czas
        # wpisywania kodu — w dev zostaje wątek jak dawniej.
        import threading

        def _runner(fl):
            from django.db import connection
            try:
                powerbi.complete_device_flow(fl)
            except Exception as exc:
                powerbi.record_error(exc)
            finally:
                connection.close()
        threading.Thread(target=_runner, args=(flow,), daemon=True).start()
    else:
        from ui.tasks import complete_powerbi_device_flow
        complete_powerbi_device_flow.delay(flow)
    return JsonResponse({
        "ok": True,
        "user_code": flow.get("user_code", ""),
        "verification_uri": flow.get("verification_uri") or "https://microsoft.com/devicelogin",
        "expires_in": int(flow.get("expires_in", 900)),
    })


@_md_role
def planner_stock_powerbi_status(request):
    """Poll target for the connect flow: is Power BI fully usable yet, and as whom."""
    from ui import powerbi
    err, err_at = powerbi.last_error()
    return JsonResponse({"connected": powerbi.is_configured(),
                         "account": powerbi.connected_account(),
                         "error": err,
                         "error_at": err_at.isoformat() if err_at else ""})

__all__ = [
    "planner_stock",
    "_HU_SORTS",
    "_HU_GROUPS",
    "_hu_queryset",
    "planner_stock_contents",
    "hu_control_sync",
    "planner_stock_powerbi_connect",
    "planner_stock_powerbi_status",
]


