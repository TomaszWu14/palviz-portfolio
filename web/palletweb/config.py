"""Typed, validated environment configuration (pydantic-settings).

Every environment variable the app reads is declared here with a type and a
default. Instantiating ``AppEnv()`` (done once at the top of ``settings.py``)
reads the process environment, coerces types, and runs the cross-field checks
below — so a missing/malformed variable makes the app fail **at startup with a
clear, aggregated message**, not deep inside a request.

Booleans accept the usual truthy/falsy spellings (true/false/1/0/yes/no/on/off,
case-insensitive). CSV-style vars stay raw strings here; ``settings.py`` splits
them (it already has the ``_csv_env`` helper semantics).
"""
from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(BaseSettings):
    # extra="ignore": the process env has hundreds of unrelated vars (PATH, HOME…);
    # only the fields declared below are read. case_sensitive matches the exact names.
    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")

    # ── Core Django ──────────────────────────────────────────────────────────
    DJANGO_SECRET_KEY: str = "dev-only-change-me"
    DJANGO_DEBUG: bool = False
    DJANGO_ALLOWED_HOSTS: str = "127.0.0.1,localhost"
    DJANGO_CSRF_TRUSTED_ORIGINS: str = "http://localhost"
    DJANGO_SSL_REDIRECT: bool = False
    DJANGO_LOG_LEVEL: str = "INFO"
    APP_NAME: str = "GROOVE"
    # Deployment variant — one codebase, one DB, but this instance serves only a subset of
    # modules. "" = full platform; "hu" = Kontrola HU + Wydruk HU only (warehouse-floor app).
    GROOVE_VARIANT: str = ""
    # Explicit module set this instance serves (overrides GROOVE_VARIANT). Comma-separated
    # module keys, e.g. "kontrola_hu,wydruk_hu". Empty = use GROOVE_VARIANT / full platform.
    GROOVE_MODULES: str = ""
    # Portal cross-links: where each module actually lives, so the hub can link to another
    # service/subdomain. "key=url" pairs, comma-separated, e.g.
    # "kontrola_hu=https://kontrola.groove.example.com/,paletyzacja=https://palet.groove.example.com/".
    GROOVE_MODULE_URLS: str = ""

    # ── SSO / OIDC (Keycloak) — opt-in; the app keeps its own login when disabled ────
    OIDC_ENABLED: bool = False
    # Keycloak realm URL, e.g. https://sso.groove.example.com/realms/groove — endpoints below
    # are derived from it (Keycloak convention) unless set explicitly.
    OIDC_ISSUER: str = ""
    OIDC_RP_CLIENT_ID: str = ""
    OIDC_RP_CLIENT_SECRET: str = ""
    OIDC_RP_SIGN_ALGO: str = "RS256"
    OIDC_OP_AUTHORIZATION_ENDPOINT: str = ""
    OIDC_OP_TOKEN_ENDPOINT: str = ""
    OIDC_OP_USER_ENDPOINT: str = ""
    OIDC_OP_JWKS_ENDPOINT: str = ""
    # Claim that carries the user's role/group names (map onto PalViz groups). A Keycloak
    # "group membership" mapper emits paths like "/Administratorzy" — both forms are accepted.
    OIDC_ROLES_CLAIM: str = "groups"

    # ── Master-data source (Faza 3) — where reference data comes from ────────────
    # Empty = read the local DB (monolith). Set to the master-data service API base, e.g.
    # https://core.groove.example.com/api/v2, to read products/customers/HU over HTTP instead.
    MASTER_DATA_URL: str = ""
    MASTER_DATA_API_KEY: str = ""

    # ── Database / storage ───────────────────────────────────────────────────
    DATABASE_URL: str = ""
    DB_PATH: str = ""
    DB_SSLMODE: str = ""
    DB_CONN_MAX_AGE: int = 600
    DB_STATEMENT_TIMEOUT_MS: int = 30000   # Postgres per-statement timeout (0 disables)
    MEDIA_ROOT: str = ""

    # ── Session / hardening ──────────────────────────────────────────────────
    SESSION_COOKIE_AGE: int = 28800        # 8h rolling idle timeout (shared scanners)
    CELERY_TASK_TIME_LIMIT: int = 600      # hard kill a runaway task after 10 min
    CELERY_TASK_SOFT_TIME_LIMIT: int = 540 # soft (SoftTimeLimitExceeded) after 9 min
    CSP_REPORT_ONLY: bool = True           # start CSP in report-only (won't break the UI)

    # ── Celery / cache ───────────────────────────────────────────────────────
    CELERY_BROKER_URL: str = "redis://127.0.0.1:6379/0"
    REDIS_CACHE_URL: str = ""
    CELERY_BEAT_DB_SCHEDULER: bool = False
    POWERBI_AUTO_PULL: bool = False
    POWERBI_PULL_INTERVAL_SEC: float = 3600.0
    DRIVER_REMINDER_INTERVAL_MIN: int = 30
    DRIVER_REMINDER_MAX: int = 5
    # Codzienny raport kontroli HU e-mailem do liderów (beat, 06:00 czasu lokalnego).
    HU_DAILY_REPORT: bool = False
    # ── Kontrola HU: wymuszenie fizycznego skanu (Warstwa A/B) + rezerwacje ──
    # Prefiks DataWedge dodawany przez skanery przed kodem (rozpoznanie skan vs klawiatura).
    HU_SCAN_TOKEN: str = ""
    # Twarde odrzucanie ręcznego wpisu HU (włącz po skonfigurowaniu DataWedge na urządzeniach).
    HU_SCAN_ENFORCE: bool = False
    # Wymuszenie zdjęcia palety przy wejściu do kontroli (dowód obecności) — TYLKO na
    # urządzeniach z aparatem (Zebra bez aparatu jedzie na samym skanie). Domyślnie OFF.
    HU_PHOTO_ENFORCE: bool = False
    # Tryb detekcji zdjęcia: NIE blokuje, tylko loguje pominięcie (kto liczył bez zdjęcia na
    # urządzeniu z aparatem) — okres obserwacji przed twardym HU_PHOTO_ENFORCE. Ignorowany gdy
    # ENFORCE=on (tam jest twarda bramka).
    HU_PHOTO_DETECT: bool = False
    # Retencja zdjęć kontroli HU (dni) — Celery-beat kasuje starsze wraz z plikiem. 0 = off.
    HU_PHOTO_RETAIN_DAYS: int = 30
    # RODO (audyt GDPR-002): dni przechowywania danych kierowców / logów logowania axes.
    # 0 = retencja wyłączona — okresy ustala IOD (audit/03-RODO-REJESTR.md).
    GDPR_DRIVER_RETAIN_DAYS: int = 0
    GDPR_ACCESSLOG_RETAIN_DAYS: int = 0
    # Auto-wygasanie nieruszonej rezerwacji HU (h) — wraca do puli auto-next.
    HU_RESERVED_MAX_HOURS: float = 8.0
    # Okno potwierdzenia wyjaśniania błędu (spec UX §3.4) — po nim pauza KPI nieważna.
    HU_INVESTIGATION_CONFIRM_MIN: int = 15
    # Auto-zwrot porzuconej kontroli (in_control bez liczenia przez N h) do kolejki. 0 = off.
    HU_INCONTROL_RELEASE_HOURS: float = 4.0
    # SLA / termin wysyłki (#18): godzina odcięcia w dniu dostawy wychodzącej i próg
    # „zaraz" (min) sterujący kolorem karty + alertem. Knob per magazyn/okno załadunku.
    HU_SLA_CUTOFF_HOUR: int = 12
    HU_SLA_SOON_MIN: int = 60
    # Potwierdzenie typu urządzenia po zalogowaniu (Zebra/telefon/tablet/PC) — routing
    # zadań ze zdjęciem (Zebra bez aparatu). Włącz na prod: DEVICE_CONFIRM_ENABLED=1.
    DEVICE_CONFIRM_ENABLED: bool = False
    # Nocny backup DB + media z aplikacji (beat, 02:30). Kopię z BACKUP_DIR trzeba
    # synchronizować off-host — backup na tym samym dysku to nie backup.
    BACKUP_ENABLED: bool = False
    BACKUP_DIR: str = "/backups"
    BACKUP_RETAIN_DAYS: int = 14

    # ── Observability / feature flags ────────────────────────────────────────
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str = "production"
    OTEL_ENABLED: str = ""
    STRUCTLOG_JSON: str = ""
    ENABLE_DEBUG_TOOLBAR: str = ""

    # ── Optional S3-compatible media storage ─────────────────────────────────
    AWS_STORAGE_BUCKET_NAME: str = ""
    AWS_S3_ENDPOINT_URL: str = ""
    AWS_S3_REGION_NAME: str = ""
    AWS_QUERYSTRING_AUTH: bool = True

    # ── Site / branding / quoting ────────────────────────────────────────────
    SITE_BASE_URL: str = ""
    SHIPMENT_ORIGIN_ADDRESS: str = "ul. Przykładowa 1, 00-001 Warszawa (magazyn Acme)"
    GOOGLE_MAPS_API_KEY: str = ""
    COMPANY_NAME: str = "ACME"
    PALLET_MAX_WEIGHT_KG: int = 1000
    DEFAULT_STOWAGE_EFFICIENCY_PCT: int = 80
    TWA_PACKAGE_NAME: str = ""
    TWA_SHA256_FINGERPRINT: str = ""

    # ── E-mail (SMTP) ────────────────────────────────────────────────────────
    EMAIL_BACKEND: str = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST: str = ""
    EMAIL_PORT: int = 587
    EMAIL_HOST_USER: str = ""
    EMAIL_HOST_PASSWORD: str = ""
    EMAIL_USE_TLS: bool = True
    DEFAULT_FROM_EMAIL: str = ""   # blank → settings.py derives "<APP_NAME> <no-reply@…>"
    WAREHOUSE_EMAIL: str = ""
    # Kontakt "napisz do administratora" pokazywany w UI (np. brak dostępu do ZARII).
    # Celowo bez domyślnej wartości w repo (PII) — ustaw w env na serwerze.
    ADMIN_CONTACT_EMAIL: str = ""

    # ── Power BI / SAP BW ────────────────────────────────────────────────────
    POWERBI_TENANT_ID: str = ""
    # Microsoft's public client (Azure CLI / Power BI Desktop) so the delegated
    # device-code login works without registering an app.
    POWERBI_CLIENT_ID: str = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
    POWERBI_CLIENT_SECRET: str = ""
    # Klient publiczny dla logowania delegowanego (device code). Ustaw tylko wtedy, gdy
    # tenant blokuje klienta Microsoftu i macie własną rejestrację „public client" —
    # POWERBI_CLIENT_ID bywa aplikacją poufną (service principal), a na niej device flow
    # nie działa (AADSTS7000218).
    POWERBI_PUBLIC_CLIENT_ID: str = ""
    POWERBI_WORKSPACE_ID: str = ""
    POWERBI_DATASET_ID: str = ""
    POWERBI_STOCK_TABLE: str = "Stock_oraz_DLT"
    POWERBI_ACCESS_TOKEN: str = ""
    POWERBI_CA_BUNDLE: str = ""
    POWERBI_SSL_VERIFY: bool = True

    # ── Kontrola HU: strefa GLS (konsolidacja kartony→paczki) ───────────────
    # Kody warehouse_type traktowane jako strefa GLS: po zaksięgowaniu palety kontroler
    # musi podać kartony w przygotowaniu + zrobione paczki (raport konsolidacji).
    GLS_ZONE_CODES: str = "GLS"

    # ── Moduł „Hierarchia opakowań" (PHV) ───────────────────────────────────
    # Adres, na który idą maile o zgłoszeniach błędów master daty z PHV.
    PHV_ISSUE_EMAIL: str = "zgloszenia@example.com"

    # Webhook kanału Teams (Workflows „when a webhook request is received") —
    # alerty reguł pakowania i przyszłe powiadomienia zespołowe. Puste = wyłączone.
    TEAMS_WEBHOOK_URL: str = ""

    # ── Moduł „Wysyłka UKRAINA" ─────────────────────────────────────────────
    # Wartości pola `warehouse_type` (SAP LGNUM) rozdzielające magazyny na ekranie
    # monitoringu partii. CSV — można podać kilka kodów na magazyn. Puste = monitor
    # grupuje stan pod „inne", dopóki kody nie zostaną ustawione.
    UKRAINE_WAREHOUSE_ACME: str = ""
    UKRAINE_WAREHOUSE_DLT: str = ""

    # ── Twilio SMS ───────────────────────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM: str = ""

    # ── External HU-scanner API ──────────────────────────────────────────────
    PALVIZ_API_TOKEN: str = ""
    # Dodatkowe ważne tokeny (CSV) — do bezprzestojowej ROTACJI: dodaj nowy tu, przełącz
    # skanery, potem usuń stary z PALVIZ_API_TOKEN. Puste = tylko PALVIZ_API_TOKEN.
    PALVIZ_API_TOKENS: str = ""

    # ── n8n (orkiestrator workflow) — puste = brak emisji zdarzeń (no-op) ────
    # URL webhooka n8n, do którego PALVIZ POST-uje zdarzenia (np. niezgodności
    # stocku). Puste = emit_event nic nie robi. Współdzielony sekret idzie w
    # nagłówku X-N8N-Secret, żeby n8n odrzucił obce POST-y.
    N8N_EVENT_URL: str = ""
    N8N_EVENT_SECRET: str = ""

    # ── ZARIA (wewnętrzny asystent AI) — puste = moduł nieaktywny ────────────
    # Klucze dostawców LLM — wyłącznie po stronie serwera, nigdy nie trafiają do
    # przeglądarki. Katalog modeli i cennik są edytowalne w panelu admina (DB);
    # tu żyją tylko sekrety.
    ZARIA_ANTHROPIC_API_KEY: str = ""
    ZARIA_OPENAI_API_KEY: str = ""
    ZARIA_AZURE_OPENAI_API_KEY: str = ""
    ZARIA_AZURE_OPENAI_ENDPOINT: str = ""
    # Lokalny LLM (Ollama, endpoint zgodny z OpenAI), np. http://192.168.1.50:11434/v1
    ZARIA_OLLAMA_BASE_URL: str = ""
    # Seeduje ZariaConfig.default_model przy pierwszym wdrożeniu (manage.py zaria_seed).
    ZARIA_DEFAULT_MODEL_KEY: str = ""
    # Fallbacki gdy wiersz ZariaConfig jeszcze nie istnieje — właściwe limity są
    # edytowalne w adminie (ZariaConfig.rate_limit_per_minute/_day).
    ZARIA_RATE_LIMIT_PER_MINUTE: int = 20
    ZARIA_RATE_LIMIT_PER_DAY: int = 200
    ZARIA_REQUEST_TIMEOUT_SEC: int = 60
    # Retry z backoffem dla 429/529/timeout (obsługiwane przez SDK dostawcy).
    ZARIA_MAX_RETRIES: int = 3
    # Domyślnie NIE zapisujemy treści rozmów z /api/chat (tylko ID + liczniki tokenów).
    ZARIA_LOG_CONVERSATIONS: bool = False

    @model_validator(mode="after")
    def _cross_field_checks(self) -> "AppEnv":
        errs: list[str] = []
        # Never boot production with the throwaway dev key.
        if not self.DJANGO_DEBUG and self.DJANGO_SECRET_KEY == "dev-only-change-me":
            errs.append("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false.")
        if self.POWERBI_AUTO_PULL and self.POWERBI_PULL_INTERVAL_SEC <= 0:
            errs.append("POWERBI_PULL_INTERVAL_SEC must be > 0 when POWERBI_AUTO_PULL is on.")
        if not (1 <= self.EMAIL_PORT <= 65535):
            errs.append(f"EMAIL_PORT must be 1–65535 (got {self.EMAIL_PORT}).")
        if not (0 <= self.DEFAULT_STOWAGE_EFFICIENCY_PCT <= 100):
            errs.append("DEFAULT_STOWAGE_EFFICIENCY_PCT must be 0–100.")
        if self.ZARIA_RATE_LIMIT_PER_MINUTE <= 0 or self.ZARIA_RATE_LIMIT_PER_DAY <= 0:
            errs.append("ZARIA_RATE_LIMIT_PER_MINUTE and ZARIA_RATE_LIMIT_PER_DAY must be > 0.")
        if errs:
            raise ValueError("Invalid configuration:\n  - " + "\n  - ".join(errs))
        return self


def load_env() -> AppEnv:
    """Instantiate and validate the env, re-raising as Django's ImproperlyConfigured
    so a bad config surfaces as a clean startup error (aggregating every problem at
    once) rather than a raw pydantic traceback."""
    try:
        return AppEnv()
    except Exception as exc:  # pydantic ValidationError or our ValueError
        from django.core.exceptions import ImproperlyConfigured
        raise ImproperlyConfigured(f"Environment configuration error:\n{exc}") from exc
