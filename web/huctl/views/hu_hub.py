# Ekrany wejsciowe skanera: hub, TV, typy magazynu, menu, skan HU, bramka zdjecia; symulator.

from ui.views.core import (
    ControlledWarehouseType, ControllerZone, Count, GROUP_ADMIN, GROUP_LEADER,
    HandlingUnit, HUControlAttempt, HUQualityIssue, Q, Shipment, WarehouseRackType,
    _controller, _leader, get_object_or_404, has_role, messages, module_required,
    redirect, render, require_POST, settings,
)
from django.utils import timezone
from django.urls import reverse
from .hu_helpers import _call_queue, _controllable, _filter_controlled, _incontrol_max_hours, _live_controls, _log_status, _raise_stale_tasks, _raise_stuck_tasks, _resolve_hu, _stale_hours, _stale_hus_qs, _status_counts, _stuck_incontrol_qs, _valid_photo, _zone_ok  # noqa: F401
from .hu_reports import _kpi_period_bounds, _kpi_stats  # noqa: F401


@module_required("kontrola_hu")
def hu_control_hub(request):
    """Desktop landing for the Control module (separate tab): HU data import + links
    to the scanner functions, reports and the imported stock containers."""
    from django.db.models import Count, Max
    from collections import OrderedDict
    from ui import powerbi
    hus = HandlingUnit.objects.all()
    # The "to control" totals count ONLY the controlled warehouse types (filtered), not
    # every type found in the feed.
    status_counts = _status_counts(_filter_controlled(hus))
    # When the stock data is from — the most recent SAP/PowerBI import stamp.
    stock_as_of = hus.aggregate(m=Max("last_seen_at"))["m"]
    stock = (Shipment.objects.filter(is_stock=True)
             .annotate(n=Count("handling_units")).order_by("-created_at"))

    # Warehouse-type map: every type present on HUs (with per-status counts) merged with
    # the defined rack-type catalog, each flagged with whether we currently control it.
    controlled = ControlledWarehouseType.controlled_codes()        # None = all controlled
    type_rows = OrderedDict()

    def _row(code):
        return type_rows.setdefault(code, {
            "code": code, "label": code or "— (bez typu)",
            "planned": 0, "in_control": 0, "ok": 0, "to_recheck": 0, "total": 0})

    # Agregacja w SQL, nie w Pythonie: po imporcie stocku ta tabela ma setki tysięcy
    # wierszy, a ekran potrzebuje tylko liczników per (typ, status). Wersja iterująca
    # `hus.values(...)` ściągała cały zbiór na każde wejście na stronę.
    # .order_by() czyści domyślne ordering modelu (shipment, seq) — bez tego trafiłoby
    # do GROUP BY i rozbiło agregację na pojedyncze wiersze.
    for row in (hus.order_by().values("warehouse_type", "status")
                .annotate(n=Count("id"))):
        r = _row(row["warehouse_type"] or "")
        r[row["status"]] = r.get(row["status"], 0) + row["n"]
        r["total"] += row["n"]
    for code in WarehouseRackType.objects.values_list("code", flat=True):
        _row(code)                                                 # catalog types with 0 HUs too
    for r in type_rows.values():
        r["controlled"] = (controlled is None) or (r["code"] in controlled)

    wh_type_rows = sorted(type_rows.values(), key=lambda r: r["code"])

    # ── Live leader view: who is controlling what right now + aging backlogs ─────
    # Sekcja „na żywo" liczy tylko kontrolowane typy (tabela typów wyżej zostaje pełna —
    # to ekran konfiguracji zakresu kontroli).
    live_controls = _live_controls(_filter_controlled(hus))
    # Recheck backlog — oldest first (aging), so nothing lingers.
    recheck_backlog = list(_filter_controlled(hus.filter(status="to_recheck"))
                           .select_related("shipment").order_by("created_at")[:50])
    # Open quality issues — oldest first (aging).
    open_quality = list(_filter_controlled(HUQualityIssue.objects.filter(status="open"),
                                           field="hu__warehouse_type")
                        .select_related("hu", "item").order_by("raised_at")[:50])
    stale_hus = list(_stale_hus_qs(_filter_controlled(hus))
                     .select_related("shipment", "controlled_by").order_by("last_seen_at")[:50])
    stale_hours = _stale_hours()
    _raise_stale_tasks(stale_hus)     # F2: podnieś zadanie dla lidera (dedup) o vanished HU
    stuck_hus = list(_stuck_incontrol_qs(_filter_controlled(hus))
                     .select_related("shipment").order_by("control_started_at")[:50])
    _raise_stuck_tasks(stuck_hus)     # alert lidera: kontrola zaczęta i nieukończona >Nh

    # ── Panel kierownika: wyniki kontroli na dashboardzie modułu (nie w skanerze) ──
    is_leader = has_role(request.user, GROUP_ADMIN, GROUP_LEADER)
    kpi_rows, kpi_totals, kpi_period = [], {}, "today"
    if is_leader:
        kpi_period, kpi_start, kpi_end = _kpi_period_bounds(request.GET.get("kpi_period", "today"))
        kpi_rows, kpi_totals = _kpi_stats(kpi_start, kpi_end)
        kpi_rows = kpi_rows[:10]                      # skrót — pełna lista na ekranie KPI

    err, err_at = powerbi.last_error()
    return render(request, "ui/control/hub.html", {
        "status_counts": status_counts, "stock": list(stock),
        "open_issues": HUQualityIssue.objects.filter(status="open").count(),
        "is_leader": is_leader,
        "kpi_rows": kpi_rows, "kpi_totals": kpi_totals, "kpi_period": kpi_period,
        "kpi_target_pph": float(getattr(settings, "KPI_TARGET_POS_PER_H", 0) or 0),
        "powerbi_error": err, "powerbi_error_at": err_at,
        "wh_type_rows": wh_type_rows,
        "control_filtered": controlled is not None,    # True → only a subset is controlled
        "live_controls": live_controls,
        "recheck_backlog": recheck_backlog,
        "open_quality": open_quality,
        "stale_hus": stale_hus,
        "stale_hours": stale_hours,
        "stuck_hus": stuck_hus,
        "incontrol_max_hours": _incontrol_max_hours(),
        "now": timezone.now(),
        "stock_as_of": stock_as_of,
        "powerbi_configured": powerbi.is_configured(),
    })


