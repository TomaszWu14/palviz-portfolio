"""Uwierzytelnianie API v2 (nagłówek ``X-API-Key``): nazwani klienci z zakresami (SEC-017).

Konfiguracja to nadal DWA stringi w settings (``PALVIZ_API_TOKEN``, ``PALVIZ_API_TOKENS``) —
bogatszy format parsuje wyłącznie ten moduł, w 100% wstecznie zgodnie:

* ``PALVIZ_API_TOKEN`` — jeden token brany DOSŁOWNIE (bez parsowania) → klient ``legacy``
  z PEŁNYM dostępem (wszystkie zakresy). Dotychczasowe skanery działają bez zmian.
* ``PALVIZ_API_TOKENS`` — lista rozdzielona przecinkami; każdy wpis to:

  - zwykły token (dotychczasowy format) → klient ``legacy#N`` (N = pozycja na liście),
    pełny dostęp;
  - ``nazwa|zakres1+zakres2|token`` → klient ``nazwa`` TYLKO z podanymi zakresami
    (``*`` = wszystkie). Wpis jest „nazwany", gdy zawiera znak ``|``; dzielimy go od lewej
    ``split("|", 2)``, więc nazwa i zakresy nie mogą zawierać ``|``, a token (ostatnie
    pole) może zawierać dowolne znaki poza przecinkiem (``-``, ``_``, ``:``, ``+``, ``/``,
    ``=``, nawet ``|``). Nazwa: ``[A-Za-z0-9_.-]{1,64}``. Zakresy: patrz ``SCOPES``.
    Zwykły token zawierający ``|`` wpisz w ``PALVIZ_API_TOKEN`` (tam nic nie jest parsowane).

Błędny wpis nazwany (zła nazwa, pusty/nieznany zakres, brak tokenu, zła liczba pól) jest
POMIJANY z ostrzeżeniem w logu ``ui.api`` — raz na konfigurację, bez treści tokenu i bez
echa pól, które mogłyby nim być. Nigdy nie wywraca startu ani żądania. Ten sam token
w dwóch wpisach: obowiązuje pierwszy (kolejność: ``PALVIZ_API_TOKEN``, potem lista) +
ostrzeżenie. Brak jakiegokolwiek tokenu = API wyłączone (każde wywołanie → 401).

Porównanie klucza: ``hmac.compare_digest`` z KAŻDYM skonfigurowanym tokenem (bez
przerywania na pierwszym trafieniu) — czas nie zdradza ani treści, ani pozycji tokenu.
Wynik: nieznany klucz → 401; znany klient bez wymaganego zakresu → 403 (JSON ``detail``);
sukces → ``request.auth`` = ``ApiClient`` (nazwa + źródło, bez tokenu) + linia INFO w logu.
"""
import hmac
import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings
from ninja.errors import HttpError
from ninja.security import APIKeyHeader

log = logging.getLogger("ui.api")

# ── Zakresy (scopes) — jeden na grupę endpointów, patrz ``ui/api.py`` ─────────────────
SCOPE_READ_LOCATIONS = "read:locations"            # GET /locations, /locations/{code}
SCOPE_READ_PRODUCTS = "read:products"              # GET /products, /products/{code}
SCOPE_READ_CUSTOMERS = "read:customers"            # GET /customers, /customers/{id}
SCOPE_READ_HU = "read:handling-units"              # GET /handling-units, /handling-units/{code}
SCOPE_WRITE_TASKS = "write:tasks"                  # POST /tasks
SCOPES = frozenset({SCOPE_READ_LOCATIONS, SCOPE_READ_PRODUCTS, SCOPE_READ_CUSTOMERS,
                    SCOPE_READ_HU, SCOPE_WRITE_TASKS})
ALL_SCOPES = "*"            # pełny dostęp (klienci legacy); też wymóg domyślny endpointu
ANY_CLIENT = None           # wymóg „dowolny ważny klient" (np. /health)

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_FORMAT_HINT = "format: nazwa|zakres1+zakres2|token"


@dataclass(frozen=True)
class ApiClient:
    """Tożsamość klienta API przypięta do ``request.auth``. NIE przechowuje tokenu."""
    name: str
    scopes: frozenset
    source: str             # skąd wpis, np. "PALVIZ_API_TOKEN" / "PALVIZ_API_TOKENS[2]"

    @property
    def full_access(self):
        return ALL_SCOPES in self.scopes

    def allows(self, scope):
        return scope is ANY_CLIENT or self.full_access or scope in self.scopes

    def __str__(self):
        # AuthRateThrottle kluczuje po sha256(str(request.auth)): źródło jest unikalne per
        # wpis → osobny limit na każdy skonfigurowany token (jak dotąd), bez tokenu w kluczu.
        return f"{self.name}@{self.source}"


def _to_bytes(token):
    return token.encode("utf-8", "surrogatepass")


