"""Etap 1 — kontrakt każdej integracji zewnętrznej × 6 nagranych scenariuszy
(ok, http_4xx, http_5xx, timeout, empty, bad_format) na PRAWDZIWYM kodzie aplikacji.

Sprawdza, że awaria dostawcy kończy się udokumentowanym zachowaniem funkcji (None / False /
wyjątek łapany przez wołającego / przyjazny ZariaLLMError), a nie 500. Sieć jest zablokowana
(testkit.net) — każda odpowiedź pochodzi z testkit.integrations.
"""
import unittest
import unittest.mock
from decimal import Decimal
from types import SimpleNamespace

import requests
from django.core import mail
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from testkit import integrations as it

OK, FAILS = "ok", ("http_4xx", "http_5xx", "timeout")
ALL = it.SCENARIOS


@override_settings(**it.INTEGRATION_SETTINGS)
class BestEffortIntegrationTests(SimpleTestCase):
    """Integracje „best-effort”: nigdy nie rzucają, zwracają wartość zastępczą."""

    def run_matrix(self, name, call, expected):
        for scenario in ALL:
            with self.subTest(integration=name, scenario=scenario):
                cache.clear()
                with it.integration(name, scenario) as http:
                    self.assertEqual(call(), expected(scenario))
                self.assertEqual(len(http.calls), 1)

    def test_nbp_rate(self):
        from ui.nbp import get_rate
        self.run_matrix("nbp", lambda: get_rate("EUR"),
                        lambda s: Decimal("4.2512") if s == OK else None)

    # B-011 (naprawione): awaria NBP zapamiętana na FAIL_TTL — drugi render nie czeka na timeout
    def test_nbp_outage_not_retried_on_every_render(self):
        from ui.nbp import get_rate
        cache.clear()
        with it.integration("nbp", "timeout") as http:
            get_rate("EUR")
            get_rate("EUR")
        self.assertEqual(len(http.calls), 1)

    def test_nbp_retried_after_fail_ttl(self):
        import time_machine
        from ui.nbp import FAIL_TTL, get_rate
        cache.clear()
        with time_machine.travel("2026-09-28 10:00", tick=False) as clock:
            with it.integration("nbp", "timeout"):
                self.assertIsNone(get_rate("EUR"))
            clock.shift(FAIL_TTL + 1)
            with it.integration("nbp", "ok"):
                self.assertEqual(get_rate("EUR"), Decimal("4.2512"))

    def test_google_maps_route(self):
        from transport.views.mailing import _google_route
        km = lambda s: 512 if s == OK else None                         # noqa: E731
        self.run_matrix("google_maps", lambda: _google_route("Łódź", "Gdańsk")[0], km)

    def test_twilio_sms(self):
        from transport.views.driver import _send_sms
        self.run_matrix("twilio", lambda: _send_sms("+48500100200", "Kierowca: potwierdź odbiór"),
                        lambda s: s not in FAILS)                        # 2xx = przyjęte przez Twilio

    def test_teams_webhook(self):
        from ui.notifications import send_teams_message
        self.run_matrix("teams", lambda: send_teams_message("Reguła pakowania", "REF100001"),
                        lambda s: s not in FAILS)

    def test_n8n_event(self):
        from ui.notifications import emit_event
        self.run_matrix("n8n", lambda: emit_event("hu.escalated", {"hu": "003590000000000001"}),
                        lambda s: s not in FAILS)
        with it.integration("n8n") as http:
            emit_event("x", {})
        self.assertIn(b'"kind": "x"', http.calls[0].body)

    def test_ollama_health_check(self):
        from ui.zaria_llm import provider_health
        self.run_matrix("ollama", lambda: provider_health("ollama")[0], lambda s: s not in FAILS)


