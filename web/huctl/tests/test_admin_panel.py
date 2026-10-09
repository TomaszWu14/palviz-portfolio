"""Advanced admin panel: all-8-role CRUD, the login × module override matrix, and its
enforcement on module entry views (hybrid role + per-user override)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import UserModuleAccess
from ui.platform_modules import can_open_module, modules_for
from ui.roles import (GROUP_ADMIN, GROUP_WAREHOUSE, GROUP_CONTROLLER, GROUP_VIEWER,
                      ALL_GROUPS)


def _user(name, *groups):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class AdminPanelAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = _user("adm", GROUP_ADMIN)
        cls.viewer = _user("view", GROUP_VIEWER)

    def test_panel_pages_require_admin(self):
        for name in ("admin_panel", "admin_module_access", "admin_role_matrix", "admin_users"):
            self.client.force_login(self.viewer)
            self.assertEqual(self.client.get(reverse(f"ui:{name}")).status_code, 403, name)
            self.client.force_login(self.admin)
            self.assertEqual(self.client.get(reverse(f"ui:{name}")).status_code, 200, name)

    def test_user_form_lists_all_eight_roles(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("ui:admin_user_new"))
        names = {g.name for g in r.context["groups"]}
        self.assertEqual(names, set(ALL_GROUPS))          # all 8, not just 4

    def test_user_form_saves_scanner_section(self):
        # Sekcja skanera (motyw przewoźnika) edytowalna z panelu, nie tylko importem.
        from django.contrib.auth.models import User
        self.client.force_login(self.admin)
        self.client.post(reverse("ui:admin_user_new"),
                         {"username": "opgls", "password1": "Str0ngPass!x",
                          "password2": "Str0ngPass!x", "is_active": "1", "section": "GLS"})
        u = User.objects.get(username="opgls")
        self.assertEqual(u.profile.section, "GLS")
        # Zła sekcja → błąd walidacji, bez zapisu użytkownika.
        self.client.post(reverse("ui:admin_user_new"),
                         {"username": "opbad", "password1": "Str0ngPass!x",
                          "password2": "Str0ngPass!x", "is_active": "1", "section": "XXX"})
        self.assertFalse(User.objects.filter(username="opbad").exists())


class ModuleOverrideTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = _user("adm2", GROUP_ADMIN)
        cls.warehouse = _user("wh", GROUP_WAREHOUSE)      # role allows wydruk_hu
        cls.controller = _user("ctrl", GROUP_CONTROLLER)  # role does NOT allow wydruk_hu

    def test_role_default_without_override(self):
        self.assertTrue(can_open_module(self.warehouse, "wydruk_hu"))
        self.assertFalse(can_open_module(self.controller, "wydruk_hu"))

    def test_force_allow_grants_entry(self):
        UserModuleAccess.objects.create(user=self.controller, module_key="wydruk_hu", allowed=True)
        self.assertTrue(can_open_module(self.controller, "wydruk_hu"))
        self.client.force_login(self.controller)
        self.assertEqual(self.client.get(reverse("ui:hu_print_home")).status_code, 200)
        self.assertIn("wydruk_hu", {m.key for m in modules_for(self.controller)})

    def test_force_deny_blocks_entry(self):
        UserModuleAccess.objects.create(user=self.warehouse, module_key="wydruk_hu", allowed=False)
        self.assertFalse(can_open_module(self.warehouse, "wydruk_hu"))
        self.client.force_login(self.warehouse)
        self.assertEqual(self.client.get(reverse("ui:hu_print_home")).status_code, 403)
        self.assertNotIn("wydruk_hu", {m.key for m in modules_for(self.warehouse)})

    def test_superuser_bypasses_deny(self):
        su = get_user_model().objects.create_superuser("root", "r@x.pl", "x")
        UserModuleAccess.objects.create(user=su, module_key="wydruk_hu", allowed=False)
        self.assertTrue(can_open_module(su, "wydruk_hu"))   # superuser always passes

    def test_matrix_post_saves_overrides(self):
        self.client.force_login(self.admin)
        # Optimistic lock (#627): zapis wymaga aktualnego tokenu macierzy z GET.
        token = self.client.get(reverse("ui:admin_module_access")).context["matrix_token"]
        r = self.client.post(reverse("ui:admin_module_access"), {
            "matrix_token": token,
            f"m_{self.controller.pk}_wydruk_hu": "allow",
            f"m_{self.warehouse.pk}_wydruk_hu": "deny",
        })
        self.assertRedirects(r, reverse("ui:admin_module_access"))
        self.assertTrue(UserModuleAccess.objects.get(user=self.controller, module_key="wydruk_hu").allowed)
        self.assertFalse(UserModuleAccess.objects.get(user=self.warehouse, module_key="wydruk_hu").allowed)
