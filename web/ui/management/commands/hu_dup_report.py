"""Raport duplikatów pickHU — do odpalenia PRZED migracją 0107_hu_code_unique.

Migracja nakłada globalną unikalność `HandlingUnit.code` i rozbraja istniejące
duplikaty, przemianowując przegranych na `<kod>#dup<pk>`. Ta komenda pokazuje z góry,
czego dotknie: ile grup, które palety wygrają, a które stracą kod (i czy mają
historię kontroli, którą trzeba będzie ręcznie wyjaśnić).

    python manage.py hu_dup_report            # podsumowanie + pierwsze 20 grup
    python manage.py hu_dup_report --all      # wszystkie grupy
    python manage.py hu_dup_report --csv out.csv
"""
import csv

from django.core.management.base import BaseCommand

from ui.models import HandlingUnit, HUControlAttempt


class Command(BaseCommand):
    help = "Pokazuje duplikaty kodów pickHU przed nałożeniem unikalności (migracja 0107)."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true",
                            help="Wypisz wszystkie grupy, nie tylko pierwsze 20.")
        parser.add_argument("--csv", metavar="PLIK",
                            help="Zapisz pełną listę do pliku CSV (średnik, BOM).")

    def handle(self, *args, **options):
        # Grupujemy po code.upper(): constraint jest case-sensitive, ale skaner ma
        # fallback `code__iexact`, więc 'ABC'/'abc' też są dla operatora nierozróżnialne.
        groups = {}
        for hu in (HandlingUnit.objects.exclude(code="")
                   .select_related("shipment")
                   .only("pk", "code", "status", "verified_at", "seq",
                         "shipment__name", "shipment__is_stock")):
            groups.setdefault(hu.code.upper(), []).append(hu)

        dupes = {k: v for k, v in groups.items() if len(v) > 1}
        if not dupes:
            self.stdout.write(self.style.SUCCESS(
                "✓ Brak duplikatów pickHU — migracja 0107 nie ruszy żadnego wiersza."))
            return

        with_attempts = set(
            HUControlAttempt.objects
            .filter(hu__in=[hu.pk for v in dupes.values() for hu in v])
            .values_list("hu_id", flat=True))

        def rank(hu):
            # Ta sama kolejność co w migracji — inaczej raport kłamałby o zwycięzcy.
            return (hu.pk in with_attempts, hu.verified_at is not None,
                    hu.status != "planned", hu.pk)

        rows, losers_with_history = [], 0
        for code, hus in sorted(dupes.items()):
            ordered = sorted(hus, key=rank, reverse=True)
            for i, hu in enumerate(ordered):
                has_hist = hu.pk in with_attempts
                if i and has_hist:
                    losers_with_history += 1
                rows.append({
                    "kod": code,
                    "rola": "ZWYCIĘZCA" if i == 0 else "przemianowany",
                    "pk": hu.pk,
                    "nowy_kod": hu.code if i == 0 else f"{hu.code[:64 - len(f'#dup{hu.pk}')]}#dup{hu.pk}",
                    "dostawa": hu.shipment.name,
                    "stock": "tak" if hu.shipment.is_stock else "nie",
                    "seq": hu.seq,
                    "status": hu.status,
                    "ma_historie_kontroli": "tak" if has_hist else "nie",
                })

        self.stdout.write(self.style.WARNING(
            f"Duplikatów: {len(dupes)} grup, {len(rows)} palet łącznie."))
        self.stdout.write(
            f"Kod straci: {len(rows) - len(dupes)} palet "
            f"(w tym {losers_with_history} z historią kontroli — te wymagają ręcznego wyjaśnienia).")
        self.stdout.write("")

        shown = rows if options["all"] else rows[:60]
        current = None
        for r in shown:
            if r["kod"] != current:
                current = r["kod"]
                self.stdout.write(self.style.MIGRATE_HEADING(f"pickHU {current}"))
            mark = "  ✓" if r["rola"] == "ZWYCIĘZCA" else "  →"
            hist = " [HISTORIA KONTROLI]" if r["ma_historie_kontroli"] == "tak" else ""
            self.stdout.write(
                f"{mark} pk={r['pk']:<7} {r['status']:<11} dostawa={r['dostawa'][:30]:<30}"
                f" stock={r['stock']:<3} → {r['nowy_kod']}{hist}")
        if len(shown) < len(rows):
            self.stdout.write(f"\n… i {len(rows) - len(shown)} więcej (użyj --all lub --csv).")

        path = options.get("csv")
        if path:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter=";")
                w.writeheader()
                w.writerows(rows)
            self.stdout.write(self.style.SUCCESS(f"\n✓ Zapisano {len(rows)} wierszy do {path}"))
