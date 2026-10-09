"""Cross-cutting HTTP middleware: request correlation IDs + response security headers."""
import contextvars
import logging
import uuid

from django.conf import settings

# Request-ID dla logów stdlib (logging.getLogger) — structlog ma własne contextvars.
# "-" poza żądaniem (Celery, komendy manage.py).
request_id_var = contextvars.ContextVar("request_id", default="-")


class RequestIDLogFilter(logging.Filter):
    """Dopisuje `record.request_id` z bieżącego żądania (format handlera: {request_id})."""

    def filter(self, record):
        record.request_id = request_id_var.get()
        return True

# A conservative Content-Security-Policy. Shipped REPORT-ONLY by default (browsers
# report violations but don't block), so it can't break the UI while we learn what a
# future enforcing policy would need. 'unsafe-inline'/'unsafe-eval' are present because
# the templates still carry inline scripts/styles and plotly evaluates code; tighten
# with nonces once those are refactored. Flip CSP_REPORT_ONLY=false to enforce.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "report-uri /csp-report/"   # SEC-001: zbieranie naruszeń przed wymuszeniem (core/csp_report.py)
)
_PERMISSIONS_POLICY = "geolocation=(), microphone=(), payment=()"


class RequestIDMiddleware:
    """Attach a request/correlation ID to every request: reuse an inbound X-Request-ID
    (from the proxy) or mint one, bind it into structlog's contextvars so every log line
    in the request carries it, and echo it back on the response for end-to-end tracing."""

    def __init__(self, get_response):
        self.get_response = get_response
        import structlog
        self._structlog = structlog

    def __call__(self, request):
        rid = (request.headers.get("X-Request-ID") or uuid.uuid4().hex)[:64]
        request.request_id = rid
        self._structlog.contextvars.clear_contextvars()
        self._structlog.contextvars.bind_contextvars(request_id=rid)
        token = request_id_var.set(rid)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
            self._structlog.contextvars.clear_contextvars()
        response["X-Request-ID"] = rid
        return response


class PresenceMiddleware:
    """Presence per użytkownik: stempluje UserProfile.last_seen_at + last_device na
    żywych żądaniach, z throttlingiem 60 s przez cache (jedna aktualizacja/min/user,
    nie zapis na każdy request). Zasila panel lidera i ostrzeżenia o odbiorcy offline."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            from django.core.cache import cache
            key = f"presence:{user.pk}"
            if cache.get(key) is None:
                cache.set(key, 1, 60)
                from django.utils import timezone
                from ui.models import UserProfile
                # last_device WYŁĄCZNIE z jawnej deklaracji użytkownika (ekran wyboru
                # urządzenia po zalogowaniu). Brak wyboru = puste pole — celowo bez
                # zgadywania z User-Agenta: routing zadań „zrób zdjęcie" wymaga pewności
                # (Zebra bez aparatu), a domysł z UA dawał fałszywą.
                UserProfile.objects.filter(user=user).update(
                    last_seen_at=timezone.now(),
                    last_device=request.session.get("device_type", ""))
        return self.get_response(request)


class DeviceTypeMiddleware:
    """Potwierdzenie typu urządzenia raz na login (sesję): Zebra / telefon / tablet /
    komputer. Zasila routing zadań ze zdjęciem (Zebra bez aparatu) i panel lidera.
    Wyjątki jak przy wymuszonej zmianie hasła + API/sync/PWA (nie-HTML)."""

    _EXEMPT_PREFIXES = ("/urzadzenie/", "/haslo/", "/logout", "/login", "/static/",
                        "/media/", "/healthz", "/readyz", "/oidc/", "/api/",
                        "/control/sync/", "/sw.js", "/manifest", "/.well-known/",
                        "/phv/manifest")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        # Tylko nawigacyjne GET-y — przekierowanie POST-a gubiłoby dane formularza.
        # Flaga `device_type_pending` ustawiana przez ekran logowania (GrooveLoginView) —
        # nie przez signal — więc force_login w testach i sesje sprzed wdrożenia nie są
        # przekierowywane. SSO/OIDC na razie pomija ekran (UA-heurystyka zostaje).
        from django.conf import settings
        if (getattr(settings, "DEVICE_CONFIRM_ENABLED", False)
                and user is not None and user.is_authenticated and request.method == "GET"
                and request.session.get("device_type_pending")
                and not request.session.get("device_type")
                and not request.path.startswith(self._EXEMPT_PREFIXES)):
            from django.shortcuts import redirect
            from urllib.parse import quote
            return redirect(f"/urzadzenie/?next={quote(request.get_full_path())}")
        return self.get_response(request)


class PasswordChangeRequiredMiddleware:
    """Konto z `profile.must_change_password` (hasło nadane hurtem przy imporcie) trafia
    na ekran zmiany hasła i nie chodzi po aplikacji, dopóki go nie zmieni. Wyjątki:
    sama zmiana hasła, wylogowanie, logowanie, statyki i endpointy zdrowia."""

    _EXEMPT_PREFIXES = ("/haslo/", "/logout", "/login", "/static/", "/media/",
                        "/healthz", "/readyz", "/oidc/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated
                and not request.path.startswith(self._EXEMPT_PREFIXES)
                and getattr(getattr(user, "profile", None), "must_change_password", False)):
            from django.shortcuts import redirect
            return redirect("ui:password_change")
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """Add headers Django doesn't set by default: Permissions-Policy and a (report-only
    by default) Content-Security-Policy for defense-in-depth against injected scripts."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Permissions-Policy", _PERMISSIONS_POLICY)
        header = ("Content-Security-Policy-Report-Only"
                  if getattr(settings, "CSP_REPORT_ONLY", True)
                  else "Content-Security-Policy")
        response.setdefault(header, _CSP)
        return response


def client_ip(request):
    """IP klienta dla django-axes za jednym zaufanym proxy (Traefik/Coolify).

    Traefik DOPISUJE adres swojego rozmówcy na końcu X-Forwarded-For, więc przy jednym
    proxy tylko ostatni wpis jest wiarygodny (wcześniejsze klient może podrobić).
    Brak nagłówka → REMOTE_ADDR. Podpinane w settings tylko przy DEBUG=false."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    last = xff.split(",")[-1].strip()
    return last or request.META.get("REMOTE_ADDR")
