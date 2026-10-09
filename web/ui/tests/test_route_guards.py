"""Strażnik tras (TEST-002 / ACL-003): każda trasa ma dekorator dostępu albo jawny wpis.

Idzie po całym resolverze i dla każdego widoku przechodzi łańcuch ``__wrapped__``
szukając strażnika: ``role_required`` / ``module_required`` / ``user_passes_test``
(= ``login_required``), ``login_required`` przez ``method_decorator`` na CBV albo
``LoginRequiredMixin``. Pomijane: ``admin/`` (AdminSite pilnuje sam) i operacje
django-ninja (``auth=ApiKey`` na routerze). Trasa bez strażnika musi być na
PUBLIC_ROUTES z uzasadnieniem — nowy widok bez dekoratora wywala CI.
"""

from django.contrib.auth.decorators import login_required
from django.test import SimpleTestCase
from django.urls import URLResolver, get_resolver

GUARD_PREFIXES = ("role_required", "module_required", "user_passes_test")

# Klucz: url_name (albo ścieżka, gdy trasa nie ma nazwy) -> powód braku dekoratora.
PUBLIC_ROUTES = {
    "robots.txt": "statyczny robots.txt (lambda w palletweb/urls.py)",
    "csp_report": "raporty CSP z przeglądarki (report-uri) — bez sesji/CSRF; limit body/IP w widoku",
    "health": "healthcheck dla Coolify/smoke — bez sesji",
    "login": "ekran logowania",
    "logout": "wylogowanie",
    "password_reset": "reset hasła dla niezalogowanych (GuardedPasswordResetView)",
    "password_reset_done": "reset hasła — potwierdzenie wysyłki",
    "password_reset_confirm": "reset hasła — link z tokenem",
    "password_reset_complete": "reset hasła — koniec",
    "pwa_manifest": "manifest PWA — przeglądarka pobiera bez sesji",
    "pwa_manifest_phv": "manifest PWA skanera PHV",
    "pwa_sw": "service worker PWA",
    "assetlinks": "Digital Asset Links dla APK (TWA)",
    "^media/(?P<path>.*)$": "media_serve — kontrola dostępu w ciele widoku",
    "quote_response": "odpowiedź przewoźnika na wycenę — autoryzacja tokenem w URL",
    "wh_readiness_response": "potwierdzenie gotowości magazynu — token w URL",
    "driver_form": "formularz kierowcy — token w URL",
    "driver_confirm": "potwierdzenie kierowcy — token w URL",
    "notifications_poll": "polling powiadomień — sprawdza is_authenticated w ciele",
    "zaria_api_chat": "API czatu ZARIA — w ciele: logowanie (401), moduł 'zaria' i RODO (403, SEC-008)",
    # openapi-json/openapi-view: chronione docs_decorator=staff_member_required (ACL-002, #740).
    "api-root": "django-ninja default_home — tylko przekierowanie do docs (chronionych, #740)",
}


def _walk(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from _walk(p.url_patterns, prefix + str(p.pattern))
        else:
            yield prefix + str(p.pattern), p


def _cells(f):
    for c in getattr(f, "__closure__", None) or ():
        try:
            yield c.cell_contents
        except ValueError:
            pass


def _is_guarded(callback):
    cls = getattr(callback, "view_class", None)
    if cls is not None and any(k.__name__ == "LoginRequiredMixin" for k in cls.__mro__):
        return True
    f, depth = callback, 0
    while f is not None and depth < 20:
        depth += 1
        code = getattr(f, "__code__", None)
        qual = code.co_qualname if code else ""
        if qual.startswith(GUARD_PREFIXES):
            return True
        if qual.startswith("_multi_decorate"):  # method_decorator(...) na CBV
            for decs in _cells(f):
                if isinstance(decs, (list, tuple)) and any(
                    d is login_required or getattr(d, "__qualname__", "").startswith(GUARD_PREFIXES) for d in decs
                ):
                    return True
        f = getattr(f, "__wrapped__", None)
    return False


def _routes():
    for path, p in _walk(get_resolver().url_patterns):
        if path.startswith("admin/") or getattr(p.callback, "__module__", "").startswith("ninja."):
            continue
        yield path, p.name or path, p.callback


class RouteGuardTests(SimpleTestCase):
    def test_every_route_has_guard_or_public_entry(self):
        missing = sorted(
            f"{path}  (name={key})" for path, key, cb in _routes() if key not in PUBLIC_ROUTES and not _is_guarded(cb)
        )
        self.assertFalse(
            missing,
            "Trasy bez strażnika dostępu:\n  "
            + "\n  ".join(missing)
            + "\nDodaj dekorator z core/roles.py (role_required/_admin_only/…, "
            "module_required, login_required) lub wpis do PUBLIC_ROUTES z uzasadnieniem.",
        )

    def test_public_routes_exist(self):
        keys = {key for _, key, _ in _routes()}
        stale = sorted(set(PUBLIC_ROUTES) - keys)
        self.assertFalse(stale, f"PUBLIC_ROUTES zawiera nieistniejące trasy (usuń): {stale}")

    def test_password_change_is_login_guarded(self):
        # CBV z login_required przez method_decorator — detektor musi to widzieć.
        cb = next(cb for _, key, cb in _routes() if key == "password_change")
        self.assertTrue(_is_guarded(cb))
