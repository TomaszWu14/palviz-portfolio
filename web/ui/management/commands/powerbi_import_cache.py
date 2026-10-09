"""Wgraj cache MSAL z lokalnego logowania (tools/powerbi_login_local.py) do bazy.

Obejście, gdy Conditional Access blokuje device-code: logujesz się interaktywnie w
przeglądarce na swoim PC, a wynikowy cache (z refresh tokenem) wgrywasz tutaj. Serwer
odświeża potem token po cichu.

    python manage.py powerbi_import_cache < cache.json
    python manage.py powerbi_import_cache --file cache.json
"""
import sys

from django.core.management.base import BaseCommand, CommandError

from ui import powerbi


class Command(BaseCommand):
    help = "Wgrywa cache MSAL (z lokalnego logowania) do PowerBIToken."

    def add_arguments(self, parser):
        parser.add_argument("--file", help="Plik z cache MSAL (domyślnie stdin).")

    def handle(self, *args, **options):
        path = options.get("file")
        if path:
            with open(path, encoding="utf-8") as f:
                raw = f.read()
        else:
            raw = (options.get("stdin") or sys.stdin).read()
        try:
            account, ok, detail = powerbi.import_cache(raw)
        except ValueError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f"Cache wgrany. Konto: {account or '(konto)'}. {detail}."))
        if not ok:
            self.stdout.write(self.style.WARNING(
                "Uwaga: sprawdź `python manage.py powerbi_diag` — auth może wymagać "
                "ponownego logowania lokalnego."))
