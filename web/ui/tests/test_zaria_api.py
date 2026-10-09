"""ZARIA (internal AI chat) module: module gating, sending a message (mocked LLM
call), role×model access grid, rate limiting, and the budget-alert dedup."""
import importlib.util
import json
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

_HAS_OPENAI = importlib.util.find_spec("openai") is not None

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, Client
from django.urls import reverse

from ui.models import (ZariaModel, ZariaModelRoleAccess, ZariaConfig,
                       ZariaConversation, ZariaMessage, Task)
from ui.roles import GROUP_ADMIN, GROUP_MASTER_DATA

from .test_zaria import _user

class ZariaApiChatTests(TestCase):
    """POST /api/chat — kody HTTP, walidacja, limity, obcięcie historii, max_tokens, CSRF."""
    @classmethod
    def setUpTestData(cls):
        cls.user = _user("zapi", GROUP_ADMIN)
        cls.model = ZariaModel.objects.create(
            key="claude-haiku", display_name="Haiku", provider="anthropic",
            price_input_per_1k="0.01", price_output_per_1k="0.03")
        ZariaModelRoleAccess.objects.create(model=cls.model, group_name=GROUP_ADMIN)
        cls.url = reverse("ui:zaria_api_chat")

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)

    def _post(self, body):
        return self.client.post(self.url, data=json.dumps(body), content_type="application/json")

    @patch("ui.zaria_llm.complete")
    def test_success_returns_reply_and_meters_without_content(self, mock_complete):
        mock_complete.return_value = ("Odpowiedź", 10, 5, 120)
        r = self._post({"message": "Hej", "model": self.model.key})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["reply"], "Odpowiedź")
        self.assertEqual(d["usage"], {"input_tokens": 10, "output_tokens": 5, "cost_pln": d["usage"]["cost_pln"]})
        am = ZariaMessage.objects.filter(role="assistant").latest("id")
        self.assertEqual(am.prompt_tokens, 10)
        self.assertEqual(am.content, "")   # LOG_CONVERSATIONS domyślnie false → brak treści

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self._post({"message": "x"}).status_code, 401)

    def test_empty_or_too_long_message_400(self):
        cfg = ZariaConfig.load()
        self.assertEqual(self._post({"message": "   ", "model": self.model.key}).status_code, 400)
        long = "x" * (cfg.max_message_chars + 1)
        self.assertEqual(self._post({"message": long, "model": self.model.key}).status_code, 400)

    def test_forbidden_model_403(self):
        ZariaModel.objects.create(key="claude-opus", display_name="Opus", provider="anthropic")
        self.assertEqual(self._post({"message": "hej", "model": "claude-opus"}).status_code, 403)

    @patch("ui.zaria_ratelimit.timezone.now")
    @patch("ui.zaria_llm.complete")
    def test_rate_limit_429(self, mock_complete, mock_now):
        # Klucz licznika zawiera bieżącą minutę — bez zamrożenia czasu dwa POST-y po obu
        # stronach granicy minuty trafiały w różne liczniki (200 zamiast 429, flaky w CI).
        mock_now.return_value = datetime(2026, 9, 28, 8, 47, 59, 999000, tzinfo=dt_timezone.utc)
        mock_complete.return_value = ("ok", 1, 1, 10)
        cfg = ZariaConfig.load(); cfg.rate_limit_per_minute = 1; cfg.rate_limit_per_day = 100; cfg.save()
        self.assertEqual(self._post({"message": "a", "model": self.model.key}).status_code, 200)
        self.assertEqual(self._post({"message": "b", "model": self.model.key}).status_code, 429)

    @patch("ui.zaria_llm.complete")
    def test_history_trimmed_to_10_and_max_tokens_applied(self, mock_complete):
        mock_complete.return_value = ("ok", 1, 1, 10)
        hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(20)]
        r = self._post({"message": "nowa", "model": self.model.key, "history": hist})
        self.assertEqual(r.status_code, 200)
        args, kwargs = mock_complete.call_args
        sent = args[1]                       # complete(model, messages, system, max_tokens=...)
        self.assertEqual(len(sent), 10)      # obcięte do ostatnich 10
        self.assertEqual(sent[-1]["content"], "nowa")
        self.assertEqual(kwargs["max_tokens"], ZariaConfig.load().max_tokens)

    @patch("ui.zaria_llm.complete")
    def test_vendor_error_returns_502_not_500(self, mock_complete):
        from ui.zaria_llm import ZariaLLMError
        mock_complete.side_effect = ZariaLLMError("Błąd usługi AI. Spróbuj ponownie później.")
        r = self._post({"message": "hej", "model": self.model.key})
        self.assertEqual(r.status_code, 502)
        self.assertIn("AI", r.json()["error"])

    def test_csrf_enforced(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.user)
        r = c.post(self.url, data=json.dumps({"message": "x", "model": self.model.key}),
                   content_type="application/json")
        self.assertEqual(r.status_code, 403)