# Stała paleta kolorów typów magazynu na ścianie TV (przydział wg kolejności kodu).
_TV_TYPE_COLORS = ["#60a5fa", "#4ade80", "#fbbf24", "#f472b6", "#a78bfa",
                   "#34d399", "#fb923c", "#38bdf8", "#f87171", "#c084fc"]


@_leader
def hu_control_tv(request):
    """Read-only wall dashboard (TV on the floor): big status tiles, who is controlling
    now, throughput in the picked range, breakdown per controlled warehouse type and
    the number of active controllers. Auto-refreshes; range via ?range= (Q79)."""
    from datetime import timedelta
    now = timezone.now()
    hus = _filter_controlled(HandlingUnit.objects.all())      # cały ekran: tylko kontrolowane typy
    status_counts = _status_counts(hus)
    live = _live_controls(hus)

    # Zakres czasu dla przepustowości: today / shift / week / month (?range=).
    rng, start, _end = _kpi_period_bounds(request.GET.get("range", "today"))
    atts = _filter_controlled(HUControlAttempt.objects.filter(created_at__gte=start),
                              field="hu__warehouse_type")
    # Przepustowość = unikalne pozycje, jak kpi_stats (huctl.rules, BIZ-007) — nie próby.
    from ..rules import STATUSES, throughput_positions
    throughput = atts.aggregate(errors=Count("id", filter=Q(result="error")))
    throughput["positions"] = throughput_positions(atts)

    # Aktywni kontrolerzy: skan w kontrolowanych typach w ostatnich 15 minutach.
    active_controllers = (_filter_controlled(
        HUControlAttempt.objects.filter(created_at__gte=now - timedelta(minutes=15)),
        field="hu__warehouse_type")
        .exclude(controller__isnull=True).values("controller").distinct().count())

    # Rozbicie per kontrolowany typ magazynu (działy) — każdy z własnym kolorem.
    by_type = {}
    for row in hus.order_by().values("warehouse_type", "status").annotate(n=Count("id")):
        r = by_type.setdefault(row["warehouse_type"] or "—", {
            **{s: 0 for s in STATUSES}, "total": 0})
        r[row["status"]] = r.get(row["status"], 0) + row["n"]
        r["total"] += row["n"]
    type_rows = [{"code": code, "color": _TV_TYPE_COLORS[i % len(_TV_TYPE_COLORS)], **r}
                 for i, (code, r) in enumerate(sorted(by_type.items()))]

    open_quality = _filter_controlled(HUQualityIssue.objects.filter(status="open"),
                                      field="hu__warehouse_type").count()
    stale_count = _stale_hus_qs(hus).count()
    refresh = int(getattr(settings, "HU_TV_REFRESH_SECONDS", 30) or 30)
    return render(request, "ui/control/tv.html", {
        "status_counts": status_counts, "live": live,
        "today_positions": throughput["positions"], "today_errors": throughput["errors"],
        "range": rng, "type_rows": type_rows, "active_controllers": active_controllers,
        "open_quality": open_quality, "stale_count": stale_count,
        "refresh": refresh, "now": now,
    })


