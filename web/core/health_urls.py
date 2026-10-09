import os

from django.urls import path
from django.http import JsonResponse
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone


def health(request):
    """Health check: DB ping + timestamp + wersja (git SHA). Wersja pozwala smoke-checkowi
    po deployu poczekać, aż serwuje SIĘ NOWA wersja (nie stary kontener) — patrz deploy.yml.
    SHA bierzemy z GIT_SHA / SOURCE_COMMIT (build-arg z Coolify); brak = 'unknown'."""
    try:
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False

    # OBS-002: Redis (cache + broker Celery) też musi żyć. IGNORE_EXCEPTIONS połyka błędy,
    # więc sprawdzamy round-trip set→get, nie wyjątek. locmem (dev/CI) pomijamy.
    # Awaria cache = 200 + "degraded" (nie 503): Docker HEALTHCHECK nie restartuje kontenera,
    # który bez Redisa działa; zewnętrzny monitor łapie słowo "degraded". 503 tylko dla bazy.
    body, ok = {}, db_ok
    if "django_redis" in settings.CACHES["default"]["BACKEND"]:
        try:
            cache.set("health:ping", "1", 10)
            cache_ok = cache.get("health:ping") == "1"
        except Exception:
            cache_ok = False
        body["cache"] = "ok" if cache_ok else "error"
        ok = ok and cache_ok

    version = (os.environ.get("GIT_SHA") or os.environ.get("SOURCE_COMMIT") or "unknown")[:40]
    status = 200 if db_ok else 503
    return JsonResponse({
        "status": "ok" if ok else "degraded",
        "db": "ok" if db_ok else "error",
        "version": version,
        "time": timezone.now().isoformat(),
        **body,
    }, status=status)


urlpatterns = [
    path("", health, name="health"),
]