class ZariaSeedCommandTests(TestCase):
    """`manage.py zaria_seed` — tworzy katalog, dostęp ról i model domyślny, idempotentnie."""
    def _seed(self, *args):
        import io
        from django.core.management import call_command
        call_command("zaria_seed", *args, stdout=io.StringIO(), stderr=io.StringIO())

    def test_seed_creates_models_access_and_default(self):
        from decimal import Decimal
        self._seed("--rate", "4.0")
        self.assertEqual(ZariaModel.objects.count(), 5)   # 4 Claude + 1 Ollama
        haiku = ZariaModel.objects.get(key="claude-haiku-4-5")
        self.assertEqual(haiku.price_input_per_1k, Decimal("0.004000"))   # $1/1M × 4 / 1000
        self.assertEqual(haiku.price_output_per_1k, Decimal("0.020000"))
        self.assertEqual(ZariaConfig.load().default_model.key, "claude-sonnet-5")
        # Admin i Master Data: pełny katalog (w tym Opus/Fable — AI-002).
        self.assertEqual(ZariaModelRoleAccess.objects.filter(group_name=GROUP_ADMIN).count(), 5)
        self.assertEqual(ZariaModelRoleAccess.objects.filter(group_name=GROUP_MASTER_DATA).count(), 5)

    def test_idempotent_and_preserves_manual_prices(self):
        from decimal import Decimal
        self._seed()
        opus = ZariaModel.objects.get(key="claude-opus-4-8")
        opus.price_input_per_1k = Decimal("9.900000"); opus.save()
        self._seed()   # re-run without --force
        opus.refresh_from_db()
        self.assertEqual(opus.price_input_per_1k, Decimal("9.900000"))  # manual price kept
        self.assertEqual(ZariaModel.objects.count(), 5)   # 4 Claude + 1 Ollama                 # no duplicates
        from ui.management.commands.zaria_seed import ROLE_ACCESS
        self.assertEqual(ZariaModelRoleAccess.objects.count(),
                         sum(len(k) for k in ROLE_ACCESS.values()))  # access not duplicated

    def test_force_overwrites_prices(self):
        from decimal import Decimal
        self._seed()
        opus = ZariaModel.objects.get(key="claude-opus-4-8")
        opus.price_input_per_1k = Decimal("9.900000"); opus.save()
        self._seed("--force")
        opus.refresh_from_db()
        self.assertEqual(opus.price_input_per_1k, Decimal("0.020000"))  # reset from catalog


