"""Limiter żądań dla publicznych formularzy (audyt SEC-014): reset hasła, linki z tokenem.

Okno stałe w cache (Redis na prod; IGNORE_EXCEPTIONS — awaria Redisa = limit nieegzekwowany,
jak w ui/zaria_ratelimit.py). Liczymy tylko POST — GET formularza nic nie wysyła."""
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse

from .middleware import client_ip

TOO_MANY = "Zbyt wiele prób w krótkim czasie. Spróbuj ponownie za kilka minut."


def hit(key, limit, window):
    """Zwiększ licznik `key`; True = w limicie, False = przekroczony."""
    full = f"rl:{key}"
    cache.add(full, 0, timeout=window)
    try:
        n = cache.incr(full)
    except ValueError:
        cache.set(full, 1, timeout=window)
        n = 1
    return (n or 0) <= limit


def request_ip(request):
    return request.META.get("REMOTE_ADDR", "") if settings.DEBUG else client_ip(request)


def _too_many():
    return HttpResponse(TOO_MANY, status=429, content_type="text/plain; charset=utf-8")


def password_reset_blocked(request):
    """Reset hasła: 5 POST/h per IP i 3/h per adres e-mail (bez zalewania cudzej skrzynki).
    Zwraca odpowiedź 429 albo None."""
    if request.method != "POST":
        return None
    email = (request.POST.get("email") or "").strip().lower()
    if hit(f"pwreset:ip:{request_ip(request)}", 5, 3600) and hit(f"pwreset:em:{email}", 3, 3600):
        return None
    return _too_many()


def post_rate_limit(scope, limit, window, by="ip"):
    """Dekorator widoku: limit POST-ów per IP (`by="ip"`) albo per token z URL (`by="token"`)."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method == "POST":
                ident = kwargs.get("token", "") if by == "token" else request_ip(request)
                if not hit(f"{scope}:{ident}", limit, window):
                    return _too_many()
            return view(request, *args, **kwargs)
        return wrapped
    return decorator
