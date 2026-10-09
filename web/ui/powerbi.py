"""Pull warehouse-stock data straight from the Power BI / SAP BW dataset via the REST
`executeQueries` (DAX) endpoint, so the stock import doesn't need a manual file upload.

Inspired by the desktop PowerBI export script (msal + requests), adapted for an
unattended server: auth is a service principal (client credentials) rather than an
interactive browser popup. For ad-hoc use a pre-fetched bearer token can be supplied
via POWERBI_ACCESS_TOKEN. All config comes from settings (POWERBI_*), themselves from
environment variables, so no secrets live in the repo.

`fetch_table()` returns (header, rows) in the same shape as core._read_table — lowercased
column names + a list of value-lists — so the existing HU importer consumes it unchanged.
"""
import logging

from django.conf import settings

log = logging.getLogger(__name__)

PBI_BASE_URL = "https://api.powerbi.com/v1.0/myorg"
# .default = all statically-consented app permissions (works for client credentials).
SCOPES = ["https://analysis.windows.net/powerbi/api/.default"]
# Microsoft's well-known public client (Azure CLI / Power BI Desktop): pre-registered,
# needs no admin consent — used for the delegated (device-code) user login when service
# principals are blocked in the tenant.
PUBLIC_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"


def _cfg(name, default=""):
    return getattr(settings, name, default) or default


def has_dataset_config():
    """True when the target workspace+dataset are set — enough to start a delegated
    login from the web UI (the auth that login provides is the only missing piece)."""
    return bool(_cfg("POWERBI_WORKSPACE_ID") and _cfg("POWERBI_DATASET_ID"))


def is_configured():
    """True when we have a workspace+dataset and some way to authenticate (a pasted
    token, a service principal, or a connected delegated/device-code session)."""
    has_ws = bool(_cfg("POWERBI_WORKSPACE_ID") and _cfg("POWERBI_DATASET_ID"))
    has_auth = (bool(_cfg("POWERBI_ACCESS_TOKEN"))
                or bool(_cfg("POWERBI_TENANT_ID") and _cfg("POWERBI_CLIENT_ID")
                        and _cfg("POWERBI_CLIENT_SECRET"))
                or has_delegated_session())
    return bool(has_ws and has_auth)


def _ssl_verify():
    """Corporate proxies (Zscaler/Umbrella/F5) re-sign TLS; point at the root CA bundle,
    or disable verification for dev only."""
    bundle = _cfg("POWERBI_CA_BUNDLE")
    if bundle:
        return bundle
    return getattr(settings, "POWERBI_SSL_VERIFY", True) is not False


def _delegated_client_id():
    """Client ID for the DELEGATED (device-code) login.

    POWERBI_CLIENT_ID is shared with the service-principal path, so when a client secret
    is configured it holds a *confidential* app registration — running a device flow on it
    fails in AAD (AADSTS7000218: the app is not a public client). In that case fall back to
    Microsoft's well-known public client, which is what the device flow needs.
    An explicit POWERBI_PUBLIC_CLIENT_ID always wins."""
    explicit = _cfg("POWERBI_PUBLIC_CLIENT_ID")
    if explicit:
        return explicit
    if _cfg("POWERBI_CLIENT_SECRET"):
        return PUBLIC_CLIENT_ID
    return _cfg("POWERBI_CLIENT_ID", PUBLIC_CLIENT_ID)


def _public_app(cache=None):
    """MSAL public client (delegated user login) on Microsoft's well-known client ID."""
    import msal
    tenant = _cfg("POWERBI_TENANT_ID") or "organizations"
    return msal.PublicClientApplication(
        _delegated_client_id(),
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=cache)


_ENC_PREFIX = "enc1:"


