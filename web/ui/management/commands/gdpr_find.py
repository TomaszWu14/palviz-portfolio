"""Wyszukanie danych osobowej osoby we wszystkich tabelach (RODO art. 15 — dostęp/eksport).

    python manage.py gdpr_find jan.kowalski@firma.pl
    python manage.py gdpr_find 600123456 --json > wynik.json
"""
import json

from django.core.management.base import BaseCommand, CommandError

from ui.gdpr import find_person


class Command(BaseCommand):
    help = "Znajdź dane osoby (e-mail, telefon, login, nazwisko, nr rej.) we wszystkich tabelach z danymi osobowymi."

    def add_arguments(self, parser):
        parser.add_argument("term")
        parser.add_argument("--json", action="store_true", help="Wynik jako JSON (do eksportu dla osoby).")

    def handle(self, *args, term, **opts):
        try:
            found = find_person(term)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if opts["json"]:
            self.stdout.write(json.dumps(found, ensure_ascii=False, indent=2))
            return
        if not found:
            self.stdout.write("Brak trafień.")
            return
        for label, rows in found.items():
            self.stdout.write(self.style.MIGRATE_HEADING(f"{label} ({len(rows)})"))
            for r in rows:
                self.stdout.write("  " + ", ".join(f"{k}={v}" for k, v in r.items()))
        self.stdout.write("Uwaga: kopie zapasowe i Sentry przechowują dane do czasu rotacji (runbook-odtworzenie.md).")
