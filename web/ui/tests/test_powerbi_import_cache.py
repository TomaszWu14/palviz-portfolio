"""Import cache'u MSAL z lokalnego logowania → PowerBIToken (obejście blokady device-code)."""
import io
import json

from django.core.management import call_command
from django.test import TestCase

from ui import powerbi
from ui.models import PowerBIToken

# Minimalny cache MSAL z jednym kontem (bez refresh tokenu — silent-refresh zwróci None,
# ale zapis i rozpoznanie konta muszą zadziałać). environment=login.windows.net jest
# aliasem login.microsoftonline.com, więc get_accounts je znajdzie.
_ACCOUNT_CACHE = json.dumps({
    "Account": {
        "uid.utid-login.windows.net-example.com": {
            "home_account_id": "uid.utid",
            "environment": "login.windows.net",
            "realm": "example.com",
            "local_account_id": "uid",
            "username": "planista@example.com",
            "authority_type": "MSSTS",
        }
    }
})


class ImportCacheTests(TestCase):
    def setUp(self):
        # MSAL w konstruktorze pyta login.microsoftonline.com (discovery) — nagrana odpowiedź
        # zamiast prawdziwej sieci (blokada testkit.net). Wcześniej ten test wychodził do Entra.
        from testkit import integrations as it
        self.enterContext(it.integration(("entra_openid", "entra_instance")))

    def test_import_cache_persists_and_reads_account(self):
        account, ok, detail = powerbi.import_cache(_ACCOUNT_CACHE)
        self.assertEqual(account, "planista@example.com")
        self.assertFalse(ok)                       # brak refresh tokenu → silent zwraca None
        row = PowerBIToken.objects.first()
        self.assertTrue(row and row.cache)
        self.assertEqual(row.account, "planista@example.com")

    def test_empty_cache_rejected(self):
        with self.assertRaises(ValueError):
            powerbi.import_cache("   ")

    def test_garbage_rejected(self):
        with self.assertRaises(ValueError):
            powerbi.import_cache("to nie jest json")

    def test_cache_without_account_rejected(self):
        with self.assertRaises(ValueError):
            powerbi.import_cache('{"AccessToken": {}}')   # poprawny JSON, ale bez konta

    def test_management_command_via_file(self):
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(_ACCOUNT_CACHE)
            out = io.StringIO()
            call_command("powerbi_import_cache", file=path, stdout=out)
            self.assertIn("Cache wgrany", out.getvalue())
            self.assertTrue(PowerBIToken.objects.exists())
        finally:
            os.unlink(path)