@override_settings(**it.INTEGRATION_SETTINGS)
class RaisingIntegrationTests(TestCase):
    """Integracje, których kontrakt to „rzuca — łapie wołający” (import stocku, SSO, MD API)."""

    def assert_raises_on_failure(self, name, call, check_ok, exc=Exception):
        for scenario in ALL:
            with self.subTest(integration=name, scenario=scenario):
                with it.integration(name, scenario):
                    if scenario == OK:
                        check_ok(call())
                    else:
                        with self.assertRaises(exc):
                            call()

    def test_master_data_api(self):
        from ui import master_data_client as md
        self.assert_raises_on_failure(
            "master_data", lambda: md.get_product("REF100001"),
            lambda p: self.assertEqual(p["code"], "REF100001"), requests.RequestException)

    def test_powerbi_dax(self):
        from ui.powerbi import fetch_table

        def ok(result):
            header, rows = result
            self.assertEqual(header, ["materiał", "partia", "ilość", "miejsce"])
            self.assertEqual(rows, [["REF100001", "LOT0000001", 120, "01-01-01"]])
        self.assert_raises_on_failure("powerbi_dax", fetch_table, ok)

    def test_powerbi_import_view_survives_every_failure(self):
        from testkit.personas import client_for
        client = client_for("superuser")
        for scenario in FAILS + ("empty", "bad_format"):
            with self.subTest(scenario=scenario), it.integration("powerbi_dax", scenario):
                r = client.post(reverse("ui:planner_stock_powerbi_import"), follow=True)
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, "Nie udało się pobrać danych z Power BI")

    @override_settings(POWERBI_ACCESS_TOKEN="", POWERBI_CLIENT_SECRET="sp-secret",
                       POWERBI_TENANT_ID="t", POWERBI_CLIENT_ID="c")
    def test_powerbi_msal_token(self):
        from ui.powerbi import get_access_token
        for scenario in ALL:
            with self.subTest(scenario=scenario), it.fake_msal(scenario):
                if scenario == OK:
                    self.assertEqual(get_access_token(), "pbi-token")
                else:
                    with self.assertRaises((RuntimeError, requests.RequestException)):
                        get_access_token()

    @override_settings(OIDC_OP_TOKEN_ENDPOINT="https://sso.example.test/realms/acme/token",
                       OIDC_OP_USER_ENDPOINT="https://sso.example.test/realms/acme/userinfo",
                       OIDC_RP_CLIENT_ID="groove", OIDC_RP_CLIENT_SECRET="s")
    def test_oidc_token_and_userinfo(self):
        from ui.oidc import GrooveOIDCBackend, groups_from_claims
        backend = GrooveOIDCBackend()
        for scenario in ALL:
            with self.subTest(scenario=scenario), it.integration(("oidc_token", "oidc_userinfo"), scenario):
                if scenario == OK:
                    tok = backend.get_token({"code": "c"})
                    claims = backend.get_userinfo(tok["access_token"], tok["id_token"], {})
                    self.assertEqual(groups_from_claims(claims), ["Transport"])
                else:
                    with self.assertRaises(requests.RequestException):
                        backend.get_token({"code": "c"})


@override_settings(**it.INTEGRATION_SETTINGS)
class ZariaLLMTests(SimpleTestCase):
    MODEL = SimpleNamespace(provider="anthropic", key="claude-haiku-4-5-20251001")
    MSG = [{"role": "user", "content": "Ile kartonów na palecie?"}]

    def test_anthropic_every_scenario(self):
        from ui.zaria_llm import ZariaLLMError, complete
        for scenario in ALL:
            with self.subTest(scenario=scenario), it.integration("anthropic", scenario) as http:
                if scenario == OK:
                    text, tin, tout, _ms = complete(self.MODEL, self.MSG, "system")
                    self.assertEqual((text, tin, tout), ("Na palecie EU zmieści się 48 kartonów.", 12, 9))
                    self.assertNotIn(b"sk-ant-test", http.calls[0].body)   # klucz tylko w nagłówku
                else:
                    with self.assertRaises(ZariaLLMError) as ctx:
                        complete(self.MODEL, self.MSG, "system")
                    self.assertNotIn("sk-ant", str(ctx.exception))

    def test_local_and_openai_failures_are_friendly(self):
        from ui.zaria_llm import ZariaLLMError, complete
        for provider in ("ollama", "openai", "azure_openai"):
            model = SimpleNamespace(provider=provider, key="llama3.1")
            for scenario in ALL[1:]:
                with self.subTest(provider=provider, scenario=scenario), it.integration(provider, scenario):
                    with self.assertRaises(ZariaLLMError):
                        complete(model, self.MSG, "system")

    # B-010 (naprawione): pakiet `openai` w requirements.txt
    def test_ollama_answers_when_server_ok(self):
        from ui.zaria_llm import complete
        with it.integration("ollama", "ok"):
            text, *_ = complete(SimpleNamespace(provider="ollama", key="llama3.1"), self.MSG, "")
        self.assertEqual(text, "Odpowiedź testowa.")


class SentryAndMailTests(TestCase):
    def test_sentry_fake_transport_captures_without_network(self):
        import sentry_sdk
        with it.capture_sentry() as envelopes:
            sentry_sdk.capture_message("testkit: zdarzenie")
            sentry_sdk.flush()
        self.assertEqual(len(envelopes), 1)
        self.assertIn("testkit: zdarzenie", envelopes[0].serialize().decode())

    def _send(self):
        from transport.views.mailing import _send_html_email
        req = SimpleNamespace(user=SimpleNamespace(email="planista@example.test"))
        return _send_html_email(req, ["spedycja@example.test"], "Zlecenie ąę", "tekst", "<p>html</p>")

    def test_smtp_success_is_captured_in_outbox(self):
        ok, _ = self._send()
        self.assertTrue(ok)
        self.assertEqual(mail.outbox[0].subject, "Zlecenie ąę")

    def test_smtp_failures_reported_not_raised(self):
        for scenario, error in it.SMTP_ERRORS.items():
            with self.subTest(scenario=scenario), \
                    override_settings(EMAIL_BACKEND="testkit.integrations.FailingEmailBackend"), \
                    unittest.mock.patch.object(it.FailingEmailBackend, "error", error):
                ok, info = self._send()
                self.assertFalse(ok)
                self.assertTrue(info)
