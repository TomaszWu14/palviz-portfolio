"""SLA / termin wysyłki HU (#18) — czyste, testowalne funkcje bez zależności od widoków.

Termin = data dostawy wychodzącej (`Shipment.outbound_delivery_date`, z feedu SAP)
o godzinie odcięcia `HU_SLA_CUTOFF_HOUR` (domyślnie 12:00, czas lokalny). Brak daty →
brak SLA (poziom 'none'). Progi są kalibrowalne env-em, bo „okno załadunku" bywa różne
per magazyn — to knob, nie stała.
"""
import datetime
from collections import namedtuple

from django.conf import settings
from django.utils import timezone

# minutes: minuty do terminu (ujemne = po terminie); label: gotowy tekst PL.
SLA = namedtuple("SLA", "deadline minutes level label")

_LEVELS = ("none", "ok", "soon", "overdue")


def _cutoff_hour():
    return int(getattr(settings, "HU_SLA_CUTOFF_HOUR", 12) or 0)


def _soon_min():
    return int(getattr(settings, "HU_SLA_SOON_MIN", 60) or 0)


def deadline_for(hu):
    """Aware datetime terminu wysyłki HU albo None (brak daty dostawy)."""
    sh = hu.shipment if getattr(hu, "shipment_id", None) else None
    d = getattr(sh, "outbound_delivery_date", None)
    if not d:
        return None
    naive = datetime.datetime(d.year, d.month, d.day, _cutoff_hour(), 0)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _fmt(mins):
    """'2 h 5 min' / '37 min' / '3 h' z wartości bezwzględnej minut."""
    h, m = divmod(abs(int(mins)), 60)
    parts = []
    if h:
        parts.append(f"{h} h")
    if m or not h:
        parts.append(f"{m} min")
    return " ".join(parts)


def sla_for(hu, now=None):
    """SLA(deadline, minutes, level, label) — czysta funkcja rankingu/prezentacji."""
    dl = deadline_for(hu)
    if dl is None:
        return SLA(None, None, "none", "")
    now = now or timezone.now()
    mins = int((dl - now).total_seconds() // 60)
    if mins < 0:
        level, label = "overdue", f"po terminie {_fmt(mins)}"
    elif mins <= _soon_min():
        level, label = "soon", f"zostało {_fmt(mins)}"
    else:
        level, label = "ok", f"zostało {_fmt(mins)}"
    return SLA(dl, mins, level, label)


def sla_sort_key(hu, now=None):
    """Mniejszy = pilniejszy; HU bez terminu lądują na końcu (inf)."""
    mins = sla_for(hu, now).minutes
    return mins if mins is not None else float("inf")
