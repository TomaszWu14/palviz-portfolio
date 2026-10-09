"""Hub (launcher): kafelki nawigują bezpośrednio — zoom/dwuklik usunięty (2026-08,
zbliżenie 3D żyje tylko na skanerze PHV)."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class HubZoomRenderTest(TestCase):
    def test_hub_renders_without_zoom(self):
        User.objects.create_superuser("hub_admin", "h@a.pl", "x")
        self.client.force_login(User.objects.get(username="hub_admin"))
        resp = self.client.get(reverse("ui:home"))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertNotIn("mzoom", body)          # brak overlayu zoomu
        self.assertNotIn("openZoom", body)       # brak skryptu przechwytującego klik
        self.assertIn("mtile", body)             # kafelki modułów nadal są linkami
