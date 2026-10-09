"""Limiter publicznych formularzy (audyt SEC-014): reset hasła i linki z tokenem."""
from django.core.cache import cache
from django.test import TestCase, override_settings

from testkit.factories import ShipmentFactory, UserFactory
from transport.models import DriverAssignment


@override_settings(EMAIL_HOST="smtp.test", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetThrottleTests(TestCase):
    def setUp(self):
        cache.clear()

    def _post(self, email, ip):
        return self.client.post("/password-reset/", {"email": email}, REMOTE_ADDR=ip, HTTP_X_FORWARDED_FOR=ip)

    def test_per_email_limit(self):
        UserFactory(email="ofiara@firma.pl")
        codes = [self._post("ofiara@firma.pl", f"10.0.0.{i}").status_code for i in range(4)]
        self.assertEqual(codes[:3], [302, 302, 302])
        self.assertEqual(codes[3], 429)

    def test_per_ip_limit(self):
        codes = [self._post(f"a{i}@firma.pl", "10.9.9.9").status_code for i in range(6)]
        self.assertNotIn(429, codes[:5])
        self.assertEqual(codes[5], 429)

    def test_get_not_counted(self):
        for _ in range(10):
            self.assertEqual(self.client.get("/password-reset/").status_code, 200)


class TokenFormThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.da = DriverAssignment.objects.create(shipment=ShipmentFactory())

    def test_driver_form_posts_limited_per_token(self):
        url = f"/driver-form/{self.da.form_token}/"
        codes = [self.client.post(url, {}).status_code for _ in range(21)]
        self.assertNotIn(429, codes[:20])
        self.assertEqual(codes[20], 429)
        other = DriverAssignment.objects.create(shipment=ShipmentFactory())
        self.assertNotEqual(self.client.post(f"/driver-form/{other.form_token}/", {}).status_code, 429)
