"""PWA endpoints: manifest + service worker (installable full-screen app)."""
from django.test import TestCase
from django.urls import reverse


class PWATests(TestCase):
    def test_manifest(self):
        resp = self.client.get(reverse("ui:pwa_manifest"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("manifest", resp["Content-Type"])
        body = resp.content.decode()
        # Scalony manifest „GROOVE Go": jedna instalka skanera, scope „/", wejście = /scan/.
        self.assertIn('"start_url": "/scan/?app=1"', body)
        self.assertIn('"scope": "/"', body)
        # 'standalone' (not 'fullscreen') so the system nav bar / back button stays visible.
        self.assertIn('standalone', body)
        self.assertIn("icon-512", body)

    def test_service_worker(self):
        resp = self.client.get(reverse("ui:pwa_sw"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("javascript", resp["Content-Type"])
        self.assertEqual(resp["Service-Worker-Allowed"], "/")
        self.assertIn("addEventListener('fetch'", resp.content.decode())


class AssetLinksTests(TestCase):
    def test_empty_when_unconfigured(self):
        resp = self.client.get("/.well-known/assetlinks.json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_populated_when_configured(self):
        from django.test import override_settings
        with override_settings(TWA_PACKAGE_NAME="pl.acme.palviz", TWA_SHA256_FINGERPRINT="AA:BB"):
            data = self.client.get("/.well-known/assetlinks.json").json()
        self.assertEqual(data[0]["target"]["package_name"], "pl.acme.palviz")
        self.assertIn("AA:BB", data[0]["target"]["sha256_cert_fingerprints"])
