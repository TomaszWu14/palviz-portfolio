"""Audyt SEC-008: /api/chat (ZARIA) musi respektować bramkę modułu 'zaria' (rola +
nadpisanie per-użytkownik) i akceptację komunikatu RODO — tak samo jak czat WWW.
Wspólny guard (_send_guards → _access_guard) pilnuje, żeby ścieżki się nie rozjechały.
LLM zawsze zamockowany (ui.zaria_llm.complete) — nigdy realne wywołania."""
import json
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import (UserModuleAccess, ZariaConfig, ZariaConversation, ZariaMessage,
                       ZariaModel, ZariaModelRoleAccess)
from ui.roles import GROUP_ADMIN, GROUP_CLIENT

from .test_zaria import _user

RODO_MSG = "Zaakceptuj komunikat RODO przed wysłaniem wiadomości."
MODULE_MSG = "Brak dostępu do modułu ZARIA."


class _ZariaGateBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user("zgate", GROUP_ADMIN)          # rola z dostępem do modułu 'zaria'
        cls.model = ZariaModel.objects.create(
            key="claude-haiku", display_name="Haiku", provider="anthropic",
            price_input_per_1k="0.01", price_output_per_1k="0.03")
        ZariaModelRoleAccess.objects.create(model=cls.model, group_name=GROUP_ADMIN)
        cls.url = reverse("ui:zaria_api_chat")

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)

    def _post_api(self, body=None):
        body = body or {"message": "hej", "model": self.model.key}
        return self.client.post(self.url, data=json.dumps(body), content_type="application/json")

    def _set_rodo(self, text="Dane są przetwarzane przez dostawcę modelu AI."):
        cfg = ZariaConfig.load()
        cfg.rodo_notice = text
        cfg.save()


class ZariaApiModuleGateTests(_ZariaGateBase):
    """/api/chat: odebrany moduł (nadpisanie deny) lub rola bez modułu → JSON 403."""

    @patch("ui.zaria_llm.complete")
    def test_module_override_deny_returns_403(self, mock_complete):
        UserModuleAccess.objects.create(user=self.user, module_key="zaria", allowed=False)
        r = self._post_api()
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], MODULE_MSG)
        mock_complete.assert_not_called()
        # Odmowa przed czymkolwiek innym — nie powstaje nawet wątek „(API)".
        self.assertFalse(ZariaConversation.objects.filter(user=self.user).exists())

    @patch("ui.zaria_llm.complete")
    def test_role_without_module_returns_403_even_with_model_access(self, mock_complete):
        # Siatka ról modeli sama w sobie NIE otwiera modułu — liczy się bramka modułu.
        klient = _user("zgate_cli", GROUP_CLIENT)
        ZariaModelRoleAccess.objects.create(model=self.model, group_name=GROUP_CLIENT)
        self.client.force_login(klient)
        r = self._post_api()
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], MODULE_MSG)
        mock_complete.assert_not_called()

    @patch("ui.zaria_llm.complete")
    def test_denied_module_checked_before_payload_validation(self, mock_complete):
        UserModuleAccess.objects.create(user=self.user, module_key="zaria", allowed=False)
        r = self.client.post(self.url, data="to nie JSON", content_type="application/json")
        self.assertEqual(r.status_code, 403)      # nie 400 — bramka modułu pierwsza
        mock_complete.assert_not_called()

    @patch("ui.zaria_llm.complete")
    def test_module_override_allow_and_rodo_off_works(self, mock_complete):
        mock_complete.return_value = ("Odpowiedź", 3, 2, 40)
        UserModuleAccess.objects.create(user=self.user, module_key="zaria", allowed=True)
        r = self._post_api()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["reply"], "Odpowiedź")


