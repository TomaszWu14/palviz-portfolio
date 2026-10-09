"""Ostrość tekstu na skanerze: font-smoothing + wyłączony pulse na breakpoint Zebry +
pełnopikselowe ramki. Smoke — reguły muszą trafić do renderu scanner/base.html."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.roles import GROUP_CONTROLLER


class ScannerCrispCssTests(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_user(username="ctrl", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        self.client.force_login(u)

    def test_crisp_css_present(self):
        html = self.client.get(reverse("ui:hu_control_menu")).content.decode()
        # Odwrócone: antialiased wyłącza subpiksel (tekst cieńszy/mydlany), a
        # optimizeLegibility zamula stary WebView Zebry — mają NIE występować.
        self.assertNotIn("-webkit-font-smoothing: antialiased", html)
        self.assertNotIn("text-rendering: optimizeLegibility", html)
        self.assertIn("animation: none !important", html)   # pulse zdjęty na Zebrze
