# Raporty i KPI lidera: statusy, historia, bledy, rozliczenie GLS, KPI zmian.

from ui.views.core import (
    ControlledWarehouseType, HandlingUnit, HandlingUnitItem, HttpResponse,
    Q, _controller, _leader, get_object_or_404, messages,
    redirect, render, settings,
)
from django.db.models import F
from django.utils import timezone
from ui.hu_metrics import hu_metrics
from huctl.kpi import kpi_stats as _kpi_stats  # noqa: F401  (alias dla hu_control/hu_hub/hu_leader)
from .hu_helpers import _attach_recheck_age, _controllable, _filter_controlled, _zone_ok  # noqa: F401
from ..rules import STATUSES, progress, status_label


@_leader
def gls_packing_report(request):
    """Raport konsolidacji GLS dla lidera: per kontroler — palety, Σ kartonów, Σ paczek,
    współczynnik konsolidacji (kartony/paczkę, wyżej = lepiej pakuje) i śr. objętość
    na paczkę. Ranking malejąco po współczynniku; filtr dat (?od/do, domyślnie 30 dni)."""
    from datetime import timedelta
    from django.db.models import Count as _Count, Sum as _Sum
    from ui.models import GlsPackingEntry
    from .hu import _parse_date_any

    d_to = _parse_date_any(request.GET.get("do") or "") or timezone.localdate()
    d_from = _parse_date_any(request.GET.get("od") or "") or (d_to - timedelta(days=30))
    qs = GlsPackingEntry.objects.filter(created_at__date__gte=d_from,
                                        created_at__date__lte=d_to)
    rows = []
    # .order_by() — bez tego GlsPackingEntry.Meta.ordering=["-created_at"] wchodzi do GROUP BY
    # (grupa per wpis, nie per kontroler) → n=1, kontroler w wielu wierszach, cały raport błędny.
    for r in (qs.order_by().values(name=F("controller__username"))
              .annotate(n=_Count("id"), cartons=_Sum("cartons"), parcels=_Sum("parcels"),
                        volume=_Sum("volume_m3"))):
        parcels = r["parcels"] or 0
        rows.append({
            "name": r["name"] or "—", "n": r["n"],
            "cartons": r["cartons"] or 0, "parcels": parcels,
            "ratio": round((r["cartons"] or 0) / parcels, 2) if parcels else 0,
            "avg_parcel_m3": round((r["volume"] or 0) / parcels, 3) if parcels else None,
            "volume": round(r["volume"] or 0, 2),
        })
    rows.sort(key=lambda x: -x["ratio"])          # najlepszy konsolidator na górze
    recent = qs.select_related("hu", "controller")[:50]
    return render(request, "ui/control/gls_report.html", {
        "rows": rows, "recent": recent, "d_from": d_from, "d_to": d_to,
        "total_cartons": sum(x["cartons"] for x in rows),
        "total_parcels": sum(x["parcels"] for x in rows),
    })


@_controller
def hu_control_recheck_list(request):
    """HUs awaiting re-control — scan or pick from the list. Re-control then runs
    on the normal detail screen, which shows only the erroneous positions with the
    expected quantity visible."""
    wh = request.GET.get("wh", "").strip()
    hus = list(_controllable(request, HandlingUnit.objects.select_related("shipment")
               .filter(status="to_recheck")))
    if wh:
        hus = [h for h in hus if h.warehouse_type == wh]
    # Wiek rekontroli (roadmapa Q4): najstarsze pierwsze, ponad limit — oznaczone.
    hus = _attach_recheck_age(hus)
    # Tylko strefy POD KONTROLĄ (jak w find_recipient) — dropdown pokazywał wszystkie
    # typy z systemu (0010, 0070, BROK…), co nie ma sensu na liście rekontroli.
    _controlled = ControlledWarehouseType.controlled_codes()
    wh_types = sorted(t for t in HandlingUnit.objects.filter(status="to_recheck")
                      .order_by().values_list("warehouse_type", flat=True).distinct()
                      if t and (_controlled is None or t in _controlled))
    return render(request, "ui/scanner/recheck_list.html",
                  {"hus": hus, "wh_types": wh_types, "wh": wh})


