"""Nagrane odpowiedzi integracji zewnętrznych + atrapy tam, gdzie HTTP to zły poziom.

Każda integracja HTTP ma 6 scenariuszy (``SCENARIOS``): ``ok``, ``http_4xx``, ``http_5xx``,
``timeout``, ``empty`` (200 z pustym ciałem), ``bad_format`` (200, ale nie to, czego kod
oczekuje — strona HTML z proxy). Różnią się tylko trasą (URL) i treścią ``ok``.

    from testkit.integrations import integration, INTEGRATION_SETTINGS
    with self.settings(**INTEGRATION_SETTINGS), integration("nbp", "timeout") as http:
        assert get_rate("EUR") is None
        assert len(http.calls) == 1

Poza HTTP: ``fake_msal`` (Power BI — token), ``capture_sentry`` (fałszywy transport Sentry),
``FailingEmailBackend`` / ``SMTP_ERRORS`` (awarie SMTP; sukces = ``django.core.mail.outbox``).
"""
import smtplib
from contextlib import contextmanager
from unittest import mock

from django.core.mail.backends.base import BaseEmailBackend

from .fake_http import FakeHTTP, Reply

SCENARIOS = ("ok", "http_4xx", "http_5xx", "timeout", "empty", "bad_format")

# Ustawienia, przy których każda integracja uważa się za skonfigurowaną (adresy .test nie
# istnieją — gdyby coś ominęło FakeHTTP, zatrzyma to blokada sieci z testkit.net).
INTEGRATION_SETTINGS = {
    "GOOGLE_MAPS_API_KEY": "test-maps-key",
    "TWILIO_ACCOUNT_SID": "ACtest", "TWILIO_AUTH_TOKEN": "tok", "TWILIO_FROM": "+48500000000",
    "TEAMS_WEBHOOK_URL": "https://teams.example.test/workflows/hook",
    "N8N_EVENT_URL": "https://n8n.example.test/webhook/groove", "N8N_EVENT_SECRET": "s3cret",
    "MASTER_DATA_URL": "https://master-data.example.test/api/v2", "MASTER_DATA_API_KEY": "md-key",
    "POWERBI_ACCESS_TOKEN": "pbi-test-token", "POWERBI_WORKSPACE_ID": "ws", "POWERBI_DATASET_ID": "ds",
    "ZARIA_ANTHROPIC_API_KEY": "sk-ant-test", "ZARIA_OPENAI_API_KEY": "sk-test",
    "ZARIA_AZURE_OPENAI_API_KEY": "az-test",
    "ZARIA_AZURE_OPENAI_ENDPOINT": "https://zaria.openai.azure.example.test",
    "ZARIA_OLLAMA_BASE_URL": "http://ollama.example.test:11434/v1",
    "ZARIA_MAX_RETRIES": 0,          # SDK bez ponowień z backoffem — testy błędów są natychmiastowe
}

_ANTHROPIC_OK = {
    "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-haiku-4-5-20251001",
    "content": [{"type": "text", "text": "Na palecie EU zmieści się 48 kartonów."}],
    "stop_reason": "end_turn", "stop_sequence": None,
    "usage": {"input_tokens": 12, "output_tokens": 9},
}
_OPENAI_OK = {
    "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "test",
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": "Odpowiedź testowa."}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
}

# nazwa → (wzorzec URL, odpowiedź „ok”)
ROUTES = {
    "nbp": (r"api\.nbp\.pl/api/exchangerates", Reply(json={
        "table": "A", "currency": "euro", "code": "EUR",
        "rates": [{"no": "187/A/NBP/2026", "effectiveDate": "2026-09-25", "mid": 4.2512}]})),
    "google_maps": (r"maps\.googleapis\.com/maps/api/directions", Reply(json={
        "status": "OK", "routes": [{"legs": [{"distance": {"value": 512300, "text": "512 km"}}],
                                    "overview_polyline": {"points": "a~l~Fjk~uOwHJy@P"}}]})),
    "twilio": (r"api\.twilio\.com/2010-04-01/Accounts/.+/Messages\.json", Reply(
        status=201, json={"sid": "SMtest", "status": "queued"})),
    "teams": (r"teams\.example\.test", Reply(status=202, body=b"")),
    "n8n": (r"n8n\.example\.test", Reply(json={"ok": True})),
    "master_data": (r"master-data\.example\.test", Reply(json={
        "code": "REF100001", "name": "Rękawice nitrylowe", "ean": "5900000000001",
        "supplier_short": "", "unit_length_cm": 24.0, "unit_width_cm": 12.0,
        "unit_height_cm": 6.0, "stackable": True, "is_active": True})),
    "powerbi_dax": (r"api\.powerbi\.com/.+/executeQueries", Reply(json={"results": [{"tables": [{"rows": [
        {"Stock_oraz_DLT[Materiał]": "REF100001", "Stock_oraz_DLT[Partia]": "LOT0000001",
         "Stock_oraz_DLT[Ilość]": 120, "Stock_oraz_DLT[Miejsce]": "01-01-01"}]}]}]})),
    "oidc_token": (r"sso\.example\.test/.+/token", Reply(json={
        "access_token": "at", "id_token": "it", "token_type": "Bearer", "expires_in": 300})),
    "oidc_userinfo": (r"sso\.example\.test/.+/userinfo", Reply(json={
        "sub": "u-1", "preferred_username": "jan.kowalski", "email": "jan@example.test",
        "groups": ["Transport"]})),
    # MSAL już w konstruktorze aplikacji robi tenant discovery (i instance discovery przy aliasach)
    "entra_openid": (r"login\.microsoftonline\.com/[^/]+/v2\.0/\.well-known/openid-configuration", Reply(json={
        "issuer": "https://login.microsoftonline.com/{tenantid}/v2.0",
        "authorization_endpoint": "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize",
        "token_endpoint": "https://login.microsoftonline.com/organizations/oauth2/v2.0/token",
        "device_authorization_endpoint": "https://login.microsoftonline.com/organizations/oauth2/v2.0/devicecode"})),
    "entra_instance": (r"login\.microsoftonline\.com/common/discovery/instance", Reply(json={
        "tenant_discovery_endpoint": "https://login.microsoftonline.com/organizations/v2.0/.well-known/openid-configuration",
        "metadata": [{"preferred_network": "login.microsoftonline.com", "preferred_cache": "login.windows.net",
                      "aliases": ["login.microsoftonline.com", "login.windows.net", "login.microsoft.com",
                                  "sts.windows.net"]}]})),
    "anthropic": (r"api\.anthropic\.com/v1/messages", Reply(json=_ANTHROPIC_OK)),
    "openai": (r"api\.openai\.com/v1/chat/completions", Reply(json=_OPENAI_OK)),
    "azure_openai": (r"zaria\.openai\.azure\.example\.test", Reply(json=_OPENAI_OK)),
    "ollama": (r"ollama\.example\.test:11434", Reply(json=_OPENAI_OK)),
}

