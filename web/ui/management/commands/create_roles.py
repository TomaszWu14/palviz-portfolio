from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, User
from ui.roles import (
    GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT, GROUP_VIEWER, GROUP_CLIENT,
    GROUP_WAREHOUSE, GROUP_OPTIMIZER, ALL_GROUPS
)


class Command(BaseCommand):
    help = "Tworzy grupy (role) użytkowników PalViz i opcjonalnie superusera."

    def add_arguments(self, parser):
        parser.add_argument(
            "--superuser",
            metavar="USERNAME",
            help="Utwórz superusera o podanej nazwie (hasło będzie pytane interaktywnie).",
        )
        parser.add_argument(
            "--email",
            default="",
            help="Email superusera (opcjonalnie).",
        )

    def handle(self, *args, **options):
        # 1. Create groups
        for name in ALL_GROUPS:
            group, created = Group.objects.get_or_create(name=name)
            if created:
                self.stdout.write(self.style.SUCCESS(f"  ✓ Utworzono grupę: {name}"))
            else:
                self.stdout.write(f"  · Grupa już istnieje: {name}")

        self.stdout.write("")
        self.stdout.write("Role i dostępy:")
        self.stdout.write(f"  {GROUP_ADMIN:<22} → pełny dostęp do wszystkich modułów")
        self.stdout.write(f"  {GROUP_MASTER_DATA:<22} → produkty, kartony, kategorie, instrukcje, ruchy, model mag.")
        self.stdout.write(f"  {GROUP_TRANSPORT:<22} → wysyłki, przewoźnicy, kalkulator, podgląd instrukcji")
        self.stdout.write(f"  {GROUP_VIEWER:<22} → podgląd: instrukcje, produkty, occupancy, mapa 3D")
        self.stdout.write(f"  {GROUP_CLIENT:<22} → wąski dostęp: tylko baza klientów / odbiorców")
        self.stdout.write(f"  {GROUP_WAREHOUSE:<22} → wydruk etykiet HU (moduł Wydruk HU)")
        self.stdout.write(f"  {GROUP_OPTIMIZER:<22} → optymalizacja wypełnienia palet (skrzynka zgłoszeń)")

        # 2. Optional superuser creation
        username = options.get("superuser")
        if username:
            if User.objects.filter(username=username).exists():
                self.stdout.write(self.style.WARNING(f"\nUżytkownik '{username}' już istnieje."))
            else:
                import getpass
                password = getpass.getpass(f"\nHasło dla '{username}': ")
                email = options.get("email", "")
                user = User.objects.create_superuser(username=username, email=email, password=password)
                admin_group = Group.objects.get(name=GROUP_ADMIN)
                user.groups.add(admin_group)
                self.stdout.write(self.style.SUCCESS(f"\n✓ Superuser '{username}' utworzony i przypisany do grupy '{GROUP_ADMIN}'."))

        self.stdout.write(self.style.SUCCESS("\nGotowe. Możesz teraz przypisywać użytkowników do grup w panelu /admin-panel/users/"))
