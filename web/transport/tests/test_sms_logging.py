"""Audyt INT-004: błąd wysyłki SMS (Twilio) był połykany bez śladu w logach. Teraz
logger.warning z typem wyjątku, kodem HTTP i kodem błędu Twilio — bez treści SMS, bez
tokenu i bez pełnego numeru telefonu (dane osobowe)."""
import io
import urllib.error
from unittest import mock

from django.test import SimpleTestCase, override_settings

from transport.sms import send_sms

TWILIO = {"TWILIO_ACCOUNT_SID": "AC123", "TWILIO_AUTH_TOKEN": "tajny-token",
          "TWILIO_FROM": "+48100200300"}


@override_settings(**TWILIO)
class SmsLoggingTests(SimpleTestCase):
    def test_http_error_logged_with_status_and_twilio_code(self):
        err = urllib.error.HTTPError("https://api.twilio.com/x", 400, "Bad Request", {},
                                     io.BytesIO(b'{"code": 21211, "message": "Invalid To"}'))
        with mock.patch("urllib.request.urlopen", side_effect=err), \
                self.assertLogs("transport.sms", level="WARNING") as logs:
            self.assertFalse(send_sms("+48500100200", "Kierowca: tajna treść"))
        out = "\n".join(logs.output)
        self.assertIn("HTTP 400", out)
        self.assertIn("21211", out)
        self.assertIn("…200", out)                              # tylko końcówka numeru
        for secret in ("tajny-token", "tajna treść", "+48500100200"):
            self.assertNotIn(secret, out)

    def test_network_error_logged_with_type(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")), \
                self.assertLogs("transport.sms", level="WARNING") as logs:
            self.assertFalse(send_sms("+48500100200", "x"))
        self.assertIn("TimeoutError", "\n".join(logs.output))

    def test_success_does_not_log_warning(self):
        with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(b"{}")), \
                self.assertNoLogs("transport.sms", level="WARNING"):
            self.assertTrue(send_sms("+48500100200", "x"))
