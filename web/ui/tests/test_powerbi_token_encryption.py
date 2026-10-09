"""Refresh token Power BI zaszyfrowany w bazie (audyt SEC-011)."""
from django.contrib.admin.sites import site
from django.test import TestCase, override_settings

from ui import powerbi
from ui.models import PowerBIToken

RAW = '{"RefreshToken": {"x": {"secret": "tajny-refresh-token"}}}'


class _Cache:
    has_state_changed = True

    def serialize(self):
        return RAW


class PowerBITokenEncryptionTests(TestCase):
    def test_saved_cache_is_encrypted_and_roundtrips(self):
        powerbi._save_cache(_Cache())
        stored = PowerBIToken.objects.get().cache
        self.assertTrue(stored.startswith("enc1:"))
        self.assertNotIn("tajny-refresh-token", stored)
        self.assertEqual(powerbi._decrypt_cache(stored), RAW)

    def test_legacy_plaintext_still_readable(self):
        self.assertEqual(powerbi._decrypt_cache(RAW), RAW)

    def test_wrong_secret_key_degrades_to_empty(self):
        stored = powerbi._encrypt_cache(RAW)
        with override_settings(SECRET_KEY="inny-klucz-po-rotacji-" + "x" * 40), self.assertLogs("ui.powerbi", "WARNING"):
            self.assertEqual(powerbi._decrypt_cache(stored), "")

    def test_admin_hides_token(self):
        self.assertIn("cache", site._registry[PowerBIToken].exclude)
