"""Demonstracyjny eksport zadań magazynowych EWM (/SCWM/MON) do importu w GROOVE.

Gdy nie ma prawdziwego eksportu: N tygodni (pn–pt, zmiany 5–13 i 13–21) w skali ACME
z 2026-10-02 — dziennie ~1 020 przyjęć palet, ~410 wydań, ~7 500 linii kompletacji (paczki
2–3 kartony), ~200 uzupełnień, ~50 przesunięć; trend ~8 %/rok + szum. Lokalizacje to kody hali
Logistyczna (B0-<przejście 01–54>-<gniazdo><pozycja><poziom>), więc kalibracja na modelu
obecnej hali je rozpozna. Wózki W01–W12 i pickerzy P01–P20 mają realistyczne odstępy między
potwierdzeniami. Dane SZTUCZNE — tylko do sprawdzenia ścieżki projektowania magazynu.

    python tools/ewm_demo_tasks.py [plik.xlsx] [--weeks 13] [--end 2026-09-25] [--scale 1.0]
"""
import argparse
import random
from datetime import date, datetime, timedelta

from openpyxl import Workbook

# Gniazda w przejściach 01–54 hali Logistyczna (EWM, porównanie z rysunkiem 2026-09-25).
BAYS = [21, 21, 21, 21, 21, 39, 39, 39, 38, 38, 38, 39, 38, 39, 39, 39, 38, 38, 38, 38, 38, 39, 39, 38, 38,
        39, 38, 38, 38, 39, 39, 39, 38, 42, 42, 42, 42, 41, 23, 23, 23, 23, 23, 23, 23, 23, 23, 36, 36, 36,
        36, 36, 36, 34]
LEVELS = "ABCDE"
DAILY = {"putaway": 1020, "outbound": 410, "picking": 7500, "replenishment": 200, "move": 50}
PROCESS = {"putaway": ("1010", "INB-PAL"), "outbound": ("2030", "OUT-LOAD"), "picking": ("2010", "PICK-K1"),
           "replenishment": ("3010", "REPL"), "move": ("3040", "INTERNAL")}
WEEKDAY = [1.10, 1.05, 1.00, 0.95, 0.90]
GROWTH_YEAR = 0.08
SHIFTS = ((5, 13), (13, 21))
HEADERS = ["Zadanie magazynowe", "Rodzaj procesu magazynowego", "Źródłowe miejsce składowania",
           "Docelowe miejsce składowania", "Produkt", "Partia", "Ilość docelowa w AJM", "AJM",
           "HU źródłowa", "HU docelowa", "Dokument", "Utworzono dn.", "Utworzono o", "Potwierdzono dn.",
           "Potwierdzono o", "Potwierdzone przez", "Zasób", "Kolejka", "Status zadania magazynowego"]


def bin_code(rng, level=None):
    aisle = rng.randrange(len(BAYS))
    bay = 10 + rng.randrange(BAYS[aisle])
    return f"B0-{aisle + 1:02d}-{bay}{rng.randrange(3)}{level or rng.choice(LEVELS)}"


def day_tasks(rng, day, week, scale, materials, weights):
    """Zadania jednego dnia: [(rodzaj, materiał, dokument)] w kolejności do rozdania zasobom."""
    factor = scale * (1 + GROWTH_YEAR) ** (week / 52) * WEEKDAY[day.weekday()] * rng.uniform(0.88, 1.12)
    out = {}
    for kind, n in DAILY.items():
        mats = rng.choices(materials, weights, k=round(n * factor))
        if kind == "picking":                       # paczki: 2–3 linie na dokument (dostawę)
            docs, i = [], 0
            while i < len(mats):
                k = rng.choice((2, 2, 3))
                docs += [f"80{day:%y%m%d}{len(docs):05d}"] * k
                i += k
            out[kind] = list(zip(mats, docs[:len(mats)], strict=True))
        else:
            out[kind] = [(m, f"81{day:%y%m%d}{j // 33:04d}" if kind == "outbound" else "") for j, m in enumerate(mats)]
    return out


