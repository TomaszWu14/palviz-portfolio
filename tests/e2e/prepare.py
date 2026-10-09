"""Baza dla testów wizualnych: czysta SQLite → migrate → seed_testdata → stałe daty → sesje.

    python tests/e2e/prepare.py        (z katalogu głównego repo; woła to globalSetup Playwrighta)

Wynik: tests/e2e/.tmp/e2e.sqlite3 i tests/e2e/.tmp/sessions.json ({persona: sessionid}).
Daty z auto_now/auto_now_add są przestawiane na stały dzień, żeby zrzut listy nie zmieniał się
z dnia na dzień. Czasy względne (SLA „po terminie X h”) maskuje sam test.
"""
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(__file__).resolve().parent / ".tmp"
DB = TMP / "e2e.sqlite3"
FIXED = dt.datetime(2026, 9, 1, 9, 30, tzinfo=dt.timezone.utc)
PERSONAS = ("superuser", "Kontrola HU", "Transport", "Podgląd")

os.environ.update(DB_PATH=str(DB), DJANGO_DEBUG="true", DJANGO_ALLOWED_HOSTS="*",
                  DJANGO_SECRET_KEY="e2e-visual-0123456789-abcdefghijklmnopqrstuvwxyz",
                  CELERY_BROKER_URL="memory://")
sys.path[:0] = [str(ROOT / "web"), str(ROOT)]
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "palletweb.settings")


def main():
    TMP.mkdir(exist_ok=True)
    DB.unlink(missing_ok=True)
    import django
    django.setup()
    from django.apps import apps
    from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
    from django.contrib.sessions.backends.db import SessionStore
    from django.core.management import call_command
    from django.db import models

    call_command("migrate", verbosity=0)
    call_command("seed_testdata")
    for model in apps.get_models():
        auto = [f.name for f in model._meta.fields
                if isinstance(f, (models.DateTimeField, models.DateField)) and (f.auto_now or f.auto_now_add)]
        if auto:
            val = {n: (FIXED if isinstance(model._meta.get_field(n), models.DateTimeField) else FIXED.date()) for n in auto}
            model._default_manager.all().update(**val)
    # Pilny komunikat z seedu otwiera modal blokujący, który zasłaniałby cały ekran skanera.
    from ui.models import Notification
    Notification.objects.filter(requires_ack=True).update(confirmed_at=FIXED, is_read=True)

    from testkit.personas import make
    out = {}
    for name in PERSONAS:
        u = make(name)
        s = SessionStore()
        s[SESSION_KEY], s[BACKEND_SESSION_KEY] = str(u.pk), "django.contrib.auth.backends.ModelBackend"
        s[HASH_SESSION_KEY] = u.get_session_auth_hash()
        s.create()
        out[name] = s.session_key
    (TMP / "sessions.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"e2e: baza {DB.name}, {len(out)} sesji")


if __name__ == "__main__":
    main()