@_controller
def hu_control_history(request, pk):
    """Full control history of one HU — every position-count attempt (who, when, result,
    counted vs expected, time per position), including all re-controls."""
    hu = get_object_or_404(HandlingUnit.objects.select_related("shipment"), pk=pk)
    # Historia to pełny ślad audytowy palety (kto, kiedy, ile naliczył) — należy jej się
    # ta sama bramka strefy co samej kontroli, inaczej deep-link daje wgląd w cudzy obszar.
    if not _zone_ok(request.user, hu):
        messages.error(request, f"Brak uprawnień do kontroli w strefie „{hu.warehouse_type or '—'}”.")
        return redirect("ui:hu_control_menu")
    attempts = (hu.control_attempts.select_related("controller", "item")
                .order_by("created_at"))
    status_events = hu.status_events.select_related("by_user").order_by("created_at")
    return render(request, "ui/scanner/hu_history.html",
                  {"hu": hu, "attempts": attempts, "status_events": status_events})


# escaped: etykieta z choices modelu („Wyjechało bez kontroli"), nie „eskalacja" (BIZ-007).
_STATUS_LABEL = {"planned": "zaplanowane", "in_control": "w kontroli",
                 "ok": "zgodne", "to_recheck": "do rekontroli",
                 "escaped": status_label("escaped")}


@_controller
def hu_control_status(request):
    """Scanner screen: 3-poziomowa hierarchia — podsumowanie zbiorcze → karty stref
    (kolor przewoźnika) → lista HU grupowana po miejscu składowania, z objętościami."""
    from collections import OrderedDict
    from ui.theme import theme_for, shipment_type_for, SHIPMENT_TYPE_THEMES, zone_colors_for
    # _controllable, nie _filter_controlled: ekran wylicza konkretne palety z lokalizacjami,
    # więc musi respektować także macierz stref użytkownika, nie tylko globalny zakres lidera.
    from django.db.models import Count, Q as _Q
    hus = list(_controllable(request, HandlingUnit.objects.all())
               .select_related("shipment__customer")
               # pozycje policzone/wszystkie per HU → licznik „poz. X/Y" per strefa
               .annotate(n_items=Count("items"),
                         n_done=Count("items", filter=_Q(items__controlled=True))))
    # ponytail: hu_metrics batchuje po całym querysecie ekranu (statusowy, zbiorczy);
    # denormalizacja objętości na HU, gdy listy urosną do setek tysięcy (patrz hu_metrics.py).
    metrics = hu_metrics(hus)

    def _blank_counts():
        return {s: 0 for s in STATUSES}          # huctl.rules.STATUS_GROUPS (z escaped)

    zones = OrderedDict()          # warehouse_type → dict strefy
    summary = {**_blank_counts(), "total": 0, "volume": 0.0, "estimated": False}
    from ui.theme import carrier_for
    for hu in hus:
        wt = (hu.warehouse_type or "—").upper()
        # Grupowanie per PROCES (EXPORT = 92EX+WCEX itd.) — strefy WC* i 92*/94*
        # to jeden proces logistyczny, więc jedna karta; kody stref w podtytule.
        proc = carrier_for(wt) if wt != "—" else "—"
        m = metrics.get(hu.pk, {})
        vol = m.get("volume_m3") or 0.0
        est = bool(m.get("estimated"))
        zone = zones.setdefault(proc, {
            "code": proc, "codes": set(),
            "theme": theme_for(hu.warehouse_type), **_blank_counts(),
            "total": 0, "volume": 0.0, "estimated": False, "_loc": OrderedDict(),
            "pos_done": 0, "pos_total": 0})
        zone["codes"].add(wt)
        zone["pos_done"] += hu.n_done
        zone["pos_total"] += hu.n_items
        summary["pos_done"] = summary.get("pos_done", 0) + hu.n_done
        summary["pos_total"] = summary.get("pos_total", 0) + hu.n_items
        loc_key = hu.location or "—"
        loc = zone["_loc"].setdefault(loc_key, {
            "location": loc_key, "count": 0, "volume": 0.0, "estimated": False, "hus": []})
        stype = shipment_type_for(hu)
        cust = getattr(hu.shipment, "customer", None) if hu.shipment_id else None
        loc["hus"].append({
            "ref": hu.code or f"{hu.shipment_id}-P{hu.seq}",
            "status": hu.status, "status_label": _STATUS_LABEL.get(hu.status, hu.status),
            "ship_type": stype, "type_theme": SHIPMENT_TYPE_THEMES[stype],
            "client": (getattr(cust, "name", "") or "").strip(),
            "vip": bool(getattr(cust, "is_vip", False)),
            "volume": vol, "estimated": est,
            "pos_done": hu.n_done, "pos_total": hu.n_items,
        })
        # agregacja liczników + objętości na trzech poziomach
        for scope in (zone, loc, summary):
            scope["volume"] += vol
            scope["estimated"] = scope["estimated"] or est
        loc["count"] += 1
        for scope in (zone, summary):
            scope[hu.status] = scope.get(hu.status, 0) + 1
            scope["total"] += 1

    zone_list = []
    for zone in sorted(zones.values(), key=lambda z: z["code"]):
        locs = sorted(zone.pop("_loc").values(),
                      key=lambda l: (l["location"] == "—", l["location"]))
        # Sortowanie naturalne refów ("2-P2" przed "2-P10", nie leksykograficznie).
        import re as _re

        def _natkey(h):
            # (0,liczba)/(1,tekst): stabilne także gdy refy mają różne formaty.
            return [(0, int(t)) if t.isdigit() else (1, t)
                    for t in _re.split(r"(\d+)", h["ref"])]
        for loc in locs:
            loc["hus"].sort(key=_natkey)
        zone["locations"] = locs
        zone["done"], zone["scope"], zone["pct"] = progress(zone)
        # „92EX + WCEX" w tytule karty procesu; kolor liczony po pierwszym kodzie,
        # żeby paleta była stabilna względem widoku sprzed grupowania.
        zone["zone_codes"] = " + ".join(sorted(zone.pop("codes")))
        zone_list.append(zone)

    # Postęp bez `escaped` (osobna grupa — ani otwarta praca, ani zgodne; BIZ-007).
    summary["done"], summary["scope"], summary["pct"] = progress(summary)
    # Kolor NAGŁÓWKA unikalny per strefa (badge zostaje carrierowy). Jeden przebieg na
    # cały zbiór stref → gwarancja braku kolizji + kontrast tekstu.
    zcolors = zone_colors_for([z["code"] for z in zone_list])
    for z in zone_list:
        zc = zcolors.get(z["code"].upper())
        z["zone_bg"] = zc["bg"] if zc else z["theme"]["header_bg"]
        z["zone_text"] = zc["text"] if zc else "#ffffff"
    return render(request, "ui/scanner/status.html",
                  {"zones": zone_list, "summary": summary,
                   "escaped_label": status_label("escaped")})