class ZariaTestConnectionTests(TestCase):
    """Admin „Testuj połączenie" — jedno kliknięcie wywołuje API i pokazuje wynik."""
    @classmethod
    def setUpTestData(cls):
        cls.admin = _user("ztestconn", GROUP_ADMIN)
        cls.model = ZariaModel.objects.create(
            key="claude-haiku-4-5", display_name="Haiku", provider="anthropic",
            price_input_per_1k="0.01", price_output_per_1k="0.03")
        cls.url = reverse("ui:admin_zaria_test")

    def setUp(self):
        cache.clear()
        self.client.force_login(self.admin)

    @patch("ui.zaria_llm.is_configured", return_value=True)
    @patch("ui.zaria_llm.complete", return_value=("OK", 5, 2, 90))
    def test_success_flash_and_audit(self, mock_complete, mock_cfg):
        from ui.models import ZariaAdminAudit
        r = self.client.post(self.url, {"model": self.model.id}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Połączenie OK")
        self.assertTrue(ZariaAdminAudit.objects.filter(action="test_connection").exists())

    @patch("ui.zaria_llm.is_configured", return_value=True)
    @patch("ui.zaria_llm.complete")
    def test_provider_error_flash(self, mock_complete, mock_cfg):
        from ui.zaria_llm import ZariaLLMError
        mock_complete.side_effect = ZariaLLMError("Błąd usługi AI. Spróbuj ponownie później.")
        r = self.client.post(self.url, {"model": self.model.id}, follow=True)
        self.assertContains(r, "Błąd połączenia")

    @patch("ui.zaria_llm.is_configured", return_value=False)
    def test_unconfigured_provider(self, mock_cfg):
        r = self.client.post(self.url, {"model": self.model.id}, follow=True)
        self.assertContains(r, "nie jest skonfigurowany")

    def test_no_models(self):
        ZariaModel.objects.all().delete()
        r = self.client.post(self.url, {}, follow=True)
        self.assertContains(r, "Brak modeli")

    def test_non_admin_forbidden(self):
        self.client.logout()
        self.client.force_login(_user("ztc_other", GROUP_MASTER_DATA))
        self.assertEqual(self.client.post(self.url, {"model": self.model.id}).status_code, 403)


class ZariaGlobalBudgetTests(TestCase):
    """Twardy globalny sufit PLN (P3) + alert globalny przy 80% (P5)."""
    @classmethod
    def setUpTestData(cls):
        cls.user = _user("zgb", GROUP_ADMIN)
        cls.model = ZariaModel.objects.create(
            key="claude-haiku-4-5", display_name="H", provider="anthropic",
            price_input_per_1k="0.01", price_output_per_1k="0.03")
        ZariaModelRoleAccess.objects.create(model=cls.model, group_name=GROUP_ADMIN)

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)
        self.conv = ZariaConversation.objects.create(user=self.user, model=self.model)

    def _seed_org_spend(self, amount):
        other = get_user_model().objects.create_user(f"spend_{amount}", password="x")
        c = ZariaConversation.objects.create(user=other, model=self.model)
        ZariaMessage.objects.create(conversation=c, role="assistant", model=self.model,
                                    prompt_tokens=0, completion_tokens=0, cost_pln=amount)
        cache.clear()   # bust cached org-spend

    @patch("ui.zaria_llm.complete")
    def test_global_cap_blocks_html_send(self, mock_complete):
        cfg = ZariaConfig.load(); cfg.monthly_budget_pln = 10; cfg.save()
        self._seed_org_spend(10)   # org spend == cap
        r = self.client.post(reverse("ui:zaria_send_message", args=[self.conv.pk]), {"content": "hej"})
        self.assertRedirects(r, reverse("ui:zaria_conversation", args=[self.conv.pk]))
        self.assertEqual(self.conv.messages.filter(role="user").count(), 0)   # nic nie wysłano
        mock_complete.assert_not_called()

    def test_global_cap_blocks_api_chat_403(self):
        cfg = ZariaConfig.load(); cfg.monthly_budget_pln = 10; cfg.save()
        self._seed_org_spend(10)
        r = self.client.post(reverse("ui:zaria_api_chat"),
                             data=json.dumps({"message": "hej", "model": self.model.key}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertIn("globaln", r.json()["error"].lower())

    def test_global_alert_fires_at_80pct(self):
        from ui.views.zaria import _check_global_budget_threshold
        cfg = ZariaConfig.load(); cfg.monthly_budget_pln = 10; cfg.save()
        self._seed_org_spend(8)    # 80%
        _check_global_budget_threshold()
        self.assertTrue(Task.objects.filter(dedup_key__startswith="zaria_budget_global").exists())

    def test_no_global_alert_under_80pct(self):
        from ui.views.zaria import _check_global_budget_threshold
        cfg = ZariaConfig.load(); cfg.monthly_budget_pln = 10; cfg.save()
        self._seed_org_spend(5)    # 50%
        _check_global_budget_threshold()
        self.assertFalse(Task.objects.filter(dedup_key__startswith="zaria_budget_global").exists())

    @patch("ui.zaria_llm.complete")
    def test_block_uses_hard_block_fraction(self, mock_complete):
        # Bufor (P3): blok łapie przy hard_block_fraction% capu, nie przy 100%.
        cfg = ZariaConfig.load(); cfg.monthly_budget_pln = 100; cfg.hard_block_fraction = 97; cfg.save()
        self._seed_org_spend(97)   # == próg blokady (97% capu), poniżej capu 100
        r = self.client.post(reverse("ui:zaria_send_message", args=[self.conv.pk]), {"content": "hej"})
        self.assertRedirects(r, reverse("ui:zaria_conversation", args=[self.conv.pk]))
        self.assertEqual(self.conv.messages.filter(role="user").count(), 0)
        mock_complete.assert_not_called()

    @patch("ui.zaria_llm.complete")
    def test_pass_just_below_block_fraction(self, mock_complete):
        # Tuż poniżej progu blokady (96% przy fraction 97) wysyłka przechodzi.
        mock_complete.return_value = ("odp", 1, 1, 5)
        cfg = ZariaConfig.load(); cfg.monthly_budget_pln = 100; cfg.hard_block_fraction = 97; cfg.save()
        self._seed_org_spend(96)
        r = self.client.post(reverse("ui:zaria_send_message", args=[self.conv.pk]), {"content": "hej"})
        self.assertRedirects(r, reverse("ui:zaria_conversation", args=[self.conv.pk]))
        self.assertEqual(self.conv.messages.filter(role="user").count(), 1)
        mock_complete.assert_called_once()

    def test_last_alert_fires_below_block_even_when_fraction_lowered(self):
        # P5: „ostatnie ostrzeżenie" liczone względem progu blokady — odpala poniżej blokady
        # nawet gdy admin obniży hard_block_fraction (tu 90 → próg blokady 90, alert 85.5).
        from ui.views.zaria import _check_global_budget_threshold
        cfg = ZariaConfig.load(); cfg.monthly_budget_pln = 100; cfg.hard_block_fraction = 90; cfg.save()
        self._seed_org_spend(86)   # >= 85.5 (95% progu blokady), a < 90 (blok)
        from ui.views.zaria import global_budget_exceeded
        self.assertFalse(global_budget_exceeded())   # jeszcze nie blokuje
        _check_global_budget_threshold()
        self.assertTrue(Task.objects.filter(dedup_key__startswith="zaria_budget_global_last").exists())


class ZariaPerUserPlnNoLongerBlocksTests(TestCase):
    """P13: per-user PLN nie blokuje już wysyłki — tylko ostrzega. Twardy cap = globalny."""
    @classmethod
    def setUpTestData(cls):
        cls.user = _user("zpu", GROUP_ADMIN)
        cls.model = ZariaModel.objects.create(
            key="claude-haiku-4-5", display_name="H", provider="anthropic",
            price_input_per_1k="0.01", price_output_per_1k="0.03")
        ZariaModelRoleAccess.objects.create(model=cls.model, group_name=GROUP_ADMIN)

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)
        self.conv = ZariaConversation.objects.create(user=self.user, model=self.model)

    @patch("ui.zaria_llm.complete")
    def test_over_per_user_budget_still_sends(self, mock_complete):
        mock_complete.return_value = ("odp", 1, 1, 5)
        cfg = ZariaConfig.load()
        cfg.monthly_budget_pln = 0          # brak globalnego capu
        cfg.per_user_monthly_budget_pln = 1  # user grubo ponad — kiedyś blokowało
        cfg.hard_block_over_budget = True    # martwa flaga — nie może już blokować
        cfg.save()
        ZariaMessage.objects.create(conversation=self.conv, role="assistant", model=self.model,
                                    prompt_tokens=0, completion_tokens=0, cost_pln=5)
        r = self.client.post(reverse("ui:zaria_send_message", args=[self.conv.pk]), {"content": "hej"})
        self.assertRedirects(r, reverse("ui:zaria_conversation", args=[self.conv.pk]))
        self.assertEqual(self.conv.messages.filter(role="user").count(), 1)
        mock_complete.assert_called_once()


class ZariaActivateCapMigrationTests(TestCase):
    """Migracja danych 0101 ożywia cap na singletonie tylko gdy wciąż domyślny."""
    def _run(self):
        from importlib import import_module
        from django.apps import apps as global_apps
        mod = import_module("ui.migrations.0101_zaria_activate_cap")
        mod.activate_cap(global_apps, None)

    def test_activates_when_zero(self):
        cfg = ZariaConfig.load()
        cfg.monthly_budget_pln = 0; cfg.per_user_monthly_budget_pln = 0
        cfg.default_monthly_token_budget = 500000; cfg.save()
        self._run()
        cfg.refresh_from_db()
        self.assertEqual(float(cfg.monthly_budget_pln), 4500.0)
        self.assertEqual(float(cfg.per_user_monthly_budget_pln), 20.0)
        self.assertEqual(cfg.default_monthly_token_budget, 800000)

    def test_preserves_admin_values(self):
        cfg = ZariaConfig.load()
        cfg.monthly_budget_pln = 999; cfg.per_user_monthly_budget_pln = 50
        cfg.default_monthly_token_budget = 123456; cfg.save()
        self._run()
        cfg.refresh_from_db()
        self.assertEqual(float(cfg.monthly_budget_pln), 999.0)
        self.assertEqual(float(cfg.per_user_monthly_budget_pln), 50.0)
        self.assertEqual(cfg.default_monthly_token_budget, 123456)


class ZariaCompareLocalVsCloudTests(TestCase):
    """Tryb porównania = lokalny vs chmura: A tylko Ollama, B tylko chmurowe."""
    @classmethod
    def setUpTestData(cls):
        from ui.models import ZariaRolePermission
        cls.admin = _user("zcmp", GROUP_ADMIN)
        cls.local = ZariaModel.objects.create(key="llama3.1", display_name="Llama lokalnie",
                                              provider="ollama")
        cls.cloud = ZariaModel.objects.create(key="claude-haiku-4-5", display_name="Haiku",
                                              provider="anthropic")
        for m in (cls.local, cls.cloud):
            ZariaModelRoleAccess.objects.create(model=m, group_name=GROUP_ADMIN)
        ZariaRolePermission.objects.create(group_name=GROUP_ADMIN, can_compare=True)

    def setUp(self):
        cache.clear()
        self.client.force_login(self.admin)

    def test_home_splits_models_local_vs_cloud(self):
        r = self.client.get(reverse("ui:zaria_home"))
        self.assertEqual([m.id for m in r.context["models_local"]], [self.local.id])
        self.assertEqual([m.id for m in r.context["models_cloud"]], [self.cloud.id])
        self.assertContains(r, "Model A (lokalny)")
        self.assertContains(r, "Model B (chmurowy)")

    def test_compare_local_a_cloud_b_creates_conversation(self):
        r = self.client.post(reverse("ui:zaria_new_conversation"),
                             {"compare": "1", "model_a": self.local.id, "model_b": self.cloud.id})
        conv = ZariaConversation.objects.latest("pk")
        self.assertRedirects(r, reverse("ui:zaria_conversation", args=[conv.pk]))
        self.assertEqual(conv.model_id, self.local.id)      # A = lokalny
        self.assertEqual(conv.model_b_id, self.cloud.id)    # B = chmurowy

    def test_compare_rejects_cloud_as_model_a(self):
        n0 = ZariaConversation.objects.count()
        r = self.client.post(reverse("ui:zaria_new_conversation"),
                             {"compare": "1", "model_a": self.cloud.id, "model_b": self.local.id})
        self.assertRedirects(r, reverse("ui:zaria_home"))
        self.assertEqual(ZariaConversation.objects.count(), n0)   # nic nie powstało
