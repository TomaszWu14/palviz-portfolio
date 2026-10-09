"""Prognoza wzrostu wolumenów z historii zadań EWM (plan 2026-10-02, etap 5) — czysty Python.

Tygodniowe sumy ruchów (pełne tygodnie, dni robocze) → trend wykładniczy: regresja liniowa
na log(tydzień). Roczne tempo wzrostu P50 = trend; P90 = trend + 1,28 × błąd standardowy
nachylenia (ostrożny scenariusz do wymiarowania). Test wsteczny: model bez ostatnich
BACKTEST_WEEKS tygodni prognozuje je — MAPE pokazuje, ile wart jest trend.

ponytail: bez sezonowości rocznej (wymaga ≥ 2 lat) — dzień projektowy P95 już niesie szczyt;
ETS/Holt-Winters z sezonowością, gdy historia obejmie dwa pełne lata.
"""
import math
import statistics
from collections import defaultdict

from .design_day import STREAMS, _stream_value, working_days

BACKTEST_WEEKS = 8
MIN_WEEKS = 12
Z90 = 1.2816


def weekly(daily, key="total"):
    """[(poniedziałek tygodnia, suma)] — tylko pełne tygodnie (bez pierwszego i ostatniego, jeśli
    obcięte) i dni robocze; tygodnie z zerem (przestój) pomijane."""
    days = working_days(daily)
    if not days:
        return []
    weeks = defaultdict(int)
    for d in days:
        weeks[d.toordinal() - d.weekday()] += _stream_value(daily[d], key)
    first, last = min(weeks), max(weeks)
    if days[0].toordinal() != first:
        weeks.pop(first, None)
    if days[-1].toordinal() - last < 4:                 # ostatni tydzień bez piątku = niepełny
        weeks.pop(last, None)
    return sorted((w, v) for w, v in weeks.items() if v > 0)


def fit(series):
    """series: [(dzień porządkowy, wartość)] → (nachylenie log/tydzień, wyraz wolny, błąd nachylenia)."""
    xs = [(w - series[0][0]) / 7 for w, _ in series]
    ys = [math.log(v) for _, v in series]
    slope, icpt = statistics.linear_regression(xs, ys)
    n = len(xs)
    resid = [y - (icpt + slope * x) for x, y in zip(xs, ys, strict=True)]
    sxx = sum((x - statistics.fmean(xs)) ** 2 for x in xs)
    se = math.sqrt(sum(r * r for r in resid) / (n - 2) / sxx) if n > 2 and sxx else 0.0
    return slope, icpt, se


def backtest(series, weeks=BACKTEST_WEEKS):
    """MAPE [%] prognozy ostatnich `weeks` tygodni z modelu uczonego bez nich (None — za mało danych)."""
    if len(series) < weeks + MIN_WEEKS // 2:
        return None
    train, test = series[:-weeks], series[-weeks:]
    slope, icpt, _ = fit(train)
    errs = [abs(v - math.exp(icpt + slope * (w - train[0][0]) / 7)) / v for w, v in test]
    return round(100 * statistics.fmean(errs), 1)


def forecast(daily, years=5):
    """Wzrost per strumień: roczne tempo P50/P90, mnożnik na `years` lat, MAPE testu wstecznego."""
    out = []
    for key, label in sorted(STREAMS, key=lambda s: s[0] != "total"):     # „Ruchy razem” na górze
        if key == "orders":
            continue
        series = weekly(daily, key)
        if len(series) < 3:
            continue
        slope, _, se = fit(series)
        g50 = math.exp(52 * slope) - 1
        g90 = math.exp(52 * (slope + Z90 * se)) - 1
        out.append({"key": key, "label": label, "weeks": len(series),
                    "growth_p50_pct": round(100 * g50, 1), "growth_p90_pct": round(100 * g90, 1),
                    "mult_p50": round(max(0.1, (1 + g50) ** years), 2),
                    "mult_p90": round(max(0.1, (1 + g90) ** years), 2),
                    "mape_pct": backtest(series), "reliable": len(series) >= MIN_WEEKS})
    return out
