"""One-time delegated (device-code) login to Power BI.

Run once (e.g. in the Coolify console): it prints a short URL + code; open it on any
device, sign in (MFA supported), and the refresh token is stored in the DB. The server
then refreshes access tokens silently — no browser needed afterwards.

    python manage.py powerbi_connect
"""
from django.core.management.base import BaseCommand

from ui import powerbi


class Command(BaseCommand):
    help = "Łączy PalViz z Power BI przez logowanie użytkownika (device code)."

    def handle(self, *args, **options):
        self.stdout.write("Rozpoczynam logowanie do Power BI (device code)...\n")
        try:
            username = powerbi.connect_device_flow(prompt=lambda msg: self.stdout.write(self.style.WARNING(msg)))
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Logowanie nieudane: {exc}"))
            return
        self.stdout.write(self.style.SUCCESS(
            f"Połączono z Power BI jako: {username or '(konto)'}. "
            "Pobieranie stanu zadziała teraz bez przeglądarki."))
