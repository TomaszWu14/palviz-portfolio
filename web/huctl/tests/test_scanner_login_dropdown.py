"""Nagłówek loginu na skanerze: pill = sam username, klik (natywny <details>) rozwija
strefy do kontroli + uprawnienia (role z kolorami). Zamiast zawijającego się
'username · wszystkie strefy'."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import ControllerZone
from ui.roles import GROUP_CONTROLLER


def _user(*groups):
    u = get_user_model().objects.create_user(username="janek", password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class ScannerLoginDropdownTests(TestCase):
    def test_dropdown_lists_zones_and_roles(self):
        u = _user(GROUP_CONTROLLER)
        ControllerZone.objects.create(user=u, code="92JU")
        self.client.force_login(u)
        html = self.client.get(reverse("ui:hu_control_menu")).content.decode()
        self.assertIn("op-menu", html)                         # dropdown obecny
        self.assertIn("Strefy do kontroli", html)
        self.assertIn("92JU", html)                            # moja strefa
        self.assertIn("Uprawnienia", html)
        self.assertIn("Kontrola HU", html)                     # etykieta roli
        self.assertNotIn("· wszystkie strefy", html)           # stary zawijający się napis zniknął

    def test_all_zones_label_when_unconfigured(self):
        u = _user(GROUP_CONTROLLER)          # brak wierszy ControllerZone → wszystkie strefy
        self.client.force_login(u)
        html = self.client.get(reverse("ui:hu_control_menu")).content.decode()
        self.assertIn("wszystkie strefy", html)
