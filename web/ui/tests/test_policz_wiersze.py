"""`manage.py policz_wiersze` — weryfikacja przeniesienia danych SQLite → Postgres (M1)."""
import io
import json
import os
import tempfile

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ui.management.commands.policz_wiersze import row_counts
from ui.models import Product


class PoliczWierszeTests(TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(os.remove, self.path)

    def _call(self, *args):
        out = io.StringIO()
        call_command("policz_wiersze", *args, stdout=out)
        return out.getvalue()

    def test_counts_app_tables_and_skips_dump_exclusions(self):
        Product.objects.create(code="PW-1", name="Liczony")
        counts = row_counts()
        self.assertEqual(counts["ui.product"], 1)
        self.assertIn("auth.user", counts)
        for skipped in ("contenttypes.contenttype", "auth.permission", "sessions.session"):
            self.assertNotIn(skipped, counts)

    def test_same_data_compares_clean(self):
        Product.objects.create(code="PW-2", name="A")
        self._call("--zapisz", self.path)
        self.assertIn("Zgodne", self._call("--porownaj", self.path))

    def test_difference_is_reported_and_fails(self):
        self._call("--zapisz", self.path)
        Product.objects.create(code="PW-3", name="Nowy po zapisie")
        out = io.StringIO()
        with self.assertRaisesMessage(CommandError, "Niezgodne liczby wierszy"):
            call_command("policz_wiersze", "--porownaj", self.path, stdout=out)
        # produkt + jego historia (simple-history) — obie tabele różnią się o 1
        self.assertIn("ui.product ", out.getvalue())

    def test_saved_file_is_plain_json(self):
        self._call("--zapisz", self.path)
        with open(self.path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertTrue(all(isinstance(v, int) for v in data.values()))
