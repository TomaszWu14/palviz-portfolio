from pathlib import Path

from .config import load_env

BASE_DIR = Path(__file__).resolve().parent.parent  # web/

# Validate the whole environment ONCE, up front. A missing/malformed variable
# raises ImproperlyConfigured here (aggregating every problem) instead of blowing
# up mid-request. Everything below reads typed values off `env`.
env = load_env()

SECRET_KEY = env.DJANGO_SECRET_KEY
DEBUG = env.DJANGO_DEBUG


def _csv(value):
    return [h.strip() for h in (value or "").split(",") if h.strip()]


ALLOWED_HOSTS = _csv(env.DJANGO_ALLOWED_HOSTS)
# The container HEALTHCHECK curls http://127.0.0.1:8000/health/ (Host: 127.0.0.1), and
# Django's host validation returns 400 when only the public domain is allowed — which
# fails the deploy healthcheck and makes Coolify roll the release back. Loopback probes
# are internal-only, so always allow them regardless of DJANGO_ALLOWED_HOSTS.
for _loopback in ("127.0.0.1", "localhost"):
    if _loopback not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_loopback)
CSRF_TRUSTED_ORIGINS = _csv(env.DJANGO_CSRF_TRUSTED_ORIGINS)
# Friendly "session expired" page instead of the bare 403 on a stale CSRF token.
CSRF_FAILURE_VIEW = "ui.views.misc.csrf_failure"

# Product / brand name shown across the UI (title bar, nav brand, login, e-mails).
# Env-driven so the whole app can be rebranded without touching templates.
APP_NAME = env.APP_NAME
# Deployment variant: "" = full platform; "hu" = a Kontrola HU + Wydruk HU-only instance
# (same code + DB, deployed separately under its own name/host). See platform_modules.
GROOVE_VARIANT = (env.GROOVE_VARIANT or "").strip().lower()
# Explicit served-module set (overrides the named variant) and portal cross-links.
GROOVE_MODULES = env.GROOVE_MODULES or ""
GROOVE_MODULE_URLS = env.GROOVE_MODULE_URLS or ""
# Master-data source: empty = local DB; else the master-data service API base (Faza 3).
MASTER_DATA_URL = (env.MASTER_DATA_URL or "").rstrip("/")
MASTER_DATA_API_KEY = env.MASTER_DATA_API_KEY or ""

# Kontrola HU: kody warehouse_type strefy GLS (wymóg rozliczenia kartony→paczki).
GLS_ZONE_CODES = _csv(env.GLS_ZONE_CODES)

# Moduł „Hierarchia opakowań" (PHV): adresat maili o zgłoszeniach master daty.
PHV_ISSUE_EMAIL = env.PHV_ISSUE_EMAIL

# Webhook kanału Teams (Workflows) — powiadomienia zespołowe; puste = wyłączone.
TEAMS_WEBHOOK_URL = env.TEAMS_WEBHOOK_URL

# Moduł „Wysyłka UKRAINA": kody warehouse_type dla naszego magazynu ACME i zewnętrznego DLT.
UKRAINE_WAREHOUSE_ACME = _csv(env.UKRAINE_WAREHOUSE_ACME)
UKRAINE_WAREHOUSE_DLT = _csv(env.UKRAINE_WAREHOUSE_DLT)

if not DEBUG:
    # (The dev-key refusal now lives in config.AppEnv, checked at startup.)
    # Production security hardening.
    SECURE_SSL_REDIRECT = env.DJANGO_SSL_REDIRECT  # opt-in (proxy loops)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "simple_history",
    "django_celery_results",
    "django_celery_beat",
    "axes",
    "django_htmx",
    "django_filters",
    "django_tables2",
    "core",   # shared kernel (bez modeli): role, middleware, context processors, hub, health
    "ui",
    "wh3d",
    "huctl",
    "transport",
]

MIDDLEWARE = [
    "core.middleware.RequestIDMiddleware",          # correlation ID → structlog + response
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.SecurityHeadersMiddleware",    # Permissions-Policy + CSP (report-only)
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",   # i18n: po Session, przed Common
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    # Po Authentication — stempluje last_seen_at/last_device (throttling 60 s).
    "core.middleware.PresenceMiddleware",
    # Po Presence — raz na sesję potwierdzenie typu urządzenia (Zebra/telefon/tablet/PC);
    # jawna deklaracja zasila last_device i routing zadań ze zdjęciem.
    "core.middleware.DeviceTypeMiddleware",
    # Po Authentication (potrzebuje request.user) — konto z hasłem nadanym hurtem
    # musi je zmienić, zanim wejdzie gdziekolwiek indziej.
    "core.middleware.PasswordChangeRequiredMiddleware",
]