_BAD_FORMAT = Reply(status=200, body=b"<html><body>502 Bad Gateway (proxy)</body></html>",
                    headers={"Content-Type": "text/html"})


def reply_for(name, scenario):
    """Nagrana odpowiedź ``name`` w ``scenario`` (patrz SCENARIOS)."""
    ok = ROUTES[name][1]
    return {
        "ok": ok,
        "http_4xx": Reply(status=401, json={"error": "unauthorized", "message": "Nieprawidłowy klucz"}),
        "http_5xx": Reply(status=503, body=b"Service Unavailable"),
        "timeout": Reply(raises="timeout"),
        "empty": Reply(status=200, body=b""),
        "bad_format": _BAD_FORMAT,
    }[scenario]


@contextmanager
def integration(names, scenario="ok"):
    """FakeHTTP z trasą (lub trasami — krotka nazw) integracji w danym scenariuszu."""
    if scenario not in SCENARIOS:
        raise ValueError(f"Scenariusz {scenario!r} — dostępne: {SCENARIOS}")
    with FakeHTTP() as fake:
        for name in ((names,) if isinstance(names, str) else names):
            fake.add(ROUTES[name][0], reply_for(name, scenario))
        yield fake


# ── Power BI: MSAL (token) ──────────────────────────────────────────────────────────────────
MSAL_RESULTS = {
    "ok": {"access_token": "pbi-token", "token_type": "Bearer", "expires_in": 3600},
    "http_4xx": {"error": "invalid_client", "error_description": "AADSTS7000215: zły sekret"},
    "http_5xx": {"error": "temporarily_unavailable", "error_description": "AADSTS90033"},
    "empty": {},
}


@contextmanager
def fake_msal(scenario="ok", accounts=({"username": "powerbi@acme.test"},)):
    """Podmienia msal.ConfidentialClientApplication / PublicClientApplication.
    ``timeout`` → wyjątek requests z acquire_token_*; ``bad_format`` → słownik bez tokenu,
    a ``acquire_token_silent`` zwraca None (tak robi MSAL, gdy nie ma czego odświeżyć)."""
    import requests

    class _App:
        def __init__(self, *a, **kw):
            self.token_cache = kw.get("token_cache")

        def _result(self, *a, silent=False, **kw):
            if scenario == "timeout":
                raise requests.exceptions.ReadTimeout("testkit: login.microsoftonline.com timeout")
            if scenario == "bad_format":
                return None if silent else {"unexpected": "shape"}
            return dict(MSAL_RESULTS[scenario])

        acquire_token_for_client = acquire_token_by_device_flow = _result

        def acquire_token_silent(self, *a, **kw):
            return self._result(silent=True)

        def get_accounts(self, *a, **kw):
            return list(accounts)

    with mock.patch("msal.ConfidentialClientApplication", _App), \
            mock.patch("msal.PublicClientApplication", _App):
        yield _App


# ── Sentry: fałszywy transport ──────────────────────────────────────────────────────────────
@contextmanager
def capture_sentry():
    """Inicjuje sentry_sdk z transportem zapisującym koperty do listy (nic nie wychodzi)."""
    import sentry_sdk
    from sentry_sdk.transport import Transport

    envelopes = []

    class _ListTransport(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope)

    sentry_sdk.init(dsn="https://public@sentry.example.test/1", transport=_ListTransport,
                    default_integrations=False)
    try:
        yield envelopes
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.init()                # klient bez DSN = no-op, jak w testach bez SENTRY_DSN


# ── SMTP ────────────────────────────────────────────────────────────────────────────────────
SMTP_ERRORS = {
    "http_4xx": smtplib.SMTPRecipientsRefused({"x@example.test": (550, b"Mailbox unavailable")}),
    "http_5xx": smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
    "timeout": TimeoutError("SMTP timed out"),
    "auth": smtplib.SMTPAuthenticationError(535, b"Authentication failed"),
}


class FailingEmailBackend(BaseEmailBackend):
    """EMAIL_BACKEND, który rzuca ``FailingEmailBackend.error`` (ustaw przed użyciem)."""
    error = SMTP_ERRORS["http_5xx"]

    def send_messages(self, email_messages):
        if self.fail_silently:
            return 0
        raise self.error
