"""Pasek nawigacji 1b: grupowanie modułów wg roli (nav_grouped) + render sekcji w base."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.platform_modules import nav_grouped, NAV_GROUP_ORDER
from ui.roles import GROUP_ADMIN, GROUP_WAREHOUSE


def _user(group):
    u = get_user_model().objects.create_user("u", password="x")
    u.groups.add(Group.objects.get_or_create(name=group)[0])
    return u


class NavGrouped(TestCase):
    def test_admin_groups_ordered_and_zadania_direct(self):
        groups, direct = nav_grouped(_user(GROUP_ADMIN))
        labels = [g for g, _ in groups]
        # sekcje w ustalonej kolejności, tylko niepuste
        self.assertEqual(labels, [g for g in NAV_GROUP_ORDER if g in labels])
        self.assertEqual(labels, ["Operacje", "Magazyn", "Transport", "Dane"])
        keys = {m.key for _, items in groups for m, _ in items}
        self.assertIn("kontrola_hu", keys)          # Operacje
        self.assertIn("phv", keys)                  # Magazyn (MATinfo)
        # Zadania to pozycja bezpośrednia (z licznikiem), nie w grupie
        self.assertIn("zadania", {m.key for m, _ in direct})
        self.assertNotIn("zadania", keys)

    def test_role_filters_groups(self):
        # Magazyn (warehouse) widzi tylko swoje moduły → brak sekcji Operacje/Transport
        groups, direct = nav_grouped(_user(GROUP_WAREHOUSE))
        keys = {m.key for _, items in groups for m, _ in items} | {m.key for m, _ in direct}
        self.assertIn("phv", keys)                  # MATinfo (Magazyn)
        self.assertIn("wydruk_hu", keys)            # Operacje
        self.assertNotIn("data_center", keys)       # brak roli Master Data

    def test_navbar_renders_group_buttons(self):
        self.client.force_login(_user(GROUP_ADMIN))
        html = self.client.get(reverse("ui:home")).content.decode()
        self.assertIn('class="gvnav"', html)
        for label in ["Operacje", "Magazyn", "Transport", "Dane"]:
            self.assertIn(f">{label}<", html)
