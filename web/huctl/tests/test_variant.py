"""Deployment variant: GROOVE_VARIANT=hu serves ONLY the Kontrola HU + Wydruk HU modules
from the same codebase/DB (a separate warehouse-floor app under its own name/host)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from ui.platform_modules import (can_open_module, modules_for, portal_modules,
                                 variant_modules)
from ui.roles import GROUP_ADMIN, GROUP_WAREHOUSE


def _user(name, *groups):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class VariantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = _user("adm_v", GROUP_ADMIN)
        cls.warehouse = _user("wh_v", GROUP_WAREHOUSE)

    def test_full_platform_by_default(self):
        keys = {m.key for m in modules_for(self.admin)}
        self.assertIn("data_center", keys)               # office modules present
        self.assertIn("kontrola_hu", keys)
        self.assertTrue(can_open_module(self.admin, "paletyzacja"))

    @override_settings(GROOVE_VARIANT="hu")
    def test_hu_variant_limits_modules(self):
        keys = {m.key for m in modules_for(self.admin)}
        self.assertEqual(keys, {"kontrola_hu", "wydruk_hu"})   # only HU modules, even for admin
        self.assertFalse(can_open_module(self.admin, "data_center"))
        self.assertFalse(can_open_module(self.admin, "paletyzacja"))
        self.assertTrue(can_open_module(self.admin, "kontrola_hu"))
        self.assertTrue(can_open_module(self.warehouse, "wydruk_hu"))

    @override_settings(GROOVE_VARIANT="hu")
    def test_hu_variant_blocks_office_entry_view(self):
        self.client.force_login(self.admin)
        # HU modules still reachable
        self.assertEqual(self.client.get(reverse("ui:hu_control_hub")).status_code, 200)
        self.assertEqual(self.client.get(reverse("ui:hu_print_home")).status_code, 200)
        # office module entry is blocked by module_required, even for an admin
        self.assertEqual(self.client.get(reverse("ui:data_center")).status_code, 403)

    @override_settings(GROOVE_MODULES="kontrola_hu,wydruk_hu")
    def test_explicit_module_set_overrides(self):
        self.assertEqual(variant_modules(), {"kontrola_hu", "wydruk_hu"})
        self.assertFalse(can_open_module(self.admin, "transport"))     # not served here
        self.assertTrue(can_open_module(self.admin, "kontrola_hu"))


class PortalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = _user("adm_p", GROUP_ADMIN)

    def test_portal_lists_all_role_modules_with_local_urls(self):
        keys = {m.key for m, url in portal_modules(self.admin)}
        self.assertIn("data_center", keys)
        self.assertIn("kontrola_hu", keys)

    @override_settings(GROOVE_VARIANT="hu",
                       GROOVE_MODULE_URLS="data_center=https://biuro.groove.pl/data-center/")
    def test_portal_links_across_services_even_when_not_served_here(self):
        # This instance serves only HU, but the portal still shows the office tile and links
        # it to the other service (ignore_variant), while HU stays local.
        by_key = {m.key: url for m, url in portal_modules(self.admin)}
        self.assertEqual(by_key["data_center"], "https://biuro.groove.pl/data-center/")
        self.assertTrue(by_key["kontrola_hu"].startswith("/"))          # local path here
