"""Per-user rate limiting for ZARIA chat calls — fixed-window counters in the
shared Redis cache (django-redis, IGNORE_EXCEPTIONS=True at settings.py, so a
Redis outage degrades to "not enforced" rather than a 500, consistent with how
the rest of the app treats the cache as optional).

Deliberately not django-ninja's `AuthRateThrottle` (api.py) — that's shaped for
API-key auth on the external partner API; ZARIA is session-authenticated.
"""
from django.core.cache import cache
from django.utils import timezone


def check_and_increment(user, per_minute, per_day):
    """Increment this user's minute/day counters and return True iff both are
    still within the limits (the call that pushes over the limit is refused)."""
    now = timezone.now()
    minute_key = f"zaria:rl:m:{user.id}:{now.strftime('%Y%m%d%H%M')}"
    day_key = f"zaria:rl:d:{user.id}:{now.strftime('%Y%m%d')}"

    # `or 0`: przy niedostępnym Redisie cache (IGNORE_EXCEPTIONS=True) zwraca None, a
    # `None <= int` rzuca TypeError (500). None→0 = „nie egzekwuję" — degradacja jak w docstringu.
    minute_count = _incr(minute_key, timeout=60)
    if (minute_count or 0) > per_minute:
        return False   # odrzucone minutowo — NIE zużywaj dziennego limitu
    day_count = _incr(day_key, timeout=86400)
    return (day_count or 0) <= per_day


def _incr(key, timeout):
    cache.add(key, 0, timeout=timeout)   # atomowa inicjalizacja — bez wyścigu na zimnym kluczu
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=timeout)
        return 1
