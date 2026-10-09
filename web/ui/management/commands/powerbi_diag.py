"""Diagnostyka połączenia z Power BI — uruchom na serwerze, gdy „Odśwież z Power BI"
nie działa:

    python manage.py powerbi_diag

Sprawdza kolejno konfigurację, wybraną ścieżkę uwierzytelnienia, faktyczne pobranie
tokenu i realne zapytanie DAX — i wypisuje prawdziwy komunikat błędu (np. AADSTS…),
zamiast ogólnego „nie działa". Nie zmienia żadnych danych.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Diagnostyka konfiguracji i połączenia z Power BI (tylko odczyt)."

    def handle(self, *args, **opts):
        from ui import powerbi
        failed = 0
        self.stdout.write("=== Diagnostyka Power BI ===========================")
        for name, ok, detail in powerbi.diagnose():
            mark = self.style.SUCCESS("OK  ") if ok else self.style.ERROR("BŁĄD")
            if not ok:
                failed += 1
            self.stdout.write(f"{mark}  {name}: {detail}")
        self.stdout.write("====================================================")
        if failed:
            self.stdout.write(self.style.ERROR(
                f"Nieudanych sprawdzeń: {failed}. Jeśli błąd dotyczy logowania, połącz "
                "ponownie: python manage.py powerbi_connect"))
        else:
            self.stdout.write(self.style.SUCCESS("Power BI działa — odświeżanie stocku powinno przechodzić."))
