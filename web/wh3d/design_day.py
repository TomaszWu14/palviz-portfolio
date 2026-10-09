"""Profil ruchów i dzień projektowy z zadań EWM (spec projektowania magazynu, krok 3).

Baza agreguje zadania partii (dzień/godzina × rodzaj ruchu, materiał × dzień, linie na
dokument) — `load_inputs`; tu czyste liczenie na tych agregatach — `build_profile`:
dni robocze, percentyle strumieni, dzień projektowy, godzina szczytu, ABC/XYZ, profil zleceń.

Założenia: 1 zadanie WT przyjęcia/wydania = 1 paleta, 1 zadanie kompletacji = 1 linia;
zlecenie = unikalny dokument (dostawa) w kompletacji i wydaniach.
"""
import statistics
from collections import Counter, defaultdict

from .blender_stock import abc_by_hits

KINDS = ("putaway", "outbound", "picking", "replenishment", "move")
STREAMS = [("putaway", "Przyjęcia (palety IN)"), ("outbound", "Wydania (palety OUT)"),
           ("picking", "Linie kompletacji"), ("replenishment", "Uzupełnienia"),
           ("move", "Przesunięcia"), ("orders", "Zlecenia (dokumenty)"), ("total", "Ruchy razem")]
PERCENTILES = (90, 95, 99)
WORKDAY_SHARE = 0.10          # dzień roboczy = ruch ≥ 10 % mediany (niedziele/święta odpadają)
XYZ_CUTS = (0.5, 1.0)         # współczynnik zmienności dziennych pobrań: X < 0,5 ≤ Y < 1,0 ≤ Z
ORDER_BUCKETS = [(1, 1, "1"), (2, 5, "2–5"), (6, 10, "6–10"), (11, 20, "11–20"), (21, None, "21+")]
DEMAND_KINDS = ("picking", "outbound")


def percentile(values, p):
    """Percentyl z interpolacją liniową (jak numpy/Excel PERCENTILE.INC); pusta lista → 0."""
    vals = sorted(values)
    if len(vals) < 2:
        return float(vals[0]) if vals else 0.0
    return statistics.quantiles(vals, n=100, method="inclusive")[p - 1]


def _total(counts):
    return sum(counts.get(k, 0) for k in KINDS)


def working_days(daily):
    """Dni z ruchem ≥ WORKDAY_SHARE mediany dni z jakimkolwiek ruchem (rosnąco)."""
    totals = {d: _total(c) for d, c in daily.items() if _total(c) > 0}
    if not totals:
        return []
    threshold = statistics.median(totals.values()) * WORKDAY_SHARE
    return sorted(d for d, t in totals.items() if t >= threshold)


def _stream_value(counts, key):
    return _total(counts) if key == "total" else counts.get(key, 0)


def _abc_xyz(material_days, days):
    """ABC wg liczby pobrań (linie kompletacji + wydania), XYZ wg zmienności dziennej."""
    day_set = set(days)
    hits = {m: sum(n for d, n in per_day.items() if d in day_set) for m, per_day in material_days.items()}
    hits = {m: n for m, n in hits.items() if n > 0}
    abc = abc_by_hits(hits)
    matrix = {(a, x): {"skus": 0, "lines": 0} for a in "ABC" for x in "XYZ"}
    for m, n in hits.items():
        series = [material_days[m].get(d, 0) for d in days]
        mean = statistics.fmean(series)
        cv = statistics.pstdev(series) / mean if mean else 0.0
        xyz = "X" if cv < XYZ_CUTS[0] else ("Y" if cv < XYZ_CUTS[1] else "Z")
        cell = matrix[(abc[m], xyz)]
        cell["skus"] += 1
        cell["lines"] += n
    total_lines = sum(hits.values()) or 1
    return {
        "skus": len(hits),
        "rows": [{"abc": a, "cells": [{"xyz": x, **matrix[(a, x)],
                                       "share": round(100 * matrix[(a, x)]["lines"] / total_lines, 1)}
                                      for x in "XYZ"]} for a in "ABC"],
    }


def _groups(material_days, days, groups):
    """Pobrania (linie kompletacji + wydania) w dni robocze per grupa asortymentowa H1."""
    day_set, per = set(days), defaultdict(lambda: {"skus": 0, "lines": 0})
    for m, per_day in material_days.items():
        n = sum(v for d, v in per_day.items() if d in day_set)
        if n:
            g = per[groups.get(m) or "Bez hierarchii w danych SAP"]
            g["skus"] += 1
            g["lines"] += n
    total = sum(g["lines"] for g in per.values()) or 1
    return sorted(({"label": k, **v, "share": round(100 * v["lines"] / total, 1)} for k, v in per.items()),
                  key=lambda g: -g["lines"])


def _order_profile(lines_per_order, p):
    if not lines_per_order:
        return None
    buckets = []
    for lo, hi, label in ORDER_BUCKETS:
        n = sum(1 for v in lines_per_order if v >= lo and (hi is None or v <= hi))
        buckets.append({"label": label, "orders": n, "share": round(100 * n / len(lines_per_order), 1)})
    return {"orders": len(lines_per_order), "mean": round(statistics.fmean(lines_per_order), 1),
            "design": round(percentile(lines_per_order, p), 1), "buckets": buckets}


