"""NBP exchange rates — convert forwarder offers in foreign currencies to PLN for a
fair comparison. Rates come from the public NBP API (table A, mid rate) and are cached;
every call degrades gracefully (returns None) when the API is unreachable, so the quote
screens never break offline.
"""
import logging
from decimal import Decimal, InvalidOperation

from django.core.cache import cache

log = logging.getLogger(__name__)

NBP_URL = "https://api.nbp.pl/api/exchangerates/rates/a/{code}/?format=json"
CACHE_TTL = 6 * 3600            # 6h — NBP publishes once per business day
FAIL_TTL = 300                  # 5 min — po awarii NBP nie pytaj przy każdym renderze (B-011)
_HTTP_TIMEOUT = 4


def get_rate(currency):
    """PLN per 1 unit of `currency` (NBP table A mid rate). PLN → 1. Returns a Decimal,
    or None when the rate can't be obtained (unknown currency / network error)."""
    cur = (currency or "PLN").strip().upper()
    if not cur or cur == "PLN":
        return Decimal("1")
    key = f"nbp_rate_{cur}"
    try:
        cached = cache.get(key)
    except Exception:                            # cache backend (e.g. Redis) down → miss
        cached = None
    if cached is not None:
        try:
            return Decimal(cached)
        except InvalidOperation:
            pass
    try:
        if cache.get(f"{key}_fail"):             # niedawna awaria — nie czekaj znowu na timeout
            return None
    except Exception:
        pass
    try:
        import requests
        resp = requests.get(NBP_URL.format(code=cur.lower()), timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        mid = resp.json()["rates"][0]["mid"]
        try:
            cache.set(key, str(mid), CACHE_TTL)
        except Exception:                        # cache write is best-effort — a Redis
            pass                                 # outage must not discard a fetched rate
        return Decimal(str(mid))
    except Exception as exc:                     # network/HTTP/parse — never propagate
        log.warning("NBP rate fetch failed for %s: %s", cur, exc)
        try:
            cache.set(f"{key}_fail", 1, FAIL_TTL)
        except Exception:
            pass
        return None


def to_pln(amount, currency):
    """Convert `amount` in `currency` to PLN. Returns a Decimal, or None when no rate."""
    if amount is None:
        return None
    rate = get_rate(currency)
    if rate is None:
        return None
    try:
        return Decimal(str(amount)) * rate
    except (InvalidOperation, ValueError, TypeError):
        return None


def convert(amount, from_currency, to_currency):
    """Convert `amount` from one currency to another via the PLN mid rate. Returns a
    Decimal, or None when either rate is missing. Same currency → unchanged amount."""
    if amount is None:
        return None
    src = (from_currency or "PLN").strip().upper()
    dst = (to_currency or "PLN").strip().upper()
    if src == dst:
        try:
            return Decimal(str(amount))
        except (InvalidOperation, ValueError, TypeError):
            return None
    pln = to_pln(amount, src)
    dst_rate = get_rate(dst)
    if pln is None or not dst_rate:
        return None
    try:
        return pln / dst_rate
    except (InvalidOperation, ValueError, TypeError, ZeroDivisionError):
        return None
