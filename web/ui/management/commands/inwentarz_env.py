"""Inwentarz zmiennych środowiskowych na tej instancji — BEZ wartości (audyt DOC-009).

Uruchom w terminalu kontenera (Coolify → aplikacja → Terminal):

    python manage.py inwentarz_env               # wszystkie zmienne: ustawiona / domyślna
    python manage.py inwentarz_env --ustawione   # tylko ustawione w środowisku

Lista zmiennych to `palletweb.config.AppEnv` + zmienne czytane tylko przez entrypoint
obraz i boto3 (EXTRA_VARS). Wartości tekstowe NIGDY nie są wypisywane (sekrety); dla
flag i liczb wypisywana jest wartość efektywna. Na końcu: zmienne ze znanymi prefiksami,
których aplikacja nie zna — zwykle literówka w nazwie ustawionej w Coolify.
"""
import os

from django.core.management.base import BaseCommand
from pydantic import TypeAdapter

from palletweb.config import AppEnv

# Czytane poza AppEnv: docker-entrypoint.sh (worker/gunicorn), Dockerfile/Coolify (wersja
# builda dla /health/) i boto3 (klucze S3 przy AWS_STORAGE_BUCKET_NAME).
EXTRA_VARS = ("CELERY_WORKER", "CELERY_BEAT", "CELERY_CONCURRENCY", "WEB_CONCURRENCY",
              "GUNICORN_THREADS", "GIT_SHA", "SOURCE_COMMIT", "AWS_ACCESS_KEY_ID",
              "AWS_SECRET_ACCESS_KEY")

# Prefiksy charakterystyczne dla aplikacji — nieznana nazwa z takim prefiksem to podejrzenie
# literówki. Bez ogólnych (AWS_, OTEL_, SESSION_, DEFAULT_…): te czytają też boto3,
# OpenTelemetry i system, więc dawałyby fałszywe alarmy.
APP_PREFIXES = ("DJANGO_", "DB_", "DATABASE_", "CELERY_", "HU_", "GDPR_", "BACKUP_", "POWERBI_",
                "ZARIA_", "TWILIO_", "EMAIL_", "OIDC_", "GROOVE_", "PALVIZ_", "SENTRY_", "N8N_",
                "UKRAINE_", "TWA_", "GOOGLE_MAPS_", "DRIVER_REMINDER_", "DEVICE_CONFIRM_",
                "MASTER_DATA_", "PHV_", "GLS_", "TEAMS_", "CSP_", "SHIPMENT_", "PALLET_",
                "WAREHOUSE_", "COMPANY_")


def inventory(environ=None):
    """[(nazwa, ustawiona?, opis stanu)] dla zmiennych AppEnv + EXTRA_VARS."""
    environ = os.environ if environ is None else environ
    rows = []
    for name, field in AppEnv.model_fields.items():
        is_set = name in environ
        state = "ustawiona" if is_set else "domyślna"
        if field.annotation in (bool, int, float):  # flagi i liczby — bez sekretów
            value = field.default
            if is_set:
                try:
                    value = TypeAdapter(field.annotation).validate_python(environ[name])
                except ValueError:  # config i tak zatrzyma start z opisem błędu
                    value = "NIEPOPRAWNA"
            state += f" = {value}"
        rows.append((name, is_set, state))
    for name in EXTRA_VARS:
        is_set = name in environ
        rows.append((name, is_set, "ustawiona" if is_set else "domyślna"))
    return rows


def unknown_app_vars(environ=None):
    """Zmienne ze znanymi prefiksami, których nie czyta ani AppEnv, ani entrypoint."""
    environ = os.environ if environ is None else environ
    known = set(AppEnv.model_fields) | set(EXTRA_VARS) | {"DJANGO_SETTINGS_MODULE"}
    return sorted(n for n in environ if n.startswith(APP_PREFIXES) and n not in known)


class Command(BaseCommand):
    help = "Inwentarz zmiennych środowiskowych (bez wartości) — porównanie z Coolify (DOC-009)."

    def add_arguments(self, parser):
        parser.add_argument("--ustawione", action="store_true",
                            help="Wypisz tylko zmienne ustawione w środowisku.")

    def handle(self, *args, **options):
        rows = inventory()
        for name, is_set, state in rows:
            if is_set or not options["ustawione"]:
                self.stdout.write(f"{name:<34} {state}")
        n_set = sum(1 for _, is_set, _ in rows if is_set)
        self.stdout.write(f"\nUstawione: {n_set} z {len(rows)} znanych zmiennych.")
        unknown = unknown_app_vars()
        if unknown:
            self.stdout.write(self.style.WARNING(
                "Nieznane aplikacji (literówka? zmienna po usuniętej funkcji?): "
                + ", ".join(unknown)))
