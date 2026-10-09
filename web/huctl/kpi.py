"""KPI kontroli HU — jedno źródło prawdy liczb wydajności (serwis, nie widok).

Wołane z ekranu KPI, panelu lidera, hubu i z raportu dziennego (Celery) w rdzeniu
`ui`. Wydzielone z huctl.views.hu_reports w ARCH-001."""
from django.conf import settings

from huctl.models import HUControlAttempt, HUErrorInvestigation


def kpi_stats(start, end, by="controller"):
    """(rows, totals) — wydajność w oknie [start, end). `by`='controller' (domyślnie)
    grupuje per kontroler; 'zone' grupuje per typ magazynu (strefa) — ten sam zestaw
    metryk w innym przekroju (panel lidera, Q z grillowania).

    Wydzielone z hu_control_kpi, żeby panel kierownika na dashboardzie modułu liczył
    dokładnie te same liczby co pełny ekran KPI (jedno źródło prawdy)."""
    # Leniwie: huctl.views importuje ten moduł (alias _kpi_stats) — top-level byłby cyklem.
    from huctl.views.hu_helpers import _filter_controlled

    max_gap = float(getattr(settings, "KPI_MAX_GAP_SECONDS", 600))   # breaks excluded
    # Raport liczy tylko kontrolowane typy magazynu (zakres wybrany na hubie).
    # CODE-009: krotki z values_list zamiast instancji modeli (+ select_related) — pętla po
    # próbach z całej zmiany/tygodnia była zdominowana kosztem budowy obiektów (20 tys. prób:
    # ~1,4 s → patrz test_kpi_perf). Te same pola, ta sama kolejność; pk rozstrzyga remisy
    # created_at deterministycznie (ostatnia próba pozycji wygrywa w last_count/last).
    atts = (_filter_controlled(
                HUControlAttempt.objects.filter(created_at__gte=start, created_at__lt=end),
                field="hu__warehouse_type")
            .order_by("created_at", "pk")
            .values_list("hu_id", "item_id", "controller__username", "hu__warehouse_type",
                         "seconds_since_prev", "input_source", "created_at", "counted_qty",
                         "counted_unit", "result", "error_flags"))
    stats = {}
    for (hu_id, item_id, username, zone, since_prev, source, created_at, counted_qty,
         counted_unit, result, error_flags) in atts:
        if by == "zone":
            name = zone or "—"
        else:
            name = username if username is not None else "—"
        r = stats.setdefault(name, {"positions": 0, "hus": set(), "errors": 0,
                                    "gap_sum": 0.0, "gap_n": 0, "seen": set(),
                                    "last_count": {}, "fast": 0, "keyboard": 0,
                                    "last": None})
        r["positions"] += 1                            # attempts (audyt) — nie do przepustowości
        r["seen"].add((hu_id, item_id))                # DISTINCT pozycje → przepustowość (F3)
        r["hus"].add(hu_id)
        # Warstwa C — sygnały nadużyć (detekcja, nie blokada) dla panelu lidera: pozycje
        # „liczone" szybciej niż fizycznie możliwe (<3 s) i sesje z ręcznym wpisem zamiast
        # skanu. Panel lidera filtruje po nich; ekran KPI ich nie renderuje.
        if since_prev is not None and since_prev < 3:
            r["fast"] += 1
        if source == "keyboard":
            r["keyboard"] += 1
        r["last"] = created_at                         # posortowane rosnąco → ostatnia próba wygrywa
        # Jednostki: OSTATNIA próba per pozycja wygrywa (posortowane po created_at),
        # więc edycje/rekontrole nie dublują sum sztuk/kartonów.
        if counted_qty is not None and counted_unit:
            r["last_count"][(hu_id, item_id)] = (counted_unit, counted_qty)
        if result == "error" or any((error_flags or {}).values()):
            r["errors"] += 1
        if since_prev is not None and 0 < since_prev <= max_gap:
            r["gap_sum"] += since_prev
            r["gap_n"] += 1

    # Spec UX §3/§5.3: POTWIERDZONE wyjaśnianie błędu nie liczy się do czasu netto
    # kontrolera (pauza ważna po ack pickera/lidera; odrzucone/niepotwierdzone — liczy się).
    if by == "controller":
        for inv in (HUErrorInvestigation.objects
                    .filter(started_at__lt=end, ended_at__gte=start,
                            confirmed_at__isnull=False, rejected=False,
                            ended_at__isnull=False)
                    .select_related("controller")):
            if not inv.kpi_excluded():        # spóźniony ack → pauza nieważna
                continue
            name = inv.controller.get_username() if inv.controller else "—"
            if name in stats:
                # Przycinamy do okna raportu — inwestygacja przekraczająca granice
                # nie odejmuje czasu spoza [start, end).
                ov = (min(inv.ended_at, end) - max(inv.started_at, start)).total_seconds()
                stats[name]["gap_sum"] = max(0.0, stats[name]["gap_sum"] - max(0.0, ov))

    def _units_str(counts):
        """{(hu,item): (unit, qty)} → "3 PAL · 100 KAR · 250 OP" (malejąco po sumie)."""
        sums = {}
        for unit, qty in counts.values():
            sums[unit] = sums.get(unit, 0.0) + qty
        parts = sorted(sums.items(), key=lambda kv: -kv[1])
        return " · ".join(f"{qty:g} {unit}" for unit, qty in parts)
    # Target throughput (positions/hour); rows below it are flagged for the leader.
    target_pph = float(getattr(settings, "KPI_TARGET_POS_PER_H", 0) or 0)
    offline = offline_counts(start, end, by=by)        # znacznik wpisów z sync offline (BIZ-006)
    rows = []
    for name, r in stats.items():
        net_sec = r["gap_sum"]                         # net working time (breaks excluded)
        avg = round(net_sec / r["gap_n"]) if r["gap_n"] else None
        distinct = len(r["seen"])                      # unikalne pozycje (rework nie zawyża, F3)
        # positions/hour po DISTINCT pozycjach nad czasem netto — podstawa premii (Q76). Edity
        # i rekontrole tej samej pozycji NIE nabijają przepustowości.
        pph = round(distinct / (net_sec / 3600.0), 1) if net_sec > 0 else None
        # 'errors' = WYKRYTE błędy pickera (sygnał JAKOŚCI — pożądany), nie błędy kontrolera.
        err_rate = round(100.0 * r["errors"] / r["positions"], 1) if r["positions"] else 0.0
        rows.append({"controller": name, "positions": r["positions"],
                     "distinct_positions": distinct, "hus": len(r["hus"]),
                     "errors": r["errors"], "avg_sec": avg, "net_sec": round(net_sec),
                     "net_min": round(net_sec / 60.0, 1), "pos_per_h": pph,
                     "error_rate": err_rate, "units": _units_str(r["last_count"]),
                     "below_target": bool(target_pph and pph is not None and pph < target_pph),
                     "fast": r["fast"], "keyboard": r["keyboard"], "last": r["last"],
                     "offline": offline.get(name, 0)})
    rows.sort(key=lambda x: -x["distinct_positions"])
    all_counts = {}
    for r in stats.values():
        all_counts.update(r["last_count"])
    # UNIA zbiorów, nie suma per-kontroler: HU/pozycja dotknięta przez dwóch kontrolerów
    # (oryginał + rekontrola — z definicji INNY kontroler) była liczona 2×. Etykiety w UI to
    # „Skontrolowanych pozycji/HU" (unikalne), więc suma prób/zbiorów je zawyżała.
    all_hus = set().union(*(r["hus"] for r in stats.values())) if stats else set()
    all_seen = set().union(*(r["seen"] for r in stats.values())) if stats else set()
    totals = {
        "positions": len(all_seen),
        "hus": len(all_hus),
        "errors": sum(r["errors"] for r in rows),
        "controllers": len(rows),
        "units": _units_str(all_counts),
        "offline": sum(r["offline"] for r in rows),
    }
    return rows, totals


def offline_counts(start, end, by="controller"):
    """BIZ-006: ile prób w oknie [start, end) przyszło z kolejki OFFLINE skanera
    (hu_control_sync) — rozpoznawane po niepustym `client_id` (tor online go nie nadaje),
    bez migracji. {kontroler|strefa: liczba}, ten sam klucz i zakres typów co kpi_stats.
    Osobne zapytanie agregujące: liczba zapytań nie rośnie z liczbą prób."""
    from django.db.models import Count
    from huctl.views.hu_helpers import _filter_controlled
    key = "hu__warehouse_type" if by == "zone" else "controller__username"
    qs = (_filter_controlled(
              HUControlAttempt.objects.filter(created_at__gte=start, created_at__lt=end),
              field="hu__warehouse_type")
          .exclude(client_id="").values(key).annotate(n=Count("id")).order_by())
    return {(row[key] or "—"): row["n"] for row in qs}
