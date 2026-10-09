"""GROOVE Go — scalony launcher skanera: kafle wg roli (1 moduł → wprost, 2 → kafle),
bez modułu → wyszukiwarka (Q-43), jeden manifest scope „/", odbicie okna apki z huba (per okno, bez cookie)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.roles import (GROUP_CONTROLLER, GROUP_LEADER, GROUP_MASTER_DATA,
                      GROUP_WAREHOUSE, GROUP_TRANSPORT)


def _user(name, group):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=group)[0])
    return u


class LauncherRouting(TestCase):
    def test_single_module_role_goes_straight_in(self):
        # Magazyn (picker) widzi tylko Hierarchię → launcher wpuszcza wprost.
        self.client.force_login(_user("mag", GROUP_WAREHOUSE))
        r = self.client.get(reverse("ui:scanner_launcher"))
        self.assertRedirects(r, reverse("ui:phv_home"), fetch_redirect_response=False)

    def test_master_data_single_module(self):
        self.client.force_login(_user("md", GROUP_MASTER_DATA))
        r = self.client.get(reverse("ui:scanner_launcher"))
        self.assertRedirects(r, reverse("ui:phv_home"), fetch_redirect_response=False)

    def test_controller_goes_to_control_menu(self):
        # Kontroler → menu Kontroli HU (jeden widok skanera), BEZ launchera z kaflami.
        self.client.force_login(_user("ctrl", GROUP_CONTROLLER))
        r = self.client.get(reverse("ui:scanner_launcher"))
        self.assertRedirects(r, reverse("ui:hu_control_menu"), fetch_redirect_response=False)

    def test_leader_goes_to_control_menu(self):
        self.client.force_login(_user("lead", GROUP_LEADER))
        r = self.client.get(reverse("ui:scanner_launcher"))
        self.assertRedirects(r, reverse("ui:hu_control_menu"), fetch_redirect_response=False)

    def test_control_menu_has_matinfo_tile(self):
        # Kontroler w menu kontroli widzi kafelek MATinfo (podgląd hierarchii) na dole.
        self.client.force_login(_user("ctrl3", GROUP_CONTROLLER))
        r = self.client.get(reverse("ui:hu_control_menu"))
        self.assertContains(r, "MATinfo")
        self.assertContains(r, reverse("ui:phv_home"))

    def test_no_scanner_module_goes_to_search(self):
        # Q-43 (2026-09-28): /scan/ dla każdej roli — bez modułu skanera wyszukiwarka
        # magazynowa (tylko odczyt), nie 403.
        self.client.force_login(_user("tr", GROUP_TRANSPORT))
        r = self.client.get(reverse("ui:scanner_launcher"))
        self.assertRedirects(r, reverse("ui:warehouse_search"), fetch_redirect_response=False)

    def test_app_query_forwarded_without_cookie(self):
        # `?app=1` idzie dalej (strona docelowa oznacza okno), bez ciasteczka dzielonego
        # z kartami przeglądarki.
        self.client.force_login(_user("ctrl2", GROUP_CONTROLLER))
        r = self.client.get(reverse("ui:scanner_launcher"), {"app": "1"})
        self.assertRedirects(r, reverse("ui:hu_control_menu") + "?app=1",
                             fetch_redirect_response=False)
        self.assertNotIn("pv_scanner", r.cookies)

    def test_scanner_cookie_no_longer_bounces_home(self):
        self.client.force_login(_user("mag2", GROUP_WAREHOUSE))
        self.client.cookies["pv_scanner"] = "1"
        r = self.client.get(reverse("ui:home"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'location.replace("%s")' % reverse("ui:scanner_launcher"))

    def test_hub_without_scanner_module_has_no_bounce(self):
        # Transport nie ma modułu skanera — odbicie dałoby 403, więc skryptu nie ma.
        self.client.force_login(_user("tr2", GROUP_TRANSPORT))
        r = self.client.get(reverse("ui:home"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, reverse("ui:scanner_launcher"))


class MergedManifest(TestCase):
    def test_manifest_is_merged_scope_root(self):
        r = self.client.get(reverse("ui:pwa_manifest"))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('"scope": "/"', body)
        self.assertIn("Go", body)
        self.assertIn('"start_url": "/scan/?app=1"', body)

    def test_phv_manifest_is_alias(self):
        # Stare instalacje/linki /phv/manifest.webmanifest → ten sam scalony manifest.
        a = self.client.get(reverse("ui:pwa_manifest")).content
        b = self.client.get(reverse("ui:pwa_manifest_phv")).content
        self.assertEqual(a, b)