def _fernet():
    """Klucz Fernet z DJANGO_SECRET_KEY (audyt SEC-011: refresh token nie leży w bazie ani
    w backupach jawnym tekstem). Zmiana SECRET_KEY = ponowne `powerbi_connect`."""
    import base64
    import hashlib
    from cryptography.fernet import Fernet
    from django.conf import settings
    digest = hashlib.sha256(b"groove-powerbi-token:" + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt_cache(raw):
    return _ENC_PREFIX + _fernet().encrypt(raw.encode()).decode() if raw else ""


def _decrypt_cache(stored):
    """Odszyfruj; wartość bez prefiksu = zapis sprzed szyfrowania (zaszyfruje się przy zapisie)."""
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(stored[len(_ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        log.warning("Cache Power BI nie do odszyfrowania (zmieniony SECRET_KEY?) — potrzebne powerbi_connect.")
        return ""


def _load_cache():
    """Deserialize the persisted MSAL token cache from the DB (empty if none)."""
    import msal
    from .models import PowerBIToken
    cache = msal.SerializableTokenCache()
    row = PowerBIToken.objects.first()
    if row and (raw := _decrypt_cache(row.cache)):
        cache.deserialize(raw)
    return cache


def _save_cache(cache, app=None):
    """Persist the MSAL token cache back to the DB when it changed (refresh rotation)."""
    if not cache.has_state_changed:
        return
    from .models import PowerBIToken
    row = PowerBIToken.load()
    row.cache = _encrypt_cache(cache.serialize())
    if app is not None:
        accts = app.get_accounts()
        if accts:
            row.account = accts[0].get("username", "") or row.account
    row.save()


def has_delegated_session():
    """True when a delegated (device-code) account is connected in the persisted cache."""
    try:
        from .models import PowerBIToken
        row = PowerBIToken.objects.first()
        if not (row and row.cache):
            return False
        return bool(_public_app(_load_cache()).get_accounts())
    except Exception:
        return False


def connected_account():
    # Tolerancyjnie: przed migracją tabeli jeszcze nie ma, a to nie powód, żeby
    # wywracać panel czy diagnostykę.
    try:
        from .models import PowerBIToken
        row = PowerBIToken.objects.first()
        return row.account if row else ""
    except Exception:
        return ""


def record_error(exc):
    """Persist the last auth/fetch failure so the UI (and a leader) can see *why* the
    refresh doesn't work, instead of a silently swallowed exception in a worker thread."""
    log.error("Power BI: błąd integracji", exc_info=exc)   # INT-006: szczegół w logu, nie w UI
    try:
        from django.utils import timezone
        from .models import PowerBIToken
        row = PowerBIToken.load()
        row.last_error = str(exc)[:2000]
        row.last_error_at = timezone.now()
        row.save(update_fields=["last_error", "last_error_at", "updated_at"])
    except Exception:                      # diagnostyka nie może wywrócić głównej ścieżki
        pass


def clear_error():
    try:
        from .models import PowerBIToken
        row = PowerBIToken.objects.first()
        if row and row.last_error:
            row.last_error = ""
            row.last_error_at = None
            row.save(update_fields=["last_error", "last_error_at", "updated_at"])
    except Exception:
        log.warning("Nie udało się wyczyścić last_error tokenu Power BI", exc_info=True)


def last_error():
    try:
        from .models import PowerBIToken
        row = PowerBIToken.objects.first()
        return (row.last_error, row.last_error_at) if row else ("", None)
    except Exception:
        return ("", None)


def diagnose():
    """Ordered list of (nazwa, ok, szczegóły) checks — powers `manage.py powerbi_diag`.
    Runs the real token acquisition and a real (1-row) query, so it reports the actual
    failure rather than guessing from config."""
    checks = []

    def add(name, ok, detail=""):
        checks.append((name, ok, detail))

    ws, ds = _cfg("POWERBI_WORKSPACE_ID"), _cfg("POWERBI_DATASET_ID")
    add("POWERBI_WORKSPACE_ID", bool(ws), _mask(ws) or "brak")
    add("POWERBI_DATASET_ID", bool(ds), _mask(ds) or "brak")
    # Tenant jest opcjonalny dla logowania delegowanego — nie oznaczaj braku jako błędu.
    add("POWERBI_TENANT_ID", True,
        _mask(_cfg("POWERBI_TENANT_ID")) or "brak (device flow użyje 'organizations')")
    add("POWERBI_STOCK_TABLE", True, _cfg("POWERBI_STOCK_TABLE", "Stock_oraz_DLT"))

    if _cfg("POWERBI_ACCESS_TOKEN"):
        auth = "wklejony token (POWERBI_ACCESS_TOKEN)"
    elif _cfg("POWERBI_CLIENT_SECRET"):
        auth = f"service principal ({_mask(_cfg('POWERBI_CLIENT_ID'))}) + fallback na sesję delegowaną"
    else:
        auth = f"logowanie delegowane (device code), client_id={_mask(_delegated_client_id())}"
    add("Ścieżka uwierzytelnienia", True, auth)
    add("Sesja delegowana połączona", has_delegated_session(), connected_account() or "brak konta")

    err, err_at = last_error()
    if err:
        add("Ostatni zapisany błąd", False, f"{err_at:%Y-%m-%d %H:%M} — {err}" if err_at else err)

    try:
        token = get_access_token()
        add("Pobranie tokenu", bool(token), f"token OK ({len(token)} znaków)")
    except Exception as exc:
        add("Pobranie tokenu", False, str(exc))
        return checks                       # bez tokenu dalsze testy nie mają sensu

    try:
        header, rows = fetch_table()
        add("Zapytanie DAX (executeQueries)", bool(header),
            f"{len(rows)} wierszy, kolumny: {', '.join(header[:8])}" if header
            else "0 wierszy — sprawdź POWERBI_STOCK_TABLE")
    except Exception as exc:
        add("Zapytanie DAX (executeQueries)", False, str(exc))
    return checks


def _mask(value):
    """Show enough of an ID to recognise it, never the whole secret-ish string."""
    value = value or ""
    return f"{value[:6]}…{value[-4:]}" if len(value) > 12 else value


def connect_device_flow(prompt=print):
    """Run the device-code login: prints a URL + code for the user to authenticate on
    any device (works with MFA), blocks until done, then persists the token cache.
    Returns the connected username. Used by `manage.py powerbi_connect`."""
    cache = _load_cache()
    app = _public_app(cache)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(flow.get("error_description") or "Nie udało się rozpocząć logowania (device flow).")
    prompt(flow["message"])                       # human-readable: open URL, enter code
    result = app.acquire_token_by_device_flow(flow)   # blocks until the user authenticates
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description") or "Logowanie do Power BI nie powiodło się.")
    _save_cache(cache, app)
    accts = app.get_accounts()
    return accts[0].get("username", "") if accts else ""


def import_cache(raw):
    """Wgraj serializowany cache MSAL (z lokalnego interaktywnego logowania,
    tools/powerbi_login_local.py) do PowerBIToken — obejście CA, gdy device-code jest
    zablokowany. Waliduje, że cache zawiera konto z refresh tokenem, zapisuje i zwraca
    (username, refreshed_ok, detail). Nie rzuca na braku sieci — sam zapis się uda,
    a silent-refresh zweryfikuje osobno."""
    import msal
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Pusty cache — nic nie wgrano.")
    cache = msal.SerializableTokenCache()
    try:
        cache.deserialize(raw)
    except Exception as exc:                       # niepoprawny JSON / nie-MSAL
        raise ValueError(f"To nie wygląda na cache MSAL: {exc}")
    app = _public_app(cache)
    accts = app.get_accounts()
    if not accts:
        raise ValueError("Cache nie zawiera zalogowanego konta (brak refresh tokenu). "
                         "Zaloguj się ponownie przez tools/powerbi_login_local.py.")
    from .models import PowerBIToken
    row = PowerBIToken.load()
    row.cache = _encrypt_cache(raw)
    row.account = accts[0].get("username", "") or row.account
    row.save()
    clear_error()
    # Potwierdź, że silent-refresh działa (nie blokuj zapisu, gdy brak sieci w konsoli).
    try:
        result = app.acquire_token_silent(SCOPES, account=accts[0])
        _save_cache(cache, app)                    # utrwal ewentualną rotację refresh tokenu
        ok = bool(result and "access_token" in result)
        detail = "token odświeżony silently" if ok else "cache wgrany, ale silent-refresh nie zwrócił tokenu"
    except Exception as exc:
        ok, detail = False, f"cache wgrany; silent-refresh nieudany: {exc}"
    return row.account, ok, detail


def start_device_flow():
    """Begin a delegated device-code login and return the MSAL flow dict (carries
    user_code, verification_uri, message, expires_in). Used by the web 'Połącz' button:
    the code is shown in the browser and complete_device_flow() finishes it. Raises on
    failure to start."""
    app = _public_app(_load_cache())
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(flow.get("error_description") or "Nie udało się rozpocząć logowania (device flow).")
    return flow


def complete_device_flow(flow):
    """Block until the user authenticates the given device-code flow (started by
    start_device_flow), then persist the token cache. Returns the connected username.
    Meant to run in a background thread so the web request returns the code immediately."""
    cache = _load_cache()
    app = _public_app(cache)
    result = app.acquire_token_by_device_flow(flow)   # blocks until authenticated / expiry
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description") or "Logowanie do Power BI nie powiodło się.")
    _save_cache(cache, app)
    accts = app.get_accounts()
    return accts[0].get("username", "") if accts else ""


def get_access_token():
    """Bearer token for the Power BI REST API. Order: a supplied token → service
    principal (if a secret is set) → delegated device-code session (refreshed silently
    from the persisted cache)."""
    tok = _cfg("POWERBI_ACCESS_TOKEN")
    if tok:
        return tok
    import msal  # imported lazily so the dependency is only needed for the Power BI path
    sp_error = ""
    # Service principal (client credentials) when a secret is configured.
    if _cfg("POWERBI_CLIENT_SECRET"):
        app = msal.ConfidentialClientApplication(
            _cfg("POWERBI_CLIENT_ID"),
            authority=f"https://login.microsoftonline.com/{_cfg('POWERBI_TENANT_ID')}",
            client_credential=_cfg("POWERBI_CLIENT_SECRET"))
        result = app.acquire_token_for_client(scopes=SCOPES)
        if "access_token" in result:
            return result["access_token"]
        # Nie przerywaj — w tenantach z zablokowanymi service principalami ta ścieżka
        # zawsze pada, a obok może być poprawnie połączona sesja delegowana. Błąd SP
        # dołączamy dopiero wtedy, gdy i ona nie zadziała (inaczej odświeżanie stocku
        # padało mimo działającego logowania użytkownika).
        sp_error = (result.get("error_description") or result.get("error")
                    or "logowanie service principal nie powiodło się")
    # Delegated (device-code) login: refresh silently from the persisted cache.
    cache = _load_cache()
    app = _public_app(cache)
    accounts = app.get_accounts()
    if not accounts:
        raise RuntimeError(_with_sp(
            "Power BI nie jest połączony — kliknij „Połącz z Power BI” na zakładce Stock "
            "magazynowy albo uruchom „python manage.py powerbi_connect”.", sp_error))
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache(cache, app)
    if not result or "access_token" not in result:
        raise RuntimeError(_with_sp(
            "Sesja Power BI wygasła — połącz ponownie („Połącz z Power BI” / "
            "„python manage.py powerbi_connect”).", sp_error))
    return result["access_token"]


def _with_sp(msg, sp_error):
    return f"{msg} [service principal: {sp_error}]" if sp_error else msg


def fetch_table(table_name=None):
    """Run `EVALUATE '<table>'` against the dataset and return (header, rows).

    header = lowercased column names (the 'Table'[Column] prefix stripped); rows = list
    of value-lists in the same column order — matching core._read_table's output."""
    import requests  # lazy import — only the Power BI path needs it
    table = table_name or _cfg("POWERBI_STOCK_TABLE", "Stock_oraz_DLT")
    token = get_access_token()
    url = (f"{PBI_BASE_URL}/groups/{_cfg('POWERBI_WORKSPACE_ID')}"
           f"/datasets/{_cfg('POWERBI_DATASET_ID')}/executeQueries")
    # Escape single quotes in the table name so a legitimate apostrophe doesn't break DAX.
    payload = {"queries": [{"query": f"EVALUATE '{table.replace(chr(39), chr(39) * 2)}'"}],
               "serializerSettings": {"includeNulls": True}}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(url, json=payload, headers=headers, timeout=120, verify=_ssl_verify())
    resp.raise_for_status()
    # A 200 with an unexpected shape (or an empty results list) must not IndexError.
    tables = ((resp.json().get("results") or [{}])[0] or {}).get("tables", [])
    raw = tables[0].get("rows", []) if tables else []
    if not raw:
        return [], []
    # Stable column order from the first row; strip 'Table'[Col] prefix + lowercase so
    # the HU column matcher (which expects lowercased headers) works unchanged.
    cols = list(raw[0].keys())
    header = [c.split("[")[-1].rstrip("]").strip().lower() for c in cols]
    rows = [[r.get(c) for c in cols] for r in raw]
    return header, rows