# django-tables2: use Bootstrap-free default template that inherits our CSS.
DJANGO_TABLES2_TEMPLATE = "django_tables2/table.html"

ROOT_URLCONF = "palletweb.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.user_roles",
                "core.context_processors.branding",
                "core.context_processors.section_theme",
            ],
        },
    },
]

WSGI_APPLICATION = "palletweb.wsgi.application"


def _db_from_url(url):
    """Parse a DATABASE_URL (postgresql://USER:PASS@HOST:PORT/NAME?sslmode=...) into a
    Django DATABASES['default'] dict. Returns None when no URL is set, so the caller
    falls back to SQLite (keeps local dev / CI working without Postgres)."""
    url = (url or "").strip()
    if not url:
        return None
    import urllib.parse as _up
    u = _up.urlparse(url)
    query = dict(_up.parse_qsl(u.query))
    options = {}
    sslmode = query.get("sslmode") or env.DB_SSLMODE
    if sslmode:                                   # managed providers usually need SSL
        options["sslmode"] = sslmode
    if env.DB_STATEMENT_TIMEOUT_MS > 0:           # bound slow queries at the DB, not just gunicorn
        options["options"] = f"-c statement_timeout={env.DB_STATEMENT_TIMEOUT_MS}"
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _up.unquote(u.path.lstrip("/")),
        "USER": _up.unquote(u.username or ""),
        "PASSWORD": _up.unquote(u.password or ""),
        "HOST": u.hostname or "",
        "PORT": str(u.port or ""),
        "CONN_MAX_AGE": env.DB_CONN_MAX_AGE,
        "OPTIONS": options,
    }


# Postgres when DATABASE_URL is set (e.g. managed cloud), otherwise SQLite.
DATABASES = {
    "default": _db_from_url(env.DATABASE_URL) or {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": Path(env.DB_PATH or (BASE_DIR / "db.sqlite3")),
        # WAL + IMMEDIATE + timeout: współbieżne zapisy (gthread) czekają zamiast "database is locked".
        "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE",
                    "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;"},
    }
}

LANGUAGE_CODE = "pl"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True

# Dwujęzyczność PL/EN (przełącznik zapisuje cookie — patrz ui.views.misc.set_prefs).
from django.utils.translation import gettext_lazy as _
LANGUAGES = [("pl", _("Polski")), ("en", _("English"))]
LOCALE_PATHS = [BASE_DIR / "ui" / "locale"]

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded media (HU quality-issue photos) — stored on the server disk.
MEDIA_URL = "/media/"
# Env-driven so Docker can point uploaded media (HU photos) at a mounted volume.
MEDIA_ROOT = env.MEDIA_ROOT or str(BASE_DIR / "media")
# Django 5.1+ ignoruje STATICFILES_STORAGE — działa tylko STORAGES (audyt PERF-001: prod serwował
# statyki bez hashy i kompresji). Gałąź S3 niżej nadpisuje "default".
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "palletweb.storage.GrooveStaticStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── Authentication backends (required by django-axes) ────────────────────────
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# ── Login / logout flow (branded PalViz login page, not the raw Django admin) ──
LOGIN_URL          = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

# Polityka haseł — egzekwowana wszędzie, gdzie ustawiane jest hasło (panel admina
# woła validate_password, Django admin i reset hasła używają jej automatycznie).
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── SSO / OIDC (Keycloak) — opt-in; disabled = normal username/password login ──
OIDC_ENABLED = bool(env.OIDC_ENABLED)
if OIDC_ENABLED:
    try:
        import mozilla_django_oidc  # noqa: F401
    except Exception:               # package missing → fall back to local login, don't crash
        OIDC_ENABLED = False
