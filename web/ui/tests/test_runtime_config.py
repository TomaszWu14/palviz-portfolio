"""Strażnicy konfiguracji współbieżności (DB-001 + ARCH-002).

SQLite: WAL + transaction_mode=IMMEDIATE + timeout, żeby współbieżne zapisy z wątków
gunicorna (gthread) czekały na blokadę zamiast rzucać "database is locked".
"""

import tempfile
import unittest
from pathlib import Path

from django.conf import settings
from django.db import connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[3]
IS_SQLITE = settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"


@unittest.skipUnless(IS_SQLITE, "gałąź SQLite (brak DATABASE_URL)")
class SqliteConcurrencyOptionsTests(SimpleTestCase):
    def test_options_immediate_and_timeout(self):
        opts = settings.DATABASES["default"]["OPTIONS"]
        self.assertEqual(opts["transaction_mode"], "IMMEDIATE")
        self.assertGreaterEqual(opts["timeout"], 20)
        self.assertIn("journal_mode=WAL", opts["init_command"])

    def test_file_db_switches_to_wal(self):
        # Osobne połączenie na pliku tymczasowym z tymi samymi OPTIONS — baza testowa
        # (:memory:) ignoruje WAL, więc sprawdzamy na prawdziwym pliku.
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {**connections["default"].settings_dict, "NAME": str(Path(tmp) / "wal.sqlite3")}
            cfg["OPTIONS"] = dict(settings.DATABASES["default"]["OPTIONS"])
            wrapper = DatabaseWrapper(cfg, alias="wal_probe")
            try:
                with wrapper.cursor() as cur:
                    cur.execute("PRAGMA journal_mode")
                    self.assertEqual(cur.fetchone()[0].lower(), "wal")
                    cur.execute("PRAGMA synchronous")
                    self.assertEqual(cur.fetchone()[0], 1)  # 1 = NORMAL
            finally:
                wrapper.close()


class GunicornEntrypointTests(SimpleTestCase):
    def test_gthread_and_recycling(self):
        src = (REPO_ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
        for flag in ("--worker-class gthread", "--threads", "--max-requests ", "--max-requests-jitter"):
            self.assertIn(flag, src)
