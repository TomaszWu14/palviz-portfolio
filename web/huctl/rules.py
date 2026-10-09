"""Reguły biznesowe Kontroli HU — JEDNO źródło definicji (audyt BIZ-007).

Wcześniej te same pojęcia liczono w kilku miejscach różnie: „krótki termin" jako 183 dni
w kolejce, miesiące klienta × 30,44 przy księgowaniu i 183/365 dni w alercie karty HU;
status `escaped` raz liczony, raz pomijany (hub/TV vs ekran statusu) i opisywany jako
„eskalacja"; przepustowość TV jako surowe próby, a KPI jako unikalne pozycje.
Czyste funkcje — bez widoków; widoki, model i szablony biorą definicje stąd.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

# ── Krótki termin ważności ────────────────────────────────────────────────────
# Decyzja właściciela: poniżej wymogu klienta (min. ważność w miesiącach), a gdy klient
# go nie ma — poniżej 6 miesięcy. Karta HU: czerwony = krótki termin, pomarańczowy < 12 mies.
DEFAULT_MIN_SHELF_MONTHS = 6
WARN_SHELF_MONTHS = 12
_DAYS_PER_MONTH = 30.44            # 6 mies. = 183 dni, 12 mies. = 365 dni


def min_shelf_months(customer):
    """Próg minimalnej ważności (miesiące): wymóg klienta albo domyślne 6."""
    months = getattr(customer, "min_shelf_life_months", None) if customer else None
    return int(months) if months else DEFAULT_MIN_SHELF_MONTHS


def shelf_cutoff(months, today):
    """Data graniczna: ważność PRZED nią = poniżej `months` miesięcy od `today`."""
    return today + timedelta(days=int(round(months * _DAYS_PER_MONTH)))


def is_short_dated(expiry, customer, today=None):
    """Czy data ważności jest poniżej progu klienta (albo 6 mies.). Brak daty = nie."""
    if not expiry:
        return False
    today = today or timezone.localdate()
    return expiry < shelf_cutoff(min_shelf_months(customer), today)


def expiry_alert(expiry, customer, today=None):
    """Kolor alertu daty na karcie HU: 'red' (krótki termin) / 'orange' (< 12 mies.) / ''."""
    if not expiry:
        return ""
    today = today or timezone.localdate()
    if is_short_dated(expiry, customer, today):
        return "red"
    if expiry < shelf_cutoff(WARN_SHELF_MONTHS, today):
        return "orange"
    return ""


def short_dated_q(today, customer_path="hu__shipment__customer"):
    """Ta sama reguła jako filtr SQL na pozycjach HU (kolejka: Exists po pozycjach).
    Progi klientów to mała, skończona lista wartości — jeden warunek na próg."""
    from ui.models import Customer
    months_field = f"{customer_path}__min_shelf_life_months"
    q = ((Q(**{f"{months_field}__isnull": True}) | Q(**{months_field: 0}))
         & Q(expiry__lt=shelf_cutoff(DEFAULT_MIN_SHELF_MONTHS, today)))
    for m in (Customer.objects.filter(min_shelf_life_months__gt=0).order_by()
              .values_list("min_shelf_life_months", flat=True).distinct()):
        q |= Q(**{months_field: m}, expiry__lt=shelf_cutoff(m, today))
    return Q(expiry__isnull=False) & q


# ── Grupy statusów HU ─────────────────────────────────────────────────────────
# Jedno źródło dla liczników hubu/TV (_status_counts) i ekranu statusu. `escaped`
# (wyjechało bez kontroli) to OSOBNA grupa — nigdy nie jest pracą otwartą ani zgodną.
STATUS_GROUPS = {
    "open": ("planned", "in_control", "to_recheck"),
    "ok": ("ok",),
    "escaped": ("escaped",),
}
STATUSES = tuple(s for group in STATUS_GROUPS.values() for s in group)


def status_label(status):
    """Etykieta statusu z choices modelu (np. escaped → „Wyjechało bez kontroli")."""
    from .models_hu import HandlingUnit
    return dict(HandlingUnit.STATUS).get(status, status)


def progress(counts):
    """(zrobione, zakres, %) ekranu statusu: zakres bez `escaped` (osobna grupa, poza
    otwartą pracą), zrobione = zakres minus jeszcze niezaczęte (planned)."""
    scope = sum(counts.get(s, 0) for s in STATUSES) - counts.get("escaped", 0)
    done = scope - counts.get("planned", 0)
    return done, scope, (round(done * 100 / scope) if scope else 0)


# ── Przepustowość ─────────────────────────────────────────────────────────────
def throughput_positions(attempts):
    """Przepustowość = UNIKALNE pozycje (hu, pozycja), jak kpi_stats — edycje i rekontrole
    tej samej pozycji nie nabijają licznika (surowe próby to audyt, nie przepustowość)."""
    return attempts.order_by().values("hu_id", "item_id").distinct().count()