@_leader
@require_POST
def hu_control_types(request):
    """Leader/admin saves which warehouse types are under control (global setting).

    All types checked (or none) → clear the config, meaning every type is controlled and
    new imports are auto-included. A strict subset → persist exactly that subset."""
    selected = set(request.POST.getlist("wh_types"))
    all_types = set(request.POST.getlist("all_types"))
    ControlledWarehouseType.objects.all().delete()
    if selected and selected != all_types:
        ControlledWarehouseType.objects.bulk_create(
            [ControlledWarehouseType(code=c) for c in selected])
        messages.success(request, f"Zapisano — kontrolujemy {len(selected)} z {len(all_types)} typów magazynu.")
    else:
        messages.success(request, "Zapisano — kontrolujemy wszystkie typy magazynu.")
    return redirect("ui:hu_control_hub")


@_controller
def hu_control_menu(request):
    # Operator z przypisanymi strefami kontroli musi mieć aktywną strefę — inaczej wybór.
    from .hu_zone import _needs_zone_select
    if _needs_zone_select(request.user):
        return redirect("ui:hu_zone_select")
    # Recheck is a priority — surface a banner ("N do rekontroli, zacznij od najstarszej").
    from django.db.models import Max
    recheck = _controllable(request, HandlingUnit.objects.filter(status="to_recheck"))
    oldest = recheck.order_by("-is_priority", "created_at", "id").first()
    zones = ControllerZone.zones_for(request.user)   # None = wszystkie strefy
    # Role usera (label, kolor) do dropdownu loginu — z jednej listy grup, bez dodatkowego zapytania.
    from ui.roles import ROLE_LABELS
    my_roles = [ROLE_LABELS[n] for n in request.user.groups.values_list("name", flat=True)
                if n in ROLE_LABELS]
    if request.user.is_superuser and not my_roles:
        my_roles = [ROLE_LABELS[GROUP_ADMIN]]
    # `?app=1` (start apki) oznacza okno w przeglądarce (_pwa_head.html) — bez ciasteczka.
    return render(request, "ui/scanner/menu.html", {
        "is_leader": has_role(request.user, GROUP_ADMIN, GROUP_LEADER),
        "recheck_count": recheck.count(),
        "recheck_oldest": oldest,
        "queue_count": _call_queue(request).count(),       # „Następna HU — N w kolejce"
        # Zaległość jakościowa widoczna z menu (grill 2026-09-05, pyt. 91: zgłoszenia
        # „leżały" bez licznika); ten sam filtr stref co lista zgłoszeń.
        "quality_open_count": HUQualityIssue.objects.filter(
            status="open", hu__in=_controllable(request, HandlingUnit.objects.all())).count(),
        "stock_as_of": HandlingUnit.objects.aggregate(m=Max("last_seen_at"))["m"],
        "my_zones": sorted(zones) if zones is not None else None,
        "my_roles": my_roles,
    })