@_controller
def hu_error_report(request):
    """Errors found in HU control — filterable, grouped by error type / picker, CSV export."""
    from .hu import _parse_date_any
    f_ship = request.GET.get("shipment", "").strip()
    f_client = request.GET.get("client", "").strip()
    f_picker = request.GET.get("picker", "").strip()
    # Parsuj daty (jak w gls_packing_report) — surowy string w filtrze __date wywala 500 na złej dacie.
    f_from = _parse_date_any(request.GET.get("from", "").strip())
    f_to = _parse_date_any(request.GET.get("to", "").strip())

    # Raport imiennie wskazuje pickerów, więc widok zawężamy do stref kontrolera —
    # filtrujemy po HU, bo to na palecie wisi typ magazynu.
    items = (HandlingUnitItem.objects
             .filter(result="error",
                     hu__in=_controllable(request, HandlingUnit.objects.all()))
             .select_related("hu", "hu__shipment", "hu__shipment__customer",
                             "hu__controlled_by")
             .order_by("-controlled_at", "hu__shipment_id", "hu__seq"))
    if f_ship:
        items = items.filter(hu__shipment__name__icontains=f_ship)
    if f_client:
        # Reklamacje: szukanie po kliencie — nazwa, kod wewnętrzny, KUNNR (z karty
        # klienta lub prosto z wsadu SAP, gdy shipment nie ma dopiętego Customer-a).
        items = items.filter(Q(hu__shipment__customer__name__icontains=f_client) |
                             Q(hu__shipment__customer__code__icontains=f_client) |
                             Q(hu__shipment__customer__kunnr__icontains=f_client) |
                             Q(hu__shipment__kunnr__icontains=f_client))
    if f_picker:
        items = items.filter(Q(hu__picker__icontains=f_picker) |
                             Q(hu__shipment__author__icontains=f_picker))
    if f_from:
        items = items.filter(controlled_at__date__gte=f_from)
    if f_to:
        items = items.filter(controlled_at__date__lte=f_to)

    def _client(it):
        """(nr, nazwa) klienta — z karty klienta, fallback na KUNNR z wsadu SAP."""
        cust = it.hu.shipment.customer
        if cust:
            return (cust.code or cust.kunnr or "", cust.name or "")
        return (it.hu.shipment.kunnr or "", "")

    flag_labels = dict(HandlingUnitItem.ERROR_FLAGS)
    flag_counts = {k: 0 for k, _ in HandlingUnitItem.ERROR_FLAGS}
    by_picker, by_ref, by_day, rows = {}, {}, {}, []
    for it in items:
        active = [flag_labels[k] for k, v in (it.error_flags or {}).items() if v and k in flag_labels]
        for k, v in (it.error_flags or {}).items():
            if v and k in flag_counts:
                flag_counts[k] += 1
        picker = it.hu.picker or it.hu.shipment.author or "—"
        by_picker[picker] = by_picker.get(picker, 0) + 1
        # #17 trend jakości: który indeks (REF) najczęściej błędny + rozkład w czasie.
        ref = it.ref_code or "—"
        d = by_ref.setdefault(ref, {"count": 0, "name": it.description or ""})
        d["count"] += 1
        if it.controlled_at:
            day = it.controlled_at.date().isoformat()
            by_day[day] = by_day.get(day, 0) + 1
        client_no, client_name = _client(it)
        rows.append({"it": it, "flags": active,
                     "client_no": client_no, "client_name": client_name})

    if request.GET.get("export") == "csv":
        from ui.views.core.xlsx import safe_csv_writer  # SEC-007
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="raport_bledow_hu.csv"'
        resp.write("﻿")
        w = safe_csv_writer(resp, delimiter=";")
        w.writerow(["Data kontroli", "HU", "Dostawa", "Nr klienta", "Klient",
                    "REF", "Zliczono", "Jedn.",
                    "Oczekiwano (JP)", "Błędy", "Picker", "Kontroler"])
        for r in rows:
            it = r["it"]
            w.writerow([
                it.controlled_at.strftime("%Y-%m-%d %H:%M") if it.controlled_at else "",
                it.hu.ref, it.hu.shipment.name,
                r["client_no"], r["client_name"], it.ref_code,
                it.counted_qty if it.counted_qty is not None else "", it.counted_unit,
                f"{it.base_qty} {it.base_unit}", ", ".join(r["flags"]) or "rozbieżność ilości",
                it.hu.picker or it.hu.shipment.author or "",
                it.hu.controlled_by.username if it.hu.controlled_by else ""])
        return resp

    # Agregaty (liczniki flag, by_picker, total) liczone nad PEŁNYM zbiorem powyżej;
    # paginujemy tylko tabelę wierszy, by nie renderować setek pozycji naraz.
    from django.core.paginator import Paginator
    page_obj = Paginator(rows, 50).get_page(request.GET.get("page"))
    return render(request, "ui/planner/hu_error_report.html", {
        "rows": page_obj, "page_obj": page_obj,
        "flag_counts": [(flag_labels[k], c) for k, c in flag_counts.items() if c],
        "by_picker": sorted(by_picker.items(), key=lambda x: -x[1]),
        # #17: top 15 najczęściej błędnych indeksów + trend dzienny (rosnąco po dacie).
        "by_ref": sorted(((r, d["count"], d["name"]) for r, d in by_ref.items()),
                         key=lambda x: -x[1])[:15],
        "by_day": sorted(by_day.items()),
        "total": len(rows),
        "f_ship": f_ship, "f_client": f_client,
        "f_picker": f_picker, "f_from": f_from, "f_to": f_to,
    })


