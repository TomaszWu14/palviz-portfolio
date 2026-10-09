"""Render-smoke podzakładek admina ZARIA — każda musi się wyrenderować (200) jako superuser."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class ZariaSubtabsRenderTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("zt_admin", "z@a.pl", "x")
        self.client.force_login(self.admin)

    def test_zaria_subtabs_render(self):
        for name in ["admin_zaria_models", "admin_zaria_role_access", "admin_zaria_user_access",
                     "admin_zaria_config", "admin_zaria_usage", "admin_zaria_audit"]:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(f"ui:{name}")).status_code, 200)
