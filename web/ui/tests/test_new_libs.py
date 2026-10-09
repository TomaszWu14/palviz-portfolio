"""Smoke + behaviour tests for the label helpers (barcode / QR PNG + endpoint)."""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ui.labels import barcode_png, qr_png


class LabelTests(SimpleTestCase):
    def test_barcode_png_is_png(self):
        png = barcode_png("0123456789", "code128")
        self.assertTrue(png[:8] == b"\x89PNG\r\n\x1a\n")

    def test_qr_png_is_png(self):
        self.assertTrue(qr_png("HU-12345")[:4] == b"\x89PNG")


class LabelEndpointTests(TestCase):
    def setUp(self):
        from ui.roles import GROUP_MASTER_DATA
        from django.contrib.auth.models import Group
        u = get_user_model().objects.create_user(username="u", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)

    def test_label_endpoint_returns_png(self):
        resp = self.client.get(reverse("ui:label_image"), {"value": "HU-1", "type": "qr"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")

    def test_label_endpoint_requires_value(self):
        self.assertEqual(self.client.get(reverse("ui:label_image")).status_code, 400)
