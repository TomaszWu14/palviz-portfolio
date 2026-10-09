"""DATABASE_URL parsing — Postgres when set, SQLite fallback otherwise."""
from django.test import SimpleTestCase

from palletweb.settings import _db_from_url


class DbUrlTests(SimpleTestCase):
    def test_empty_falls_back_to_sqlite(self):
        self.assertIsNone(_db_from_url(""))
        self.assertIsNone(_db_from_url(None))
        self.assertIsNone(_db_from_url("   "))

    def test_parses_managed_postgres_url(self):
        cfg = _db_from_url("postgresql://user:p%40ss@db.example.com:5432/palviz?sslmode=require")
        self.assertEqual(cfg["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(cfg["NAME"], "palviz")
        self.assertEqual(cfg["USER"], "user")
        self.assertEqual(cfg["PASSWORD"], "p@ss")          # percent-decoded
        self.assertEqual(cfg["HOST"], "db.example.com")
        self.assertEqual(cfg["PORT"], "5432")
        self.assertEqual(cfg["OPTIONS"]["sslmode"], "require")

    def test_no_sslmode_when_absent(self):
        cfg = _db_from_url("postgres://u:p@localhost/palviz")
        self.assertEqual(cfg["HOST"], "localhost")
        self.assertEqual(cfg["PORT"], "")
        self.assertNotIn("sslmode", cfg["OPTIONS"])