if OIDC_ENABLED:
    INSTALLED_APPS = INSTALLED_APPS + ["mozilla_django_oidc"]
    AUTHENTICATION_BACKENDS = [
        "axes.backends.AxesStandaloneBackend",
        "ui.oidc.GrooveOIDCBackend",              # SSO — maps IdP roles → PalViz groups
        "django.contrib.auth.backends.ModelBackend",  # keep local login (superuser/fallback)
    ]
    OIDC_RP_CLIENT_ID = env.OIDC_RP_CLIENT_ID
    OIDC_RP_CLIENT_SECRET = env.OIDC_RP_CLIENT_SECRET
    OIDC_RP_SIGN_ALGO = env.OIDC_RP_SIGN_ALGO or "RS256"
    OIDC_RP_SCOPES = "openid email profile"
    OIDC_ROLES_CLAIM = env.OIDC_ROLES_CLAIM or "groups"
    OIDC_STORE_ID_TOKEN = True
    # Keycloak endpoints: explicit override, else derived from the realm issuer URL.
    _iss = (env.OIDC_ISSUER or "").rstrip("/")
    _kc = (lambda explicit, suffix: explicit or (f"{_iss}/protocol/openid-connect/{suffix}" if _iss else ""))
    OIDC_OP_AUTHORIZATION_ENDPOINT = _kc(env.OIDC_OP_AUTHORIZATION_ENDPOINT, "auth")
    OIDC_OP_TOKEN_ENDPOINT         = _kc(env.OIDC_OP_TOKEN_ENDPOINT, "token")
    OIDC_OP_USER_ENDPOINT          = _kc(env.OIDC_OP_USER_ENDPOINT, "userinfo")
    OIDC_OP_JWKS_ENDPOINT          = _kc(env.OIDC_OP_JWKS_ENDPOINT, "certs")

# Rolling idle-session timeout — important for shared warehouse/scanner devices. Each
# request refreshes the cookie, so an active user stays logged in; an idle session
# expires after SESSION_COOKIE_AGE (default 8h) instead of Django's fixed 2 weeks.
SESSION_COOKIE_AGE = env.SESSION_COOKIE_AGE
SESSION_SAVE_EVERY_REQUEST = True

# Content-Security-Policy mode (see ui.middleware). Report-only by default.
CSP_REPORT_ONLY = env.CSP_REPORT_ONLY

# ── django-axes (brute-force protection) ─────────────────────────────────────
AXES_FAILURE_LIMIT        = 5          # block after 5 failed attempts
# B-009: blokada per para login+IP. Samo IP (domyślne axes) za proxy Coolify/NAT hali = wszyscy
# mają ten sam adres, więc 5 pomyłek jednej osoby blokowało logowanie całej firmie na godzinę.
AXES_LOCKOUT_PARAMETERS   = [["username", "ip_address"]]
AXES_COOLOFF_TIME         = 1          # hours until automatic unblock
AXES_RESET_ON_SUCCESS     = True       # reset counter on successful login
AXES_LOCKOUT_TEMPLATE     = "ui/axes_lockout.html"
AXES_VERBOSE              = False
# SEC-002: za Traefikiem REMOTE_ADDR = IP proxy → klient z X-Forwarded-For (1 zaufane proxy).
AXES_CLIENT_IP_CALLABLE   = None if DEBUG else "core.middleware.client_ip"

# ── Sentry (error tracking) ───────────────────────────────────────────────────
_SENTRY_DSN = env.SENTRY_DSN
# GDPR-003: bez zmiennych lokalnych w ramkach stosu (mogą nieść dane osobowe).
SENTRY_INIT_OPTS = {"traces_sample_rate": 0.2, "send_default_pii": False, "include_local_variables": False}
if _SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration
    import os as _os
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        **SENTRY_INIT_OPTS,       # 20% traces, bez PII i zmiennych lokalnych
        environment=env.SENTRY_ENVIRONMENT,
        # Release = git SHA (build-arg SOURCE_COMMIT → GIT_SHA, patrz Dockerfile) — błędy
        # w Sentry grupują się per wdrożona wersja i widać, który deploy je wprowadził.
        release=(_os.environ.get("GIT_SHA") or None),
    )