def _scan_token():
    return (getattr(settings, "HU_SCAN_TOKEN", "") or "")


@_controller
@require_POST
def hu_control_scan(request):
    # Warstwa A: rozpoznanie fizycznego skanu. DataWedge dodaje token (prefiks/sufiks); JS
    # skanera wykrywa „burst" (szybkie keydown) i ustawia scan_src. Ręczny wpis = keyboard.
    raw = request.POST.get("code", "")
    tok = _scan_token()
    had_token = bool(tok) and raw.startswith(tok)
    if had_token:
        raw = raw[len(tok):]
    src = "scan" if (had_token or request.POST.get("scan_src") == "scan") else "keyboard"
    device = (request.POST.get("device_id") or request.COOKIES.get("pv_device") or "")[:64]
    # Twarde wymuszenie skanu (anty-„przeklikanie z biurka") — opcjonalne, domyślnie OFF
    # (włącz po skonfigurowaniu DataWedge na urządzeniach): HU_SCAN_ENFORCE=1.
    if getattr(settings, "HU_SCAN_ENFORCE", False) and src != "scan":
        messages.warning(request, "Wpisz kod SKANEREM — ręczne wpisywanie HU jest wyłączone.")
        return redirect("ui:hu_control_menu")
    request.session["hu_device"] = device
    hu = _resolve_hu(raw)
    if not hu:
        messages.error(request, "Nie znaleziono HU o podanym kodzie.")
        return redirect("ui:hu_control_menu")
    # Źródło inputu PER HU (nie globalnie): jeden ręczny wpis nie może „brudzić" całej
    # zmiany, a skan HU1 nie uwiarygadnia liczenia HU2 otwartej przez auto-next.
    request.session[f"hu_scan_src_{hu.pk}"] = src
    codes = ControlledWarehouseType.controlled_codes()
    if codes is not None and (hu.warehouse_type or "") not in codes:
        messages.warning(request, f"Typ magazynu „{hu.warehouse_type or '—'}” nie jest aktualnie kontrolowany "
                                  "(zmień na panelu Kontrola HU).")
        return redirect("ui:hu_control_menu")
    if not _zone_ok(request.user, hu):
        messages.warning(request, f"Nie masz uprawnień do kontroli w strefie „{hu.warehouse_type or '—'}”.")
        return redirect("ui:hu_control_menu")
    if _needs_photo(request, hu):
        return redirect("ui:hu_photo_check", pk=hu.pk)
    return redirect("ui:hu_control_detail", pk=hu.pk)


def _needs_photo(request, hu):
    """Czy przed liczeniem tej HU trzeba zrobić zdjęcie palety (dowód obecności)?
    Tak, gdy HU_PHOTO_ENFORCE i urządzenie kontrolera MA aparat (Zebra bez aparatu →
    jedzie na samym skanie) i nie zrobiono jeszcze zdjęcia tej HU w tej sesji."""
    if not getattr(settings, "HU_PHOTO_ENFORCE", False):
        return False
    prof = getattr(request.user, "profile", None)
    if not (prof and prof.has_camera):
        return False
    return not request.session.get(f"hu_photo_ok_{hu.pk}")