def _shift_bounds(now):
    """Current shift window from settings KPI_SHIFTS (list of 'HH:MM' starts), or a
    single all-day shift. Returns (start_dt, end_dt) covering `now`."""
    from datetime import timedelta
    starts = getattr(settings, "KPI_SHIFTS", []) or []
    mins = []
    for s in starts:
        try:
            h, m = s.split(":")
            mins.append(int(h) * 60 + int(m))
        except (ValueError, AttributeError):
            continue
    # Północ w strefie LOKALNEJ (nie UTC) — inaczej granice zmian są przesunięte o offset
    # strefy i praca nocnej zmiany trafia do złej zmiany/dnia. Porównania też w lokalnej.
    now = timezone.localtime(now)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if not mins:
        return day, day + timedelta(days=1)
    mins = sorted(set(mins))
    starts_today = [day + timedelta(minutes=x) for x in mins]
    cur = max([s for s in starts_today if s <= now], default=None)
    if cur is None:                                   # before first shift → previous day's last
        cur = starts_today[-1] - timedelta(days=1)
    nxt = min([s for s in starts_today if s > cur], default=cur + timedelta(days=1))
    return cur, nxt


def _kpi_period_bounds(period, now=None):
    """(period, start, end) dla 'today' | 'shift' | 'month' — wspólne dla ekranu KPI
    i skrótu wyników na panelu (dashboardzie) modułu."""
    from datetime import timedelta
    now = now or timezone.now()
    local = timezone.localtime(now)          # granice dnia/miesiąca w strefie LOKALNEJ, nie UTC
    if period == "shift":
        start, end = _shift_bounds(now)
    elif period == "week":
        start = (local - timedelta(days=local.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        end = now + timedelta(days=1)
    elif period == "month":
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now + timedelta(days=1)
    else:
        period = "today"
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now + timedelta(days=1)
    return period, start, end


@_leader
def hu_control_kpi(request):
    """Controller productivity KPI (leader/admin only) — positions, HUs, average time
    per position (gap to the previous position, breaks excluded), detected errors.

    Ekran kierownika — renderowany w chromie desktopowym (nie w skanerze), bo wyniki
    kontroli mają być widoczne na panelu modułu, a nie na urządzeniu kontrolera."""
    period, start, end = _kpi_period_bounds(request.GET.get("period", "today"))
    by = "zone" if request.GET.get("by") == "zone" else "controller"
    rows, totals = _kpi_stats(start, end, by=by)
    target_pph = float(getattr(settings, "KPI_TARGET_POS_PER_H", 0) or 0)

    if request.GET.get("export") == "csv":
        from ui.views.core.xlsx import safe_csv_writer  # SEC-007
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="kpi_kontrola.csv"'
        resp.write("﻿")
        w = safe_csv_writer(resp, delimiter=";")
        w.writerow(["Strefa" if by == "zone" else "Kontroler",
                    "Pozycje (unikalne)", "Próby (audyt)", "HU",
                    "Wykryte błędy (jakość — pożądane)", "% wykrytych",
                    "Czas netto [min]", "Pozycje/h (premia)", "Śr. czas/pozycję [s]",
                    "Jednostki (suma)", "Próby offline (sync)"])
        for r in rows:
            w.writerow([r["controller"], r["distinct_positions"], r["positions"], r["hus"],
                        r["errors"], r["error_rate"], r["net_min"],
                        r["pos_per_h"] if r["pos_per_h"] is not None else "",
                        r["avg_sec"] if r["avg_sec"] is not None else "",
                        r["units"], r["offline"]])
        return resp
    return render(request, "ui/control/kpi.html",
                  {"rows": rows, "period": period, "start": start,
                   "totals": totals, "target_pph": target_pph, "by": by})

__all__ = [
    "gls_packing_report",
    "hu_control_recheck_list",
    "hu_control_history",
    "_STATUS_LABEL",
    "hu_control_status",
    "hu_error_report",
    "_shift_bounds",
    "_kpi_period_bounds",
    "_kpi_stats",
    "hu_control_kpi",
]
