"""JEDEN ranking kolejki kontroli HU (audyt BIZ-005, decyzja właściciela Q-42).

„Następna HU" (hu_control_next) i „Weź następną" (hu_take_next) biorą kolejkę z tego
samego miejsca: filtr = `_call_queue` (strefa/typ, wolne albo moje, bez aktywnego
snooze), kolejność = `order_queue` poniżej. Wcześniej „Następna HU" miała osobny
ranking SQL (priorytet → status → VIP → data → objętość) i oba wejścia podawały inne
palety. Czyste funkcje — bez widoków i requestu, testowane wprost.
"""
from ui.theme import shipment_type_for
from .sla import sla_sort_key

# Ranking (decyzja 2026-09-03, potwierdzona Q-42): VIP → PILNE → rekontrole →
# dokończenie rozgrzebanych rodzin → reszta. Mniejszy klucz = pilniejsza.
_RANK_VIP, _RANK_PILNE, _RANK_RECHECK, _RANK_FAMILY, _RANK_REST = 0, 1, 2, 3, 4

_RANK_BADGE = {_RANK_VIP: "VIP", _RANK_PILNE: "PILNE", _RANK_RECHECK: "rekontrola",
               _RANK_FAMILY: "dokończ klienta", _RANK_REST: ""}


def _customer_key(hu):
    """Klucz odbiorcy dla rodzin: KUNNR → nazwa odbiorcy → typ odbiorcy."""
    sh = hu.shipment if hu.shipment_id else None
    return _key(getattr(sh, "kunnr", ""), getattr(sh, "recipient_name", ""), hu.recipient_type)


def _key(kunnr, recipient_name, recipient_type):
    return (kunnr or "").strip() or (recipient_name or "").strip() or (recipient_type or "")


def rank_key(hu, families_in_progress, now=None):
    """(kategoria, termin_wysyłki, krótki_termin, created_at) — czysta funkcja rankingu.

    W obrębie kategorii pilniejsze jest to, co ma bliższy termin wysyłki (#18); HU bez
    terminu (inf) zachowują kolejność „najstarsze" po created_at. Kategoria nadal
    dominuje — VIP zawsze przed PILNE itd. Krótki termin ważności (adnotacja
    `a_shortdated` z `_call_queue`; brak = False) rozstrzyga dopiero remis kategorii
    i terminu wysyłki — przed kolejnością FIFO."""
    stype = shipment_type_for(hu)
    if stype == "VIP":
        cat = _RANK_VIP
    elif stype == "PILNE":
        cat = _RANK_PILNE
    elif hu.status == "to_recheck":
        cat = _RANK_RECHECK
    elif _customer_key(hu) in families_in_progress:
        cat = _RANK_FAMILY
    else:
        cat = _RANK_REST
    return (cat, sla_sort_key(hu, now), not getattr(hu, "a_shortdated", False), hu.created_at)


def families_in_progress(all_hus, started_keys=()):
    """Odbiorcy „rozgrzebani": część palet skontrolowana/w kontroli, część czeka —
    domykamy konsolidację, zanim weźmiemy nowego klienta. `started_keys` = klucze
    odbiorców z paletami już domkniętymi (ok/escaped), gdy all_hus ich nie zawiera."""
    started, waiting = set(started_keys), set()
    for h in all_hus:
        key = _customer_key(h)
        if not key:
            continue
        (started if h.status in ("ok", "in_control", "to_recheck", "escaped") else waiting).add(key)
    return started & waiting


def order_queue(hus, families, user_id, now=None):
    """Kolejność kolejki dla kontrolera: „dokończ swoją" — moje rezerwacje najpierw —
    potem `rank_key`. Wspólna dla „Następna HU" i „Weź następną"."""
    return sorted(hus, key=lambda h: (h.assigned_to_id != user_id, rank_key(h, families, now)))


def queue_badge(hu, families, user_id):
    """Etykieta „następnej" na pulpicie — spójna z kolejnością `order_queue`."""
    if hu.assigned_to_id == user_id:
        return "dokończ swoją"
    cat = rank_key(hu, families)[0]
    return f"dokończ: {_customer_key(hu)}" if cat == _RANK_FAMILY else _RANK_BADGE[cat]
