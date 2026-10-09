"""Zasiej bazę deweloperską/E2E pełnym zestawem danych testowych (testkit.seed).

    python manage.py seed_testdata

Tylko przy DJANGO_DEBUG=true — to dane fikcyjne (persony z hasłem testkit.factories.PASSWORD)
i NIGDY nie mogą trafić na produkcję. Wymaga zależności testowych (requirements-test.txt).
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Zasiewa bazę danymi testowymi (persony + dane domenowe). Tylko DEBUG."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_testdata działa tylko przy DJANGO_DEBUG=true (dane testowe!).")
        from django.contrib.auth import get_user_model
        from testkit import personas
        from testkit.factories import PASSWORD
        from testkit.seed import seed_all

        if get_user_model().objects.filter(username=personas.username("Administratorzy")).exists():
            self.stdout.write("Baza już zasiana (jest persona p_admin) — pomijam.")
            return
        with transaction.atomic():
            data = seed_all()
        self.stdout.write(self.style.SUCCESS(
            f"Zasiano: {len(personas.PERSONAS) - 1} person (hasło: {PASSWORD}), "
            f"{len(data.catalog.products)} produktów, {len(data.hu.all)} HU, "
            f"{len(data.shipments.by_status)} statusów shipmentów."))
