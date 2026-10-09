"""60-30-10 na skanerze: akcja główna menu („Skan etykiety HU") dostaje akcent (btn--accent),
reszta zostaje teal — jeden punkt uwagi zamiast 5 identycznych przycisków."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.roles import GROUP_CONTROLLER


class ScannerAccentTests(TestCase):
    def test_menu_primary_has_accent(self):
        u = get_user_model().objects.create_user(username="c", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        self.client.force_login(u)
        html = self.client.get(reverse("ui:hu_control_menu")).content.decode()
        self.assertIn("btn--accent", html)          # akcja główna wyróżniona
        self.assertIn("--accent:", html)            # token akcentu w bazie
