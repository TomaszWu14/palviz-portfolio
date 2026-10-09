# Beat taski: dzienny raport kontroli HU dla liderów + backup DB/media z aplikacji.
import tempfile
from datetime import timedelta
from unittest import mock, skipUnless

from django.contrib.auth.models import Group, User
from django.core import mail
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from ui.models import HUControlAttempt, HandlingUnit, Shipment
from ui.roles import GROUP_LEADER
from ui.tasks import run_backup, send_hu_daily_report


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   EMAIL_HOST="smtp.test", DEFAULT_FROM_EMAIL="groove@test")
class HuDailyReportTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_LEADER)
        self.leader = User.objects.create_user("lider", "lider@example.com", "Zx9!longpass")
        self.leader.groups.add(g)

    def _attempt_yesterday(self):
        sh = Shipment.objects.create()
        hu = HandlingUnit.objects.create(shipment=sh, code="HU1")
        ctrl = User.objects.create_user("kontroler", "k@example.com", "Zx9!longpass")
        a = HUControlAttempt.objects.create(hu=hu, controller=ctrl, result="ok")
        # created_at ma auto_now_add — cofamy update'em na wczoraj. Pełne 24 h, nie 20:
        # przy -20h test odpalony po 20:00 lądował jeszcze „dzisiaj" i raport za wczoraj
        # był pusty (flaky zależny od godziny uruchomienia).
        HUControlAttempt.objects.filter(pk=a.pk).update(
            created_at=timezone.now() - timedelta(days=1))

    def test_report_sent_to_leader_with_kpi(self):
        self._attempt_yesterday()
        out = send_hu_daily_report()
        self.assertTrue(out["ok"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("lider@example.com", mail.outbox[0].to)
        self.assertIn("Raport kontroli HU", mail.outbox[0].subject)
        self.assertIn("kontroler", mail.outbox[0].body)

    def test_no_recipients_is_noop(self):
        self.leader.email = ""
        self.leader.save(update_fields=["email"])
        out = send_hu_daily_report()
        self.assertFalse(out["ok"])
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_HOST="")
    def test_no_smtp_is_noop(self):
        self.assertEqual(send_hu_daily_report()["reason"], "no_smtp")


class BackupTaskTests(TransactionTestCase):
    # Ścieżka SQLite (.backup in-process). Na Postgresie run_backup woła pg_dump — realnego
    # bin/env nie ma sensu odpalać na klonowanej bazie testowej w CI (ścieżka pg_dump działa
    # na prodzie z PGPASSWORD). Dlatego ten test jest SQLite-only.
    # TransactionTestCase, nie TestCase: TestCase trzyma otwartą transakcję IMMEDIATE na
    # współdzielonej bazie testowej w pamięci, więc .backup z drugiego połączenia dostawał
    # SQLITE_LOCKED w kółko (lokalny `make test` wisiał na tym teście).
    @skipUnless(connection.vendor == "sqlite", "test ścieżki backupu SQLite (na Postgresie pg_dump)")
    def test_sqlite_backup_creates_file_and_prunes(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(BACKUP_DIR=tmp, BACKUP_RETAIN_DAYS=14):
                out = run_backup()
                self.assertTrue(out["ok"], out)
                self.assertIn("_db.sqlite3", out["file"])
                self.assertTrue(os.path.exists(out["file"]))


class SqliteBackupLockTests(SimpleTestCase):
    """Zablokowana baza SQLite: .backup nie może wisieć w nieskończoność (CPython ponawia
    krok przy SQLITE_BUSY/LOCKED bez limitu) — po _SQLITE_BACKUP_TIMEOUT_S przerywamy,
    usuwamy niepełny plik i zgłaszamy błąd (worker Celery wolny)."""

    def test_locked_database_times_out_instead_of_hanging(self):
        import os
        import sqlite3
        import time
        from django.conf import settings
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "live.sqlite3")
            holder = sqlite3.connect(src, isolation_level=None)
            holder.execute("CREATE TABLE t (x)")
            holder.execute("INSERT INTO t VALUES (1)")
            holder.execute("BEGIN EXCLUSIVE")                 # inny proces trzyma blokadę
            out_dir = os.path.join(tmp, "out")
            try:
                with mock.patch.dict(settings.DATABASES["default"],
                                     {"ENGINE": "django.db.backends.sqlite3", "NAME": src}), \
                        mock.patch("ui.tasks._SQLITE_BACKUP_TIMEOUT_S", 1), \
                        mock.patch("ui.notifications.notify") as notify, \
                        mock.patch("ui.notifications.owner_users", return_value=[]), \
                        override_settings(BACKUP_DIR=out_dir):
                    t0 = time.monotonic()
                    out = run_backup()
                    elapsed = time.monotonic() - t0
            finally:
                holder.execute("ROLLBACK")
                holder.close()
            self.assertFalse(out["ok"])
            self.assertIn("zablokowana", out["reason"])
            self.assertLess(elapsed, 15)
            notify.assert_called_once()
            self.assertEqual([f for f in os.listdir(out_dir) if "_db." in f], [])   # bez połówki


class PgDumpBackupTests(TestCase):
    # Ścieżka Postgres bez realnego serwera: hasło/sslmode tylko w env procesu, nigdy w argv
    # (argv widać w `ps`); settings nie eksportuje DATABASE_URL (BACKUP-001/SEC-019).
    DB = {"ENGINE": "django.db.backends.postgresql", "NAME": "palviz", "USER": "groove",
          "PASSWORD": "tajne-haslo", "HOST": "db.local", "PORT": "5432",
          "OPTIONS": {"sslmode": "require"}}

    def test_pg_dump_cmd_keeps_password_out_of_argv(self):
        from ui.tasks import _pg_dump_cmd
        cmd, env = _pg_dump_cmd(self.DB)
        self.assertEqual(cmd, ["pg_dump", "-h", "db.local", "-p", "5432", "-U", "groove", "palviz"])
        self.assertNotIn("tajne-haslo", " ".join(cmd))
        self.assertEqual(env["PGPASSWORD"], "tajne-haslo")
        self.assertEqual(env["PGSSLMODE"], "require")

    def test_run_backup_writes_gzipped_dump(self):
        import gzip
        import os
        from types import SimpleNamespace
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            # Podmieniamy settings tylko w module tasks — DATABASES połączeń testowych nietknięte.
            fake = SimpleNamespace(DATABASES={"default": self.DB}, MEDIA_ROOT="",
                                   BACKUP_DIR=tmp, BACKUP_RETAIN_DAYS=14)
            with mock.patch("ui.tasks.settings", fake), \
                    mock.patch("subprocess.run", return_value=SimpleNamespace(stdout=b"dump")) as run:
                out = run_backup()
            self.assertTrue(out["ok"], out)
            self.assertTrue(out["file"].endswith("_db.sql.gz"))
            with gzip.open(out["file"]) as fh:
                self.assertEqual(fh.read(), b"dump")
            self.assertTrue(os.path.exists(out["file"]))
            argv, kwargs = run.call_args.args[0], run.call_args.kwargs
            self.assertNotIn("tajne-haslo", " ".join(argv))
            self.assertEqual(kwargs["env"]["PGPASSWORD"], "tajne-haslo")