def build_profile(daily, hourly, material_days=None, lines_per_order=(), p=95, groups=None):
    """daily: {data: {rodzaj: n, "orders": n}}; hourly: {(data, godzina): {rodzaj: n}};
    material_days: {materiał: {data: linie}}; lines_per_order: [linie na dokument];
    groups: {materiał: grupa H1} z danych materiałowych SAP albo None (brak importu)."""
    days = working_days(daily)
    if not days:
        return None
    day_set = set(days)
    streams = []
    for key, label in STREAMS:
        vals = [_stream_value(daily[d], key) for d in days]
        if not any(vals):
            continue
        hours = [_stream_value(c, key) for (d, _h), c in hourly.items() if d in day_set]
        hours = [v for v in hours if v]
        streams.append({
            "key": key, "label": label, "mean": round(statistics.fmean(vals), 1),
            "p50": round(percentile(vals, 50), 1),
            "pcts": [(q, round(percentile(vals, q), 1)) for q in PERCENTILES],
            "max": max(vals), "design": round(percentile(vals, p), 1),
            "peak_hour": round(percentile(hours, p), 1) if hours else 0,
        })
    totals = {d: _total(daily[d]) for d in days}
    target = percentile(totals.values(), p)
    design_day = min(days, key=lambda d: (abs(totals[d] - target), -d.toordinal()))
    hour_avg = defaultdict(Counter)
    for (d, h), c in hourly.items():
        if d in totals:
            for k in KINDS:
                hour_avg[h][k] += c.get(k, 0)
    return {
        "p": p, "days_total": len(daily), "days_working": len(days),
        "first_day": days[0], "last_day": days[-1],
        "streams": streams,
        "design_day": {"date": design_day, "total": totals[design_day], "target": round(target, 1),
                       "hours": [_total(hourly.get((design_day, h), {})) for h in range(24)]},
        "hourly_avg": {k: [round(hour_avg[h][k] / len(days), 1) for h in range(24)] for k in KINDS},
        "daily_series": {"dates": [d.isoformat() for d in days], "total": [totals[d] for d in days]},
        "abc_xyz": _abc_xyz(material_days, days) if material_days else None,
        "orders": _order_profile(list(lines_per_order), p),
        "groups": _groups(material_days, days, groups) if groups is not None and material_days else None,
    }


# ─── ORM → agregaty ──────────────────────────────────────────────────────────

def load_groups(materials):
    """{materiał z zadań: H1} z danych materiałowych SAP — po MATNR (EWM), a gdy brak, po REF;
    None, gdy danych materiałowych jeszcze nie zaimportowano."""
    from ui.models import MaterialMaster

    if not MaterialMaster.objects.exists():
        return None
    keys = {m: m.strip().lstrip("0") for m in materials}
    qs = MaterialMaster.objects.exclude(h1="")
    by_matnr = dict(qs.filter(matnr__in=set(keys.values())).values_list("matnr", "h1"))
    by_ref = dict(qs.filter(ref__in=list(materials)).values_list("ref", "h1"))
    return {m: by_matnr.get(keys[m]) or by_ref.get(m) for m in materials}


def load_inputs(batch):
    """Agregaty zadań potwierdzonych partii (czas lokalny) — liczone w bazie, nie w Pythonie."""
    from django.db.models import Count, Q
    from django.db.models.functions import TruncDate, TruncHour
    from django.utils import timezone

    tz = timezone.get_current_timezone()
    qs = batch.tasks.exclude(confirmed_at=None).order_by()
    by_day = qs.annotate(d=TruncDate("confirmed_at", tzinfo=tz))
    daily = defaultdict(dict)
    for d, kind, n in by_day.values("d", "kind").annotate(n=Count("id")).values_list("d", "kind", "n"):
        daily[d][kind] = n
    demand = by_day.filter(kind__in=DEMAND_KINDS)
    for d, n in (demand.exclude(document="").values("d").annotate(n=Count("document", distinct=True))
                 .values_list("d", "n")):
        daily[d]["orders"] = n
    hourly = defaultdict(dict)
    for h, kind, n in (qs.annotate(h=TruncHour("confirmed_at", tzinfo=tz)).values("h", "kind")
                       .annotate(n=Count("id")).values_list("h", "kind", "n")):
        hourly[(h.date(), h.hour)][kind] = n
    material_days = defaultdict(dict)
    for m, d, n in (demand.exclude(material="").values("material", "d").annotate(n=Count("id"))
                    .values_list("material", "d", "n")):
        material_days[m][d] = n
    lines = list(qs.filter(Q(kind="picking") & ~Q(document="")).values("document")
                 .annotate(n=Count("id")).values_list("n", flat=True))
    return {"daily": dict(daily), "hourly": dict(hourly), "material_days": dict(material_days),
            "lines_per_order": lines}
