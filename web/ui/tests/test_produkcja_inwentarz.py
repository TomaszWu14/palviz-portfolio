"""Inwentarz produkcji i procedura rollbacku (audyt DOC-009, CICD-003).

`manage.py inwentarz_env` porównuje stan kontenera z tą listą i nigdy nie wypisuje
wartości tekstowych (sekretów)."""
import io
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase

from palletweb.config import AppEnv
from ui.management.commands.inwentarz_env import EXTRA_VARS, inventory, unknown_app_vars

ROOT = Path(__file__).resolve().parents[3]


class RunbookRollbackTests(SimpleTestCase):
    def test_runbook_has_rollback_procedure(self):
        text = (ROOT / "docs" / "runbook-operatora.md").read_text(encoding="utf-8")
        section = text.split("## Rollback", 1)[1].split("\n## ", 1)[0]
        for must in ("git revert -m 1", "migrate <app> <poprzednia_migracja>", "expand",
                     "runbook-odtworzenie.md", "/health/"):
            self.assertIn(must, section)


class InventoryCommandTests(SimpleTestCase):
    ENV = {"DJANGO_SECRET_KEY": "super-tajny-klucz-1234567890", "HU_SCAN_ENFORCE": "true",
           "DB_CONN_MAX_AGE": "abc", "CELERY_WORKER": "true", "POWERBI_CLIENTID": "x",
           "PATH": "/usr/bin"}

    def test_states_without_string_values(self):
        rows = {name: (is_set, state) for name, is_set, state in inventory(self.ENV)}
        self.assertEqual(rows["DJANGO_SECRET_KEY"], (True, "ustawiona"))
        self.assertEqual(rows["HU_SCAN_ENFORCE"], (True, "ustawiona = True"))
        self.assertEqual(rows["DB_CONN_MAX_AGE"], (True, "ustawiona = NIEPOPRAWNA"))
        self.assertEqual(rows["HU_PHOTO_RETAIN_DAYS"], (False, "domyślna = 30"))
        self.assertEqual(rows["CELERY_WORKER"], (True, "ustawiona"))
        self.assertEqual(len(rows), len(AppEnv.model_fields) + len(EXTRA_VARS))

    def test_unknown_names_with_app_prefix_are_flagged(self):
        self.assertEqual(unknown_app_vars(self.ENV), ["POWERBI_CLIENTID"])

    def test_command_never_prints_secret_values(self):
        out = io.StringIO()
        with mock.patch.dict("os.environ", self.ENV):
            call_command("inwentarz_env", "--ustawione", stdout=out)
        text = out.getvalue()
        self.assertNotIn("super-tajny", text)
        self.assertIn("DJANGO_SECRET_KEY", text)
        self.assertIn("POWERBI_CLIENTID", text)
        self.assertNotIn("HU_PHOTO_RETAIN_DAYS", text)  # --ustawione: bez domyślnych
