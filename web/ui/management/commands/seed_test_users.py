"""Seed jednego konta testowego na rolę — hasło = login, do ręcznego przechodzenia
całego flow (Kontrola HU, MatInfo, Data Center…) jedną osobą.

Hasło == nazwa użytkownika (np. login `test_kontrola`, hasło `test_kontrola`) — świadoma
wygoda testowa, dlatego komenda bezwarunkowo odmawia startu przy DEBUG=false (bez obejścia).
"""

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError

from ui.roles import (
    GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT, GROUP_CONTROLLER, GROUP_LEADER,
    GROUP_VIEWER, GROUP_CLIENT, GROUP_WAREHOUSE, GROUP_OPTIMIZER,
)

PREFIX = "test_"

# login → (grupa lub None dla superusera)
TEST_USERS = [
    ("test_super",     None),              # superuser (pełny dostęp, is_superuser)
    ("test_admin",     GROUP_ADMIN),
    ("test_md",        GROUP_MASTER_DATA),
    ("test_transport", GROUP_TRANSPORT),
    ("test_kontrola",  GROUP_CONTROLLER),  # is_control_only → prosto do skanera HU
    ("test_lider",     GROUP_LEADER),
    ("test_podglad",   GROUP_VIEWER),
    ("test_klient",    GROUP_CLIENT),
    ("test_magazyn",   GROUP_WAREHOUSE),
    ("test_opt",       GROUP_OPTIMIZER),
]


class Command(BaseCommand):
    help = ("Tworzy konta testowe (jedno na rolę), hasło = login. "
            "Tylko środowisko nieprodukcyjne (DEBUG=true) — na prod zawsze odmawia.")

    def add_arguments(self, parser):
        parser.add_argument("--drop", action="store_true",
                            help=f"Usuń istniejące konta '{PREFIX}*' przed seedem.")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "Odmowa: konta testowe z hasłem=login tylko przy DJANGO_DEBUG=true. "
                "Na środowisku produkcyjnym ta komenda nie działa (bez wyjątków)."
            )

        if options["drop"]:
            n, _ = User.objects.filter(username__startswith=PREFIX).delete()
            self.stdout.write(f"  · Usunięto obiekty pasujące do '{PREFIX}*': {n}")

        for username, group_name in TEST_USERS:
            is_super = group_name is None
            user, created = User.objects.update_or_create(
                username=username,
                defaults={
                    "is_active": True,
                    "is_staff": is_super,
                    "is_superuser": is_super,
                },
            )
            user.set_password(username)   # hasło = login
            user.save()

            user.groups.set([] if is_super else [Group.objects.get_or_create(name=group_name)[0]])

            # nie wyrzucaj od razu na zmianę hasła (PasswordChangeRequiredMiddleware)
            if hasattr(user, "profile"):
                if user.profile.must_change_password:
                    user.profile.must_change_password = False
                    user.profile.save(update_fields=["must_change_password"])

            tag = "superuser" if is_super else group_name
            verb = "✓ utworzono" if created else "· zaktualizowano"
            self.stdout.write(self.style.SUCCESS(f"  {verb}: {username:<16} → {tag}"))

        self.stdout.write(self.style.SUCCESS(
            f"\nGotowe. {len(TEST_USERS)} kont testowych, hasło = login. "
            "Logowanie na /login/."))
