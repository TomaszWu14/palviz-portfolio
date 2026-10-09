"""Zamrażanie czasu (time-machine) w strefie Europe/Warsaw + daty graniczne.

    from testkit.clock import frozen, KEY_DATES
    with frozen("dst_jesien"):              # nazwa z KEY_DATES
        ...
    with frozen(warsaw(2026, 12, 31, 23, 59, 59), tick=True):
        ...

Django liczy lokalny czas z ``settings.TIME_ZONE`` (Europe/Warsaw), więc wynik nie zależy od
strefy maszyny (CI stoi w UTC, laptop w CET). ``tick=False`` (domyślnie) = zegar stoi.
"""
import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")


def warsaw(*args, fold=0):
    """Świadoma data w Europe/Warsaw. ``fold=1`` = druga (zimowa) z dwóch 02:xx w październiku."""
    return datetime(*args, tzinfo=WARSAW, fold=fold)


def last_sunday(year, month):
    """Ostatnia niedziela miesiąca — w UE wtedy przestawia się zegar (marzec / październik)."""
    last = calendar.monthrange(year, month)[1]
    d = date(year, month, last)
    return d - timedelta(days=(d.weekday() - 6) % 7)


def key_dates(year):
    """Punkty graniczne dla roku ``year`` (wszystkie świadome, Europe/Warsaw)."""
    spring, autumn = last_sunday(year, 3), last_sunday(year, 10)
    return {
        "przeszlosc": warsaw(year - 1, 6, 15, 10, 0),
        "dzis": warsaw(year, 9, 26, 10, 0),
        "przyszlosc": warsaw(year + 1, 6, 15, 10, 0),
        "koniec_roku": warsaw(year, 12, 31, 23, 59, 59),
        "nowy_rok": warsaw(year + 1, 1, 1, 0, 0, 1),
        # 02:00 CET → 03:00 CEST: 02:30 nie istnieje; 01:59 i 03:00 dzieli 1 minuta
        "dst_wiosna_przed": warsaw(spring.year, 3, spring.day, 1, 59),
        "dst_wiosna_po": warsaw(spring.year, 3, spring.day, 3, 0),
        # 03:00 CEST → 02:00 CET: 02:30 występuje dwa razy
        "dst_jesien": warsaw(autumn.year, 10, autumn.day, 2, 30),
        "dst_jesien_drugi_raz": warsaw(autumn.year, 10, autumn.day, 2, 30, fold=1),
    }


KEY_DATES = key_dates(2026)


def frozen(when, tick=False):
    """Kontekst/dekorator time-machine. ``when``: nazwa z KEY_DATES, datetime albo ISO."""
    import time_machine   # zależność testowa (requirements-test.txt)
    if isinstance(when, str):
        when = KEY_DATES.get(when) or datetime.fromisoformat(when)
    if isinstance(when, datetime) and when.tzinfo is None:
        when = when.replace(tzinfo=WARSAW)
    return time_machine.travel(when, tick=tick)