# ── Celery ───────────────────────────────────────────────────────────────────
CELERY_BROKER_URL = env.CELERY_BROKER_URL
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "default"
CELERY_RESULT_EXTENDED = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
# In DEBUG mode tasks run synchronously (no worker needed)
CELERY_TASK_ALWAYS_EAGER = DEBUG
CELERY_TASK_EAGER_PROPAGATES = True
# Kill runaway tasks (e.g. a full recalculation that hangs) so a worker can't be tied up
# forever. Soft limit raises SoftTimeLimitExceeded first (lets the task clean up).
CELERY_TASK_SOFT_TIME_LIMIT = env.CELERY_TASK_SOFT_TIME_LIMIT
CELERY_TASK_TIME_LIMIT = env.CELERY_TASK_TIME_LIMIT

# Automatic driver pickup-confirmation reminders (Celery beat). The beat job runs
# often; each driver is reminded at most every INTERVAL minutes, up to MAX times,
# until they confirm. Requires a beat process: `celery -A palletweb beat`.
CELERY_BEAT_SCHEDULE = {
    "driver-pickup-reminders": {"task": "ui.send_driver_reminders", "schedule": 300.0},  # every 5 min
    "generate-recurring-tasks": {"task": "ui.generate_recurring_tasks", "schedule": 21600.0},  # every 6h
    # Retencja ZARIA (F3): raz na dobę usuwa niezapisane wątki starsze niż retention_days
    # (no-op gdy retention_days=0). Ostrzeżenie w UI 7 dni wcześniej.
    "zaria-retention": {"task": "ui.zaria_purge_old_conversations", "schedule": 86400.0},  # daily
    # Retencja zdjęć kontroli HU: raz na dobę kasuje foto starsze niż HU_PHOTO_RETAIN_DAYS.
    "hu-photo-retention": {"task": "ui.purge_old_hu_control_photos", "schedule": 86400.0},  # daily
    "gdpr-retention": {"task": "ui.gdpr_retention", "schedule": 86400.0},  # daily; no-op przy 0 dni
    # Snapshot KPI transportu: dashboard renderuje z cache zamiast liczyć O(N) per request.
    "transport-kpi-refresh": {"task": "ui.refresh_transport_kpi", "schedule": 3600.0},  # hourly
}

# Optional scheduled Power BI / SAP BW stock refresh. Off by default (manual pull from the
# Data Center). Enable with POWERBI_AUTO_PULL=true; tune cadence with POWERBI_PULL_INTERVAL_SEC.
POWERBI_AUTO_PULL = env.POWERBI_AUTO_PULL
POWERBI_PULL_INTERVAL_SEC = env.POWERBI_PULL_INTERVAL_SEC  # hourly by default
if POWERBI_AUTO_PULL:
    CELERY_BEAT_SCHEDULE["powerbi-stock-pull"] = {
        "task": "ui.scheduled_powerbi_stock_pull", "schedule": POWERBI_PULL_INTERVAL_SEC}

# Crontab-owe wpisy beat liczone w czasie LOKALNYM (bez tego Celery używa UTC).
CELERY_TIMEZONE = TIME_ZONE

# Codzienny raport kontroli HU do liderów (06:00) — włączany HU_DAILY_REPORT=true.
HU_DAILY_REPORT = env.HU_DAILY_REPORT
if HU_DAILY_REPORT:
    from celery.schedules import crontab
    CELERY_BEAT_SCHEDULE["hu-daily-report"] = {
        "task": "ui.send_hu_daily_report", "schedule": crontab(hour=6, minute=0)}

# Kontrola HU: wymuszenie fizycznego skanu HU + auto-wygasanie rezerwacji.
HU_SCAN_TOKEN = env.HU_SCAN_TOKEN
HU_SCAN_ENFORCE = env.HU_SCAN_ENFORCE
HU_INVESTIGATION_CONFIRM_MIN = env.HU_INVESTIGATION_CONFIRM_MIN
HU_PHOTO_ENFORCE = env.HU_PHOTO_ENFORCE
HU_PHOTO_DETECT = env.HU_PHOTO_DETECT
HU_PHOTO_RETAIN_DAYS = env.HU_PHOTO_RETAIN_DAYS
GDPR_DRIVER_RETAIN_DAYS = env.GDPR_DRIVER_RETAIN_DAYS
GDPR_ACCESSLOG_RETAIN_DAYS = env.GDPR_ACCESSLOG_RETAIN_DAYS
HU_RESERVED_MAX_HOURS = env.HU_RESERVED_MAX_HOURS
HU_INCONTROL_RELEASE_HOURS = env.HU_INCONTROL_RELEASE_HOURS
HU_SLA_CUTOFF_HOUR = env.HU_SLA_CUTOFF_HOUR
HU_SLA_SOON_MIN = env.HU_SLA_SOON_MIN
DEVICE_CONFIRM_ENABLED = env.DEVICE_CONFIRM_ENABLED