def timeline(rng, day, shift, n, mean_gap):
    """`n` chwil potwierdzeń jednego zasobu w zmianie: start + odstępy ~ mean_gap, przerwa 30 min."""
    t = datetime(day.year, day.month, day.day, shift[0]) + timedelta(seconds=rng.uniform(60, 600))
    end = datetime(day.year, day.month, day.day, shift[1])
    gap = min(mean_gap, (end - t).total_seconds() * 0.9 / max(1, n))
    out = []
    for i in range(n):
        if i == n // 2:
            t += timedelta(minutes=30)
        t += timedelta(seconds=max(8.0, rng.lognormvariate(0, 0.35) * gap))
        out.append(min(t, end - timedelta(seconds=1)))
    return out


def rows_for_day(rng, day, week, scale, materials, weights, counter):
    tasks = day_tasks(rng, day, week, scale, materials, weights)
    trucks = [("putaway", m, d) for m, d in tasks["putaway"]] + [("outbound", m, d) for m, d in tasks["outbound"]] \
        + [("replenishment", m, d) for m, d in tasks["replenishment"]] + [("move", m, d) for m, d in tasks["move"]]
    rng.shuffle(trucks)
    picks = [("picking", m, d) for m, d in tasks["picking"]]
    out = []
    for jobs, prefix, per_shift, gap in ((trucks, "W", 6, 200.0), (picks, "P", 10, 42.0)):
        half = len(jobs) // 2
        for s, (shift, chunk) in enumerate(zip(SHIFTS, (jobs[:half], jobs[half:]), strict=True)):
            for r in range(per_shift):
                mine = chunk[r::per_shift]
                who = f"{prefix}{s * per_shift + r + 1:02d}"
                for (kind, mat, doc), at in zip(mine, timeline(rng, day, shift, len(mine), gap), strict=True):
                    counter[0] += 1
                    out.append(row(rng, counter[0], kind, mat, doc, at, who, prefix == "W"))
    return out


def row(rng, no, kind, mat, doc, at, who, truck):
    src, dst = {"putaway": (f"GR-ZONE-0{rng.randint(1, 7)}", None), "outbound": (None, f"GI-ZONE-0{rng.randint(1, 4)}"),
                "picking": (None, f"PACK-0{rng.randint(1, 4)}"), "replenishment": (None, None),
                "move": (None, None)}[kind]
    src = src or bin_code(rng, "A" if kind == "picking" else None)
    dst = dst or bin_code(rng, "A" if kind == "replenishment" else None)
    created = at - timedelta(minutes=rng.uniform(2, 40))
    pt, queue = PROCESS[kind]
    hu = f"00{rng.randrange(10**15, 10**16)}"
    return [f"{no:010d}", pt, src, dst, mat, f"L{rng.randrange(10**6):06d}",
            rng.randint(1, 4) if kind == "picking" else 1, "KAR" if kind == "picking" else "PAL",
            hu, hu if kind != "picking" else "", doc, f"{created:%d.%m.%Y}", f"{created:%H:%M:%S}",
            f"{at:%d.%m.%Y}", f"{at:%H:%M:%S}", f"OP{who}", who if truck else "", queue, "C"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", nargs="?", default="zadania_ewm_demo.xlsx")
    ap.add_argument("--weeks", type=int, default=13)
    ap.add_argument("--end", default="2026-09-25", help="ostatni piątek danych (RRRR-MM-DD)")
    ap.add_argument("--scale", type=float, default=1.0, help="mnożnik wolumenu (np. 0.2 = mniejszy plik)")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    materials = [f"1{n:07d}" for n in range(3000)]
    weights = [1 / (i + 1) ** 0.9 for i in range(len(materials))]          # rotacja ABC (Pareto)
    end = date.fromisoformat(a.end)
    start = end - timedelta(days=end.weekday()) - timedelta(weeks=a.weeks - 1)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Zadania magazynowe")
    ws.append(HEADERS)
    counter, n = [0], 0
    for w in range(a.weeks):
        for d in range(5):
            day = start + timedelta(days=7 * w + d)
            for r in rows_for_day(rng, day, w, a.scale, materials, weights, counter):
                ws.append(r)
                n += 1
    wb.save(a.out)
    print(f"{a.out}: {n} zadań, {start:%d.%m.%Y}–{end:%d.%m.%Y}")


if __name__ == "__main__":
    main()