class ZariaApiRodoGateTests(_ZariaGateBase):
    """/api/chat: skonfigurowany komunikat RODO bez akceptacji → JSON 403 (PL, jak w WWW)."""

    @patch("ui.zaria_llm.complete")
    def test_rodo_not_accepted_returns_403_with_message(self, mock_complete):
        self._set_rodo()
        r = self._post_api()
        self.assertEqual(r.status_code, 403)
        d = r.json()
        self.assertEqual(d["error"], RODO_MSG)
        # Akceptacja jak w czacie WWW — link do wątku „(API)" z komunikatem i przyciskiem.
        conv = ZariaConversation.objects.get(user=self.user, title="(API)")
        self.assertTrue(d["privacy_notice_url"].endswith(
            reverse("ui:zaria_conversation", args=[conv.pk])))
        mock_complete.assert_not_called()
        self.assertFalse(ZariaMessage.objects.filter(conversation=conv).exists())

    @patch("ui.zaria_llm.complete")
    def test_rodo_rejection_does_not_consume_rate_limit(self, mock_complete):
        mock_complete.return_value = ("ok", 1, 1, 10)
        self._set_rodo()
        cfg = ZariaConfig.load(); cfg.rate_limit_per_minute = 1; cfg.rate_limit_per_day = 100; cfg.save()
        self.assertEqual(self._post_api().status_code, 403)
        self.assertEqual(self._post_api().status_code, 403)
        ZariaConversation.objects.filter(user=self.user, title="(API)").update(
            accepted_privacy_notice_at=timezone.now())
        self.assertEqual(self._post_api().status_code, 200)   # odmowy RODO nie zjadły limitu

    @patch("ui.zaria_llm.complete")
    def test_module_and_accepted_rodo_works(self, mock_complete):
        mock_complete.return_value = ("Odpowiedź", 10, 5, 120)
        self._set_rodo()
        self.assertEqual(self._post_api().status_code, 403)
        conv = ZariaConversation.objects.get(user=self.user, title="(API)")
        # Człowiek akceptuje komunikat w wątku „(API)" (ten sam widok co czat WWW).
        r = self.client.post(reverse("ui:zaria_accept_privacy", args=[conv.pk]))
        self.assertEqual(r.status_code, 302)
        r = self._post_api()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["reply"], "Odpowiedź")
        mock_complete.assert_called_once()
        # Licznik zużycia trafia do tego samego wątku „(API)" (bez duplikatu).
        self.assertEqual(ZariaConversation.objects.filter(user=self.user, title="(API)").count(), 1)
        self.assertEqual(conv.messages.filter(role="assistant").count(), 1)


class ZariaWebSendGateUnchangedTests(_ZariaGateBase):
    """Czat WWW (zaria_send_message / SSE) — zachowanie bez zmian po wydzieleniu guardu."""

    def setUp(self):
        super().setUp()
        self.conv = ZariaConversation.objects.create(user=self.user, model=self.model)

    @patch("ui.zaria_llm.complete")
    def test_web_send_blocked_without_rodo_acceptance(self, mock_complete):
        self._set_rodo()
        r = self.client.post(reverse("ui:zaria_send_message", args=[self.conv.pk]), {"content": "hej"})
        self.assertRedirects(r, reverse("ui:zaria_conversation", args=[self.conv.pk]),
                             fetch_redirect_response=False)
        self.assertEqual([str(m) for m in get_messages(r.wsgi_request)], [RODO_MSG])
        self.assertFalse(self.conv.messages.exists())
        mock_complete.assert_not_called()

    @patch("ui.zaria_llm.complete")
    def test_web_send_after_acceptance_works(self, mock_complete):
        mock_complete.return_value = ("odp", 1, 1, 5)
        self._set_rodo()
        self.client.post(reverse("ui:zaria_accept_privacy", args=[self.conv.pk]))
        self.client.post(reverse("ui:zaria_send_message", args=[self.conv.pk]), {"content": "hej"})
        self.assertEqual(self.conv.messages.filter(role="user").count(), 1)
        mock_complete.assert_called_once()

    def test_web_send_denied_by_module_override_403(self):
        UserModuleAccess.objects.create(user=self.user, module_key="zaria", allowed=False)
        r = self.client.post(reverse("ui:zaria_send_message", args=[self.conv.pk]), {"content": "hej"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.conv.messages.exists())

    def test_stream_blocked_without_rodo_acceptance_same_message(self):
        self._set_rodo()
        r = self.client.post(reverse("ui:zaria_send_stream", args=[self.conv.pk]), {"content": "hej"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], RODO_MSG)
        self.assertFalse(self.conv.messages.exists())

    def test_shared_guard_returns_same_codes_for_all_paths(self):
        from ui.views.zaria_chat import _send_guards
        cfg = ZariaConfig.load()
        self.assertEqual(_send_guards(self.user, "hej", cfg, self.model, self.conv), (None, None))
        self._set_rodo()
        cfg = ZariaConfig.load()
        self.assertEqual(_send_guards(self.user, "hej", cfg, self.model, self.conv),
                         ("privacy_notice", RODO_MSG))
        UserModuleAccess.objects.create(user=self.user, module_key="zaria", allowed=False)
        fresh = type(self.user).objects.get(pk=self.user.pk)   # bez cache nadpisań na obiekcie
        self.assertEqual(_send_guards(fresh, "hej", cfg, self.model, self.conv),
                         ("forbidden_module", MODULE_MSG))