# Nocny backup DB + media (02:30) — włączany BACKUP_ENABLED=true.
BACKUP_ENABLED = env.BACKUP_ENABLED
BACKUP_DIR = env.BACKUP_DIR
BACKUP_RETAIN_DAYS = env.BACKUP_RETAIN_DAYS
if BACKUP_ENABLED:
    from celery.schedules import crontab
    CELERY_BEAT_SCHEDULE["nightly-backup"] = {
        "task": "ui.run_backup", "schedule": crontab(hour=2, minute=30)}
DRIVER_REMINDER_INTERVAL_MIN = env.DRIVER_REMINDER_INTERVAL_MIN
DRIVER_REMINDER_MAX = env.DRIVER_REMINDER_MAX

# Periodic schedules editable in the admin (django-celery-beat) when a beat process runs
# with the DatabaseScheduler. Falls back to the static CELERY_BEAT_SCHEDULE otherwise.
if env.CELERY_BEAT_DB_SCHEDULER:
    CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# ─── Cache (django-redis on the Celery Redis; locmem fallback so dev/CI need no Redis) ──
_REDIS_CACHE_URL = env.REDIS_CACHE_URL or (
    CELERY_BROKER_URL if CELERY_BROKER_URL.startswith("redis://") and not DEBUG else "")
if _REDIS_CACHE_URL:
    CACHES = {"default": {"BACKEND": "django_redis.cache.RedisCache",
                          "LOCATION": _REDIS_CACHE_URL,
                          # Treat the cache as OPTIONAL: when Redis is unreachable, cache
                          # ops return the default (None) instead of raising, so a Redis
                          # outage degrades gracefully (e.g. live NBP lookups, no caching)
                          # instead of 500-ing every page that touches the cache. The
                          # swallowed errors are still logged so the outage stays visible.
                          "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient",
                                      "IGNORE_EXCEPTIONS": True}}}
    DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS = True
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# ─── Optional object storage for media (HU photos) — opt-in via S3-compatible env ──────
if env.AWS_STORAGE_BUCKET_NAME:
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3.S3Storage"},
        "staticfiles": {"BACKEND": "palletweb.storage.GrooveStaticStorage"},
    }
    AWS_STORAGE_BUCKET_NAME = env.AWS_STORAGE_BUCKET_NAME
    AWS_S3_ENDPOINT_URL = env.AWS_S3_ENDPOINT_URL or None
    AWS_S3_REGION_NAME = env.AWS_S3_REGION_NAME or None
    AWS_QUERYSTRING_AUTH = env.AWS_QUERYSTRING_AUTH

# ─── Optional OpenTelemetry tracing (off unless OTEL_ENABLED=1; needs a collector) ──────
if env.OTEL_ENABLED == "1":
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        _tp = TracerProvider()
        _tp.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(_tp)
    except Exception:
        pass

# ─── Structured logging (structlog) — console by default, JSON with STRUCTLOG_JSON=1 ───
import structlog as _structlog
_structlog.configure(
    processors=[
        _structlog.contextvars.merge_contextvars,
        _structlog.processors.add_log_level,
        _structlog.processors.TimeStamper(fmt="iso"),
        (_structlog.processors.JSONRenderer() if env.STRUCTLOG_JSON == "1"
         else _structlog.dev.ConsoleRenderer()),
    ],
    cache_logger_on_first_use=True,
)

# ─── Django stdlib logging — surface request errors (incl. 500 tracebacks) on the
#     console so they appear in container logs even when DEBUG is off. Django's default
#     config routes django.request errors to mail_admins ONLY when DEBUG=False (its
#     console handler is gated by require_debug_true), so production 500s were invisible
#     in the Coolify/gunicorn logs. Level is tunable via DJANGO_LOG_LEVEL. ───────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "[{asctime}] {levelname} [{request_id}] {name}: {message}",
                    "style": "{"},
    },
    # request_id z RequestIDMiddleware (X-Request-ID) w każdej linii logu stdlib.
    "filters": {"request_id": {"()": "core.middleware.RequestIDLogFilter"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose",
                    "filters": ["request_id"]},
    },
    "root": {"handlers": ["console"], "level": env.DJANGO_LOG_LEVEL},
    "loggers": {
        # Always emit the full traceback for unhandled view exceptions (HTTP 500).
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}

