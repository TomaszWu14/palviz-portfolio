"""Liczby wierszy wszystkich tabel aplikacji — weryfikacja przeniesienia danych SQLite → Postgres.

Procedura (docs/postgres-switch.md, krok „Weryfikacja”):

    # na SQLite, tuż przed dumpdata (ruch zapisujący zatrzymany)
    python manage.py policz_wiersze --zapisz /app/data/wiersze_sqlite.json
    # po loaddata na Postgresie (DATABASE_URL ustawione)
    python manage.py policz_wiersze --porownaj /app/data/wiersze_sqlite.json

`--porownaj` wypisuje różnice i kończy się błędem, gdy któraś tabela się nie zgadza.
Tabele celowo pomijane przy dumpdata (contenttypes, uprawnienia, sesje, logi admina,
axes) są wyłączone także tutaj — `migrate` tworzy je od nowa.
"""
import json

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

# Te same wykluczenia co w poleceniu dumpdata z docs/postgres-switch.md.
SKIP_APPS = {"contenttypes", "sessions", "admin", "axes"}
SKIP_MODELS = {"auth.permission"}


def row_counts():
    """{app_label.model: liczba wierszy} dla wszystkich zarządzanych modeli z tabelą."""
    counts = {}
    for model in apps.get_models():
        meta = model._meta
        label = meta.label_lower
        if (meta.app_label in SKIP_APPS or label in SKIP_MODELS or not meta.managed
                or meta.proxy):
            continue
        counts[label] = model._base_manager.count()
    return dict(sorted(counts.items()))


class Command(BaseCommand):
    help = "Liczy wiersze tabel aplikacji; zapis/porównanie przy przenosinach bazy (M1, DB-001)."

    def add_arguments(self, parser):
        parser.add_argument("--zapisz", metavar="PLIK", help="Zapisz liczby do pliku JSON.")
        parser.add_argument("--porownaj", metavar="PLIK",
                            help="Porównaj z plikiem JSON zapisanym wcześniej (np. na SQLite).")

    def handle(self, *args, **options):
        counts = row_counts()
        if options["zapisz"]:
            with open(options["zapisz"], "w", encoding="utf-8") as fh:
                json.dump(counts, fh, ensure_ascii=False, indent=1)
            self.stdout.write(f"Zapisano {len(counts)} tabel ({sum(counts.values())} wierszy) "
                              f"do {options['zapisz']}.")
        if not options["porownaj"]:
            if not options["zapisz"]:
                for label, n in counts.items():
                    self.stdout.write(f"{label:<55} {n:>9}")
            return
        with open(options["porownaj"], encoding="utf-8") as fh:
            before = json.load(fh)
        diffs = [(label, before.get(label), counts.get(label))
                 for label in sorted(set(before) | set(counts))
                 if before.get(label, 0) != counts.get(label, 0)]
        if not diffs:
            self.stdout.write(self.style.SUCCESS(
                f"✓ Zgodne: {len(counts)} tabel, {sum(counts.values())} wierszy."))
            return
        for label, was, now in diffs:
            self.stdout.write(f"  {label:<55} było {was if was is not None else '—':>9}  "
                              f"jest {now if now is not None else '—':>9}")
        raise CommandError(f"Niezgodne liczby wierszy w {len(diffs)} tabelach — nie przełączaj "
                           "ruchu, sprawdź loaddata.")