def _parse_named(entry, pos):
    """``nazwa|zakresy|token`` → (token, ApiClient) albo None (+ ostrzeżenie, bez tokenu)."""
    parts = entry.split("|", 2)
    if len(parts) != 3:
        return _skip(pos, "oczekiwano 3 pól rozdzielonych '|'")
    name, raw_scopes, token = parts[0].strip(), parts[1].strip(), parts[2].strip()
    if not _NAME_RE.match(name):
        return _skip(pos, "niepoprawna nazwa klienta (dozwolone: litery, cyfry, _ . -; do 64 zn.)")
    scopes = frozenset(s.strip() for s in raw_scopes.split("+") if s.strip())
    if not scopes:
        return _skip(pos, f"klient '{name}' bez zakresów")
    if not scopes <= (SCOPES | {ALL_SCOPES}):
        # Nie echujemy wartości — przy pomyłce kolejności pól mógłby tu trafić token.
        return _skip(pos, f"klient '{name}': nieznany zakres (dozwolone: "
                          f"{', '.join(sorted(SCOPES))}, *)")
    if not token:
        return _skip(pos, f"klient '{name}' bez tokenu")
    return token, ApiClient(name, scopes, f"PALVIZ_API_TOKENS[{pos}]")


def _skip(pos, reason):
    log.warning("API v2: pominięto wpis nr %d w PALVIZ_API_TOKENS — %s (%s).",
                pos, reason, _FORMAT_HINT)
    return None


@lru_cache(maxsize=8)
def _parse_clients(single, csv):
    """Rejestr ``((token_bytes, ApiClient), …)`` z surowych stringów settings.

    Cache po wartościach: ostrzeżenia o błędnych wpisach padają raz na konfigurację, a nie
    przy każdym żądaniu; zmiana settings (np. ``override_settings``) = nowy parse."""
    found = []
    if single:
        found.append((single, ApiClient("legacy", frozenset({ALL_SCOPES}), "PALVIZ_API_TOKEN")))
    for pos, raw in enumerate(csv.split(","), 1):
        entry = raw.strip()
        if not entry:
            continue
        if "|" in entry:
            parsed = _parse_named(entry, pos)
            if parsed:
                found.append(parsed)
        else:
            found.append((entry, ApiClient(f"legacy#{pos}", frozenset({ALL_SCOPES}),
                                           f"PALVIZ_API_TOKENS[{pos}]")))
    registry, seen = [], {}
    for token, client in found:
        key = _to_bytes(token)
        if key in seen:
            log.warning("API v2: token klienta '%s' (%s) powtarza token klienta '%s' (%s) — "
                        "pominięto, obowiązuje wcześniejszy wpis.",
                        client.name, client.source, seen[key].name, seen[key].source)
            continue
        seen[key] = client
        registry.append((key, client))
    return tuple(registry)


def api_clients():
    """Aktualny rejestr klientów (z bieżących settings). Pusty = API wyłączone."""
    return _parse_clients(str(getattr(settings, "PALVIZ_API_TOKEN", "") or ""),
                          str(getattr(settings, "PALVIZ_API_TOKENS", "") or ""))


def resolve_client(key):
    """Klient dla klucza albo None. Porównuje ze WSZYSTKIMI tokenami (stały czas)."""
    registry = api_clients()
    if not key or not registry:
        return None
    candidate = _to_bytes(key)
    match = None
    for token, client in registry:
        if hmac.compare_digest(candidate, token) and match is None:
            match = client
    return match


class ApiKey(APIKeyHeader):
    """Auth django-ninja z wymaganym zakresem. ``ApiKey()`` (domyślny dla całego API)
    wymaga pełnego dostępu — nowy endpoint bez jawnego zakresu jest więc zamknięty dla
    klientów z zakresami (fail-closed), a ``ApiKey(ANY_CLIENT)`` wpuszcza każdego klienta."""
    param_name = "X-API-Key"

    def __init__(self, scope=ALL_SCOPES):
        self.scope = scope
        super().__init__()

    def authenticate(self, request, key):
        client = resolve_client(key)
        if client is None:
            if key and api_clients():          # obecny, ale niepasujący klucz → widoczność brute-force
                log.warning("HU API: odrzucony klucz X-API-Key z %s ścieżka=%s",
                            request.META.get("REMOTE_ADDR", "?"), request.path)
            return None
        if not client.allows(self.scope):
            need = "pełny dostęp (*)" if self.scope == ALL_SCOPES else self.scope
            log.warning("API v2: klient=%s (%s) bez zakresu %s → 403 %s %s", client.name,
                        client.source, need, request.method, request.path)
            raise HttpError(403, f"Brak uprawnień: klient API „{client.name}” nie ma "
                                 f"zakresu „{need}” wymaganego przez ten endpoint.")
        log.info("API v2: klient=%s (%s) %s %s", client.name, client.source,
                 request.method, request.path)
        return client
