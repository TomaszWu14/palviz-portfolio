"""Power BI auth: pasted token / service principal / delegated device-code session."""
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from ui import powerbi
from ui.models import PowerBIToken


class PowerBIAuthTests(TestCase):
    @override_settings(POWERBI_ACCESS_TOKEN="tok123",
                       POWERBI_WORKSPACE_ID="w", POWERBI_DATASET_ID="d")
    def test_pasted_token_used_first_and_configured(self):
        self.assertEqual(powerbi.get_access_token(), "tok123")
        self.assertTrue(powerbi.is_configured())

    @override_settings(POWERBI_ACCESS_TOKEN="", POWERBI_CLIENT_SECRET="",
                       POWERBI_TENANT_ID="", POWERBI_WORKSPACE_ID="", POWERBI_DATASET_ID="")
    def test_not_configured_when_empty(self):
        self.assertFalse(powerbi.is_configured())

    @override_settings(POWERBI_ACCESS_TOKEN="", POWERBI_CLIENT_SECRET="")
    def test_get_token_without_session_raises_clear_error(self):
        with patch.object(powerbi, "_public_app") as pa:
            pa.return_value.get_accounts.return_value = []
            with self.assertRaises(RuntimeError) as ctx:
                powerbi.get_access_token()
        self.assertIn("powerbi_connect", str(ctx.exception))

    def test_has_delegated_session_and_account(self):
        self.assertFalse(powerbi.has_delegated_session())     # no row yet
        PowerBIToken.objects.create(cache="{}", account="operator@example.com")
        with patch.object(powerbi, "_public_app") as pa:
            pa.return_value.get_accounts.return_value = [{"username": "operator@example.com"}]
            self.assertTrue(powerbi.has_delegated_session())
        self.assertEqual(powerbi.connected_account(), "operator@example.com")

    def test_device_flow_prints_code_and_returns_user(self):
        fake_app = MagicMock()
        fake_app.initiate_device_flow.return_value = {"user_code": "ABCD",
                                                      "message": "Wejdź na microsoft.com/devicelogin i wpisz ABCD"}
        fake_app.acquire_token_by_device_flow.return_value = {"access_token": "xyz"}
        fake_app.get_accounts.return_value = [{"username": "operator@example.com"}]
        msgs = []
        with patch.object(powerbi, "_public_app", return_value=fake_app), \
                patch.object(powerbi, "_save_cache"):
            user = powerbi.connect_device_flow(prompt=msgs.append)
        self.assertEqual(user, "operator@example.com")
        self.assertTrue(any("devicelogin" in m for m in msgs))

    def test_device_flow_failure_raises(self):
        fake_app = MagicMock()
        fake_app.initiate_device_flow.return_value = {"error": "bad", "error_description": "nope"}
        with patch.object(powerbi, "_public_app", return_value=fake_app):
            with self.assertRaises(RuntimeError):
                powerbi.connect_device_flow(prompt=lambda _m: None)


class DelegatedClientIdTests(TestCase):
    """Device flow musi lecieć na kliencie PUBLICZNYM. POWERBI_CLIENT_ID jest dzielony
    ze ścieżką service principal, więc przy ustawionym sekrecie wskazuje aplikację
    poufną — na niej AAD odrzuca device flow (AADSTS7000218)."""

    @override_settings(POWERBI_CLIENT_ID="sp-app-id", POWERBI_CLIENT_SECRET="sekret",
                       POWERBI_PUBLIC_CLIENT_ID="")
    def test_service_principal_id_is_not_used_for_device_flow(self):
        self.assertEqual(powerbi._delegated_client_id(), powerbi.PUBLIC_CLIENT_ID)

    @override_settings(POWERBI_CLIENT_ID="wlasny-public", POWERBI_CLIENT_SECRET="",
                       POWERBI_PUBLIC_CLIENT_ID="")
    def test_own_public_client_used_when_no_secret(self):
        self.assertEqual(powerbi._delegated_client_id(), "wlasny-public")

    @override_settings(POWERBI_CLIENT_ID="sp-app-id", POWERBI_CLIENT_SECRET="sekret",
                       POWERBI_PUBLIC_CLIENT_ID="jawny-public")
    def test_explicit_public_client_wins(self):
        self.assertEqual(powerbi._delegated_client_id(), "jawny-public")


@override_settings(POWERBI_ACCESS_TOKEN="", POWERBI_TENANT_ID="t",
                   POWERBI_CLIENT_ID="sp-app-id", POWERBI_CLIENT_SECRET="sekret")
class ServicePrincipalFallbackTests(TestCase):
    """Nieudany service principal nie może ubijać całego pobrania, gdy obok jest
    działająca sesja delegowana (tenant ACME blokuje SP)."""

    def _sp_failing(self):
        app = MagicMock()
        app.acquire_token_for_client.return_value = {
            "error": "unauthorized_client",
            "error_description": "AADSTS7000229: service principal zablokowany"}
        return app

    def test_falls_back_to_delegated_session_when_sp_fails(self):
        delegated = MagicMock()
        delegated.get_accounts.return_value = [{"username": "kierownik@example.com"}]
        delegated.acquire_token_silent.return_value = {"access_token": "TOKEN-DELEGOWANY"}
        with patch("msal.ConfidentialClientApplication", return_value=self._sp_failing()), \
             patch.object(powerbi, "_public_app", return_value=delegated), \
             patch.object(powerbi, "_save_cache"):
            self.assertEqual(powerbi.get_access_token(), "TOKEN-DELEGOWANY")

    def test_sp_error_is_reported_when_no_delegated_session(self):
        delegated = MagicMock()
        delegated.get_accounts.return_value = []
        with patch("msal.ConfidentialClientApplication", return_value=self._sp_failing()), \
             patch.object(powerbi, "_public_app", return_value=delegated):
            with self.assertRaises(RuntimeError) as ctx:
                powerbi.get_access_token()
        self.assertIn("AADSTS7000229", str(ctx.exception))


class ErrorRecordingTests(TestCase):
    """Błąd logowania/pobrania musi zostawić ślad — wcześniej ginął w wątku w tle."""

    def test_error_is_persisted_and_cleared(self):
        powerbi.record_error(RuntimeError("AADSTS50173: token wygasł"))
        msg, at = powerbi.last_error()
        self.assertIn("AADSTS50173", msg)
        self.assertIsNotNone(at)
        powerbi.clear_error()
        self.assertEqual(powerbi.last_error()[0], "")

    def test_record_error_creates_row_when_missing(self):
        PowerBIToken.objects.all().delete()
        powerbi.record_error(RuntimeError("boom"))
        self.assertIn("boom", powerbi.last_error()[0])