def _maybe_log_photo_skip(request, hu):
    """Tryb detekcji zdjęcia (HU_PHOTO_DETECT): gdy urządzenie MA aparat, a operator liczy
    BEZ zrobionego zdjęcia — zaloguj pominięcie (bez blokowania), żeby lider widział, kogo
    twarde HU_PHOTO_ENFORCE by zatrzymało. Raz na HU/sesję. W trybie enforce nie logujemy —
    tam działa twarda bramka (_needs_photo)."""
    if getattr(settings, "HU_PHOTO_ENFORCE", False) or not getattr(settings, "HU_PHOTO_DETECT", False):
        return
    prof = getattr(request.user, "profile", None)
    if not (prof and prof.has_camera):
        return
    if request.session.get(f"hu_photo_ok_{hu.pk}") or request.session.get(f"hu_photo_logged_{hu.pk}"):
        return
    request.session[f"hu_photo_logged_{hu.pk}"] = True
    _log_status(hu, hu.status, hu.status, request.user,
                "Zdjęcie obecności POMINIĘTE (tryb detekcji)", kind="photo", force=True)


@_controller
def hu_photo_check(request, pk):
    """Bramka fizycznej obecności: zdjęcie palety ze skanera PRZED liczeniem HU.
    GET = ekran „zrób zdjęcie"; POST = walidacja + zapis HUControlPhoto + odblokowanie
    sesji. Tylko urządzenia z aparatem — Zebra tu nie trafia (_needs_photo ją przepuszcza).
    Zdjęcie kompresowane po stronie skanera (kanwa) przed uploadem; serwer waliduje _valid_photo."""
    hu = get_object_or_404(HandlingUnit, pk=pk)
    if not _zone_ok(request.user, hu):
        return redirect("ui:hu_control_menu")
    if not _needs_photo(request, hu):
        return redirect("ui:hu_control_detail", pk=pk)
    if request.method == "POST":
        photo = _valid_photo(request)
        if not photo:
            messages.error(request, "Zrób zdjęcie palety, aby przejść do liczenia.")
            return redirect("ui:hu_photo_check", pk=pk)
        from ui.models import HUControlPhoto
        HUControlPhoto.objects.create(hu=hu, user=request.user, photo=photo)
        request.session[f"hu_photo_ok_{hu.pk}"] = True
        return redirect("ui:hu_control_detail", pk=pk)
    return render(request, "ui/scanner/photo_check.html", {"hu": hu})


@_leader
def scanner_simulator(request):
    """Symulator skanera (narzędzie testowe lidera/admina): moduły PWA — Kontrola HU
    i Hierarchia opakowań (PHV) — w ramce urządzenia o zadanej rozdzielczości i
    przekątnej. Preset: Zebra MC330L (kolejne terminale dodawane ręcznie przez
    „własne wymiary"); kalibracja DPI monitora dla trybu 1:1; iframe same-origin
    (X_FRAME_OPTIONS=SAMEORIGIN)."""
    return render(request, "ui/control/scanner_sim.html", {
        "targets": [
            ("Kontrola HU — menu", reverse("ui:hu_control_menu")),
            ("Status kontroli", reverse("ui:hu_control_status")),
            ("Kontrola HU — rekontrola", reverse("ui:hu_control_recheck_list")),
            ("Hierarchia opakowań (PHV)", reverse("ui:phv_home")),
            ("PHV — Moje zgłoszenia", reverse("ui:phv_my_issues")),
        ],
    })

__all__ = [
    "_TV_TYPE_COLORS",
    "_maybe_log_photo_skip",
    "_needs_photo",
    "_scan_token",
    "hu_control_hub",
    "hu_control_menu",
    "hu_control_scan",
    "hu_control_tv",
    "hu_control_types",
    "hu_photo_check",
    "scanner_simulator",
]
