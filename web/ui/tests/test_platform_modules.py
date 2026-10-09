"""MONTY platform module registry — every module points at a real URL, and the
role filter hides modules a user may not open."""
from django.test import TestCase
from django.urls import reverse, NoReverseMatch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from ui.platform_modules import MODULES, modules_for
from ui.roles import GROUP_TRANSPORT, GROUP_MASTER_DATA, GROUP_CLIENT


class PlatformModuleRegistryTests(TestCase):
    def test_every_module_url_reverses(self):
        for m in MODULES:
            try:
                reverse(f"ui:{m.url_name}")
            except NoReverseMatch:                      # pragma: no cover
                self.fail(f"Module '{m.key}' points at unknown url_name '{m.url_name}'")

    def test_keys_are_unique(self):
        keys = [m.key for m in MODULES]
        self.assertEqual(len(keys), len(set(keys)))

    def test_superuser_sees_all_but_deduplicated_customers(self):
        # Superuser reaches customers via Data Center, so the standalone "klienci" hub
        # tile is hidden for them (no duplicate).
        su = get_user_model().objects.create_superuser("boss", password="x")
        keys = {m.key for m in modules_for(su)}
        self.assertIn("data_center", keys)
        self.assertNotIn("klienci", keys)
        self.assertEqual(len(modules_for(su)), len(MODULES) - 1)

    def test_master_data_does_not_see_standalone_customers(self):
        u = get_user_model().objects.create_user("md", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        keys = {m.key for m in modules_for(u)}
        self.assertIn("data_center", keys)              # customers live here
        self.assertNotIn("klienci", keys)               # not duplicated on the hub

    def test_client_role_still_sees_customers_tile(self):
        # "Obsługa klienta" has no Data Center, so the hub tile is their sole entry point.
        u = get_user_model().objects.create_user("klient", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_CLIENT)[0])
        keys = {m.key for m in modules_for(u)}
        self.assertIn("klienci", keys)
        self.assertNotIn("data_center", keys)

    def test_role_filter_hides_other_modules(self):
        u = get_user_model().objects.create_user("driver", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_TRANSPORT)[0])
        keys = {m.key for m in modules_for(u)}
        self.assertIn("transport", keys)               # Transport may open it
        self.assertNotIn("kontrola_hu", keys)          # HU control is not their module