# ─── django-extensions — dev aid (in requirements-dev); enable only when installed so
#     CI/production (runtime requirements only) don't fail app loading on a missing module ──
try:
    import django_extensions  # noqa: F401
    INSTALLED_APPS += ["django_extensions"]
except Exception:
    pass

# ─── django-debug-toolbar — dev only, strictly opt-in (never active in CI/tests) ───────
if DEBUG and env.ENABLE_DEBUG_TOOLBAR == "1":
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")
    INTERNAL_IPS = ["127.0.0.1"]

# Absolute base URL for links built outside a request (e.g. SMS in Celery tasks).
SITE_BASE_URL = env.SITE_BASE_URL

# Origin (warehouse) address for shipment route / quote requests. Editable per
# request; this is just the default dispatch point.
SHIPMENT_ORIGIN_ADDRESS = env.SHIPMENT_ORIGIN_ADDRESS

# Optional Google Maps key — when set, the quote screen shows the real road km in
# the header and a static map with the drawn route. Without it, a no-key route
# embed + directions link are used instead.
GOOGLE_MAPS_API_KEY = env.GOOGLE_MAPS_API_KEY

# Company name shown to forwarders on the quote-response page.
COMPANY_NAME = env.COMPANY_NAME

# Palletization / quoting calibration — tune from real shipment outcomes (env-overridable)
# instead of hardcoding. PALLET_MAX_WEIGHT_KG: per-pallet weight cap driving pallet count;
# DEFAULT_STOWAGE_EFFICIENCY_PCT: realistic usable share of pallet cube for mixed hand-stacking.
PALLET_MAX_WEIGHT_KG = env.PALLET_MAX_WEIGHT_KG
DEFAULT_STOWAGE_EFFICIENCY_PCT = env.DEFAULT_STOWAGE_EFFICIENCY_PCT

# TWA (Android APK wrapper) Digital Asset Links — set both to enable assetlinks.json.
TWA_PACKAGE_NAME = env.TWA_PACKAGE_NAME
TWA_SHA256_FINGERPRINT = env.TWA_SHA256_FINGERPRINT  # comma-separated

# Outgoing e-mail (SMTP) — used to send the styled HTML quote requests. Without a
# host configured, the send fails gracefully and the UI offers the mailto fallback.
EMAIL_BACKEND = env.EMAIL_BACKEND
EMAIL_HOST = env.EMAIL_HOST
EMAIL_PORT = env.EMAIL_PORT
EMAIL_HOST_USER = env.EMAIL_HOST_USER
EMAIL_HOST_PASSWORD = env.EMAIL_HOST_PASSWORD
EMAIL_USE_TLS = env.EMAIL_USE_TLS
EMAIL_TIMEOUT = 20  # INT-001: sekundy — zawieszony SMTP nie blokuje workera w nieskończoność
DEFAULT_FROM_EMAIL = env.DEFAULT_FROM_EMAIL or f"{APP_NAME} <no-reply@monty.local>"
ADMIN_CONTACT_EMAIL = env.ADMIN_CONTACT_EMAIL

# Django puts the machine's FQDN into every Message-ID header. A malformed local
# hostname (e.g. "T_tab..home" — empty DNS label) makes that idna-encode step raise
# and silently breaks EVERY e-mail send. Pin the cached name to "localhost" then.
import socket as _socket
try:
    _socket.getfqdn().encode("idna")
except UnicodeError:
    from django.core.mail.utils import DNS_NAME as _DNS_NAME
    _DNS_NAME._fqdn = "localhost"

# Warehouse mailbox for readiness confirmations (token links are e-mailed here).
WAREHOUSE_EMAIL = env.WAREHOUSE_EMAIL

