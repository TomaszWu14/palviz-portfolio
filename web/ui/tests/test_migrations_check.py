"""DATA-02: strażnik higieny łańcucha migracji — zero brakujących migracji.

Pina na stałe wynik `makemigrations --check --dry-run` dla wszystkich czterech
appek (`ui`, `wh3d`, `huctl`, `transport`), obok istniejącego hooka pre-commit
(`web/scripts/migrations_check.py`), który działa tylko przy zmianach `models*`.
Ten test biegnie zawsze w ramach `manage.py test ui.tests` (CI + lokalnie), więc
łapie też rozbieżności wprowadzone poza commitem zmieniającym pliki modeli."""
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase


class MigrationsCheckTests(SimpleTestCase):
    # makemigrations --check wewnętrznie woła loader.check_consistent_history(),
    # które odpytuje tabelę django_migrations (odczyt, zero zapisu) — SimpleTestCase
    # domyślnie blokuje jakikolwiek dostęp do bazy; ten atrybut odblokowuje tylko
    # 'default' bez przechodzenia na (wolniejszy) TestCase z transakcją per test.
    databases = {"default"}

    def test_no_missing_migrations(self):
        out = StringIO()
        try:
            call_command(
                "makemigrations", "ui", "wh3d", "huctl", "transport",
                check=True, dry_run=True, stdout=out, stderr=out, verbosity=1,
            )
        except SystemExit:
            self.fail(
                "makemigrations --check wykrył niezmigrowane zmiany modeli:\n"
                + out.getvalue()
            )
        self.assertIn("No changes detected", out.getvalue())
