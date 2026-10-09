"""„Zainstaluj aplikację” nie pokazuje się w zainstalowanej PWA: guard po display-mode
standalone w scanner/base.html. Smoke — guard musi być w renderze."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.roles import GROUP_CONTROLLER


class PwaInstallHiddenTests(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_user(username="ctrl", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        self.client.force_login(u)

    def test_standalone_guard_present(self):
        html = self.client.get(reverse("ui:hu_control_menu")).content.decode()
        self.assertIn("display-mode: standalone", html)   # guard obecny
        self.assertIn("appinstalled", html)               # ukrycie po instalacji