# ── Power BI / SAP BW stock pull ────────────────────────────────────────────────
# Warehouse stock (HU) can be pulled straight from the Power BI dataset (SAP BW cube)
# via the REST executeQueries (DAX) endpoint, instead of a manual file upload. Server
# auth uses a service principal (client credentials); for ad-hoc use a pre-fetched
# token can be supplied via POWERBI_ACCESS_TOKEN. Corporate TLS interception → point
# POWERBI_CA_BUNDLE at the root CA, or set POWERBI_SSL_VERIFY=false (dev only).
POWERBI_TENANT_ID     = env.POWERBI_TENANT_ID
# Defaults to Microsoft's public client (Azure CLI/Power BI Desktop) so the delegated
# device-code login works without registering an app or a service principal.
POWERBI_CLIENT_ID     = env.POWERBI_CLIENT_ID
POWERBI_CLIENT_SECRET = env.POWERBI_CLIENT_SECRET
# Optional separate public-client ID for the delegated device-code login (see config.py).
POWERBI_PUBLIC_CLIENT_ID = env.POWERBI_PUBLIC_CLIENT_ID
POWERBI_WORKSPACE_ID  = env.POWERBI_WORKSPACE_ID
POWERBI_DATASET_ID    = env.POWERBI_DATASET_ID
POWERBI_STOCK_TABLE   = env.POWERBI_STOCK_TABLE
POWERBI_ACCESS_TOKEN  = env.POWERBI_ACCESS_TOKEN
POWERBI_CA_BUNDLE     = env.POWERBI_CA_BUNDLE
POWERBI_SSL_VERIFY    = env.POWERBI_SSL_VERIFY

# Twilio SMS (driver notifications). Without credentials, SMS is "queued" and the
# link is shown for manual sending.
TWILIO_ACCOUNT_SID = env.TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN  = env.TWILIO_AUTH_TOKEN
TWILIO_FROM        = env.TWILIO_FROM

# Shared token for the HU verification REST API (external scanner app). Send it as
# the `X-API-Key` header. Empty → API only reachable in DEBUG (local development).
PALVIZ_API_TOKEN = env.PALVIZ_API_TOKEN
PALVIZ_API_TOKENS = env.PALVIZ_API_TOKENS

# n8n event webhook (outbound). Empty → emit_event is a no-op (zero regresji).
N8N_EVENT_URL = env.N8N_EVENT_URL
N8N_EVENT_SECRET = env.N8N_EVENT_SECRET

# ── ZARIA (wewnętrzny asystent AI) ──────────────────────────────────────────────
# Server-side-only vendor keys; the model catalog/pricing/limits live in the DB
# (ZariaConfig/ZariaModel) and are editable in the admin panel.
ZARIA_ANTHROPIC_API_KEY     = env.ZARIA_ANTHROPIC_API_KEY
ZARIA_OPENAI_API_KEY        = env.ZARIA_OPENAI_API_KEY
ZARIA_AZURE_OPENAI_API_KEY  = env.ZARIA_AZURE_OPENAI_API_KEY
ZARIA_AZURE_OPENAI_ENDPOINT = env.ZARIA_AZURE_OPENAI_ENDPOINT
ZARIA_OLLAMA_BASE_URL       = env.ZARIA_OLLAMA_BASE_URL
ZARIA_DEFAULT_MODEL_KEY     = env.ZARIA_DEFAULT_MODEL_KEY
ZARIA_RATE_LIMIT_PER_MINUTE = env.ZARIA_RATE_LIMIT_PER_MINUTE
ZARIA_RATE_LIMIT_PER_DAY    = env.ZARIA_RATE_LIMIT_PER_DAY
ZARIA_REQUEST_TIMEOUT_SEC   = env.ZARIA_REQUEST_TIMEOUT_SEC
ZARIA_MAX_RETRIES           = env.ZARIA_MAX_RETRIES
ZARIA_LOG_CONVERSATIONS     = env.ZARIA_LOG_CONVERSATIONS

# Symulator skanera (podgląd modułów w iframe na tej samej domenie).
X_FRAME_OPTIONS = "SAMEORIGIN"
# W019 (X_FRAME_OPTIONS != DENY) świadomie wyciszone — SAMEORIGIN jest celowy (wyżej).
# CI (`check --deploy --fail-level WARNING`) blokuje każde inne ostrzeżenie (CICD-006).
SILENCED_SYSTEM_CHECKS = ["security.W019"]

# Testy: DiscoverRunner + blokada sieci (także w procesach --parallel). tests/README-TESTS.md
TEST_RUNNER = "testkit.runner.GrooveTestRunner"
