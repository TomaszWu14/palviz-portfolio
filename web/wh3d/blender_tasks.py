"""Wózki w animacji z realnych zadań magazynowych EWM (WT) zamiast symulacji demo.

Agent = zasób EWM (a gdy go brak — użytkownik potwierdzający). Zadania idą w kolejności
potwierdzenia; start zadania = znacznik potwierdzenia względem początku okna (÷ kompresja):
wózek czeka do tej chwili, a gdy jest zajęty poprzednim zadaniem, rusza zaraz po nim.
ponytail: start = potwierdzenie, nie (potwierdzenie − czas wykonania) — ruch jest spóźniony
o czas zadania; do skorygowania przy kalibracji symulacji na wariancie 0 (krok 7 speca).
"""
from datetime import datetime, timedelta

from django.utils import timezone

MAX_TASKS = 150          # A* ~20 ms/trasa × 2 trasy na zadanie ≈ 6 s — widok jest synchroniczny
MAX_HOURS = 24
# Rodzaj ruchu → (początek, koniec). „rack” = gniazdo regału — lokalizacja MUSI być w modelu,
# inaczej zadanie jest pomijane; „dock”/„station” = gniazdo, gdy kod jest w modelu, a poza
# nim najbliższy dok / stanowisko (strefa wydań, bufor przyjęć — spoza regałów modelu).
ENDPOINTS = {
    "putaway": ("dock", "rack"), "outbound": ("rack", "dock"), "picking": ("rack", "station"),
    "replenishment": ("rack", "rack"), "move": ("rack", "rack"),
}
FLOW_KIND = {"putaway": "inbound", "outbound": "outbound", "picking": "picking",
             "replenishment": "replenishment", "move": "transfer"}
ROW_FIELDS = ("confirmed_at", "resource", "user", "kind", "src_location", "dst_location", "material")


def resolve_moves(rows, locator, start, scale=1.0):
    """Wiersze WT (krotki ROW_FIELDS, rosnąco po potwierdzeniu) → (ruchy, pominięte).

    Ruch = (t [s od początku okna], agent, rodzaj, początek, koniec, sku); koniec ruchu to
    ("rack", rack, bay_idx, poziom) albo ("dock",) / ("station",)."""
    moves, skipped = [], 0
    for at, resource, user, kind, src, dst, material in rows:
        ends = []
        for code, role in zip((src, dst), ENDPOINTS.get(kind, ENDPOINTS["move"]), strict=True):
            found = locator.rack_and_bay(code) if code else None
            if found:
                rack, bay_idx, _col, level = found
                ends.append(("rack", rack, bay_idx, level))
            elif role == "rack":
                break
            else:
                ends.append((role,))
        if len(ends) < 2:
            skipped += 1
            continue
        t = max(0.0, (at - start).total_seconds() / max(1.0, scale))
        moves.append((t, resource or user or "Wózek", kind, ends[0], ends[1], material or ""))
    return moves, skipped


def default_start(batch):
    """Początek pierwszej pełnej godziny z zadaniami partii (czas lokalny)."""
    if batch.first_confirmed is None:
        return None
    return timezone.localtime(batch.first_confirmed).replace(minute=0, second=0, microsecond=0)


def parse_start(raw):
    """„2026-03-02T06:00” (input datetime-local, czas lokalny) → datetime ze strefą albo None."""
    try:
        dt = datetime.fromisoformat((raw or "").strip())
    except ValueError:
        return None
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def load_window(batch, start, hours=1.0, scale=1.0, limit=None):
    """Zadania partii potwierdzone w oknie [start, start + hours) — najwyżej `limit` (MAX_TASKS)."""
    limit = limit or MAX_TASKS
    end = start + timedelta(hours=hours)
    qs = batch.tasks.filter(confirmed_at__gte=start, confirmed_at__lt=end)
    rows = list(qs.order_by("confirmed_at", "id").values_list(*ROW_FIELDS)[:limit])
    return {"batch": batch, "start": start, "end": end, "scale": scale, "limit": limit,
            "rows": rows, "total": qs.count() if len(rows) >= limit else len(rows)}


def window_source(wt, animated, skipped):
    """Opis źródła wózków do `scene.source` (odtwarzacz pokazuje go pod animacją)."""
    fmt = "%Y-%m-%dT%H:%M"
    return {
        "forklifts": "ewm_tasks", "tasks_batch": wt["batch"].name,
        "window": {"from": timezone.localtime(wt["start"]).strftime(fmt),
                   "to": timezone.localtime(wt["end"]).strftime(fmt)},
        "time_scale": wt["scale"],
        "tasks": {"total": wt["total"], "loaded": len(wt["rows"]), "animated": animated,
                  "skipped_unmapped": skipped, "limit": wt["limit"],
                  "truncated": wt["total"] > len(wt["rows"])},
    }
