"""The container HEALTHCHECK curls http://127.0.0.1:8000/health/, so loopback hosts
must pass Django's host validation or Coolify rolls the deploy back."""
from django.conf import settings
from django.test import TestCase


class HealthHostTests(TestCase):
    def test_loopback_hosts_are_allowed(self):
        self.assertIn("127.0.0.1", settings.ALLOWED_HOSTS)
        self.assertIn("localhost", settings.ALLOWED_HOSTS)

    def test_health_ok_with_loopback_host(self):
        r = self.client.get("/health/", HTTP_HOST="127.0.0.1:8000")
        self.assertEqual(r.status_code, 200)      # not a 400 DisallowedHost
