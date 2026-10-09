"""Smoke: zredesignowane ekrany ZARIA renderują się (home + conversation) w PL i EN,
oraz endpoint /prefs/ zapisuje motyw i język. Usuwalny po fazie UI, ale tani strażnik."""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.contrib.auth.models import User, Group
from django.urls import reverse

from ui.roles import GROUP_ADMIN
from ui.models import ZariaModel, ZariaModelRoleAccess, ZariaConversation, ZariaMessage


class ZariaRedesignSmoke(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user("zredz", password="x")
        cls.u.groups.add(Group.objects.get_or_create(name=GROUP_ADMIN)[0])
        cls.model = ZariaModel.objects.create(
            key="claude-sonnet-4-5", display_name="Claude Sonnet", provider="anthropic",
            price_input_per_1k="0.01", price_output_per_1k="0.03")
        ZariaModelRoleAccess.objects.create(model=cls.model, group_name=GROUP_ADMIN)
        cls.conv = ZariaConversation.objects.create(user=cls.u, model=cls.model, title="Test")
        ZariaMessage.objects.create(conversation=cls.conv, role="user", content="Cześć")
        ZariaMessage.objects.create(conversation=cls.conv, role="assistant", content="Witaj",
                                    model=cls.model, prompt_tokens=10, completion_tokens=5)

    def setUp(self):
        self.client.force_login(self.u)

    def test_home_renders(self):
        r = self.client.get(reverse("ui:zaria_home"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "z-newbtn")          # nowy layout (sidebar)
        self.assertContains(r, "Rozpocznij rozmowę")  # PL domyślnie

    def test_conversation_renders_and_context_panel(self):
        r = self.client.get(reverse("ui:zaria_conversation", args=[self.conv.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "z-ctx")             # panel kontekstu
        self.assertContains(r, "z-usage__bar")      # sygnaturowy pasek zużycia

    def test_english_switch(self):
        # Ustaw język przez /prefs/, potem strona powinna zawierać angielskie etykiety.
        r = self.client.post(reverse("ui:set_prefs"), {"lang": "en"})
        self.assertEqual(r.status_code, 200)
        r2 = self.client.get(reverse("ui:zaria_home"))
        self.assertContains(r2, "Start conversation")
        self.u.refresh_from_db()
        self.assertEqual(self.u.profile.ui_lang, "en")

    def test_prefs_theme_saved(self):
        self.client.post(reverse("ui:set_prefs"), {"theme": "light"})
        self.u.refresh_from_db()
        self.assertEqual(self.u.profile.ui_theme, "light")

    def test_new_conversation_empty_template_ok(self):
        # Pusty select szablonu wysyła template="" — nie może wywalić tworzenia rozmowy
        # (na PostgreSQL filter(pk="") rzucał 500; tu walidujemy że przechodzi i bez szablonu).
        r = self.client.post(reverse("ui:zaria_new_conversation"),
                             {"model": str(self.model.id), "template": ""})
        self.assertEqual(r.status_code, 302)
        conv = ZariaConversation.objects.filter(user=self.u).order_by("-id").first()
        self.assertIsNotNone(conv)
        self.assertIsNone(conv.system_prompt_template)

    def test_user_panel_renders(self):
        r = self.client.get(reverse("ui:zaria_user_panel"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Twoje modele")
        self.assertContains(r, "Claude Sonnet")

    def test_base_links_app_css_and_styleguide(self):
        # Po wyniesieniu CSS z base.html do app.css — strona wciąż linkuje arkusz,
        # a /ui/ (żywy podgląd systemu designu) renderuje komponenty.
        r = self.client.get(reverse("ui:zaria_home"))
        self.assertContains(r, "ui/css/app.css")
        self.assertContains(r, "ui/js/app.js")
        r2 = self.client.get(reverse("ui:ui_styleguide"))
        self.assertEqual(r2.status_code, 200)
        self.assertContains(r2, "status-badge")
        self.assertContains(r2, "gv-modal")
        # Launcher Ctrl+K (Etap 3) obecny w każdym module (base.html) dla zalogowanego.
        self.assertContains(r, "gv-launcher")
        self.assertContains(r, "gv-launcher-input")

    def test_conv_save_pin_delete_actions(self):
        conv = ZariaConversation.objects.create(user=self.u, model=self.model, title="Wątek")
        url = reverse("ui:zaria_conv_action", args=[conv.pk])
        self.assertTrue(self.client.post(url, {"action": "save"}).json()["is_saved"])
        conv.refresh_from_db(); self.assertTrue(conv.is_saved)
        self.assertTrue(self.client.post(url, {"action": "pin"}).json()["pinned"])
        self.client.post(url, {"action": "rename", "title": "Nowa"})
        conv.refresh_from_db(); self.assertEqual(conv.title, "Nowa")
        self.assertTrue(self.client.post(url, {"action": "delete"}).json()["deleted"])
        self.assertFalse(ZariaConversation.objects.filter(pk=conv.pk).exists())

    def test_mail_prepare_logs_and_footer(self):
        from ui.models import ZariaMailLog, ZariaMessage
        conv = ZariaConversation.objects.create(user=self.u, model=self.model, title="Wątek")
        ZariaMessage.objects.create(conversation=conv, role="user", content="Pytanie")
        ZariaMessage.objects.create(conversation=conv, role="assistant", content="Odpowiedź", model=self.model)
        # .eml → plik + log; stopka nienegocjowalna obecna
        r = self.client.post(reverse("ui:zaria_mail", args=[conv.pk]),
                             {"scope": "last", "to": "a@b.pl", "subject": "Test", "fmt": "eml"})
        self.assertEqual(r["Content-Type"], "message/rfc822")
        self.assertIn(b"ZARIA", r.content)
        self.assertIn(b"weryfikacji", r.content)   # stopka
        log = ZariaMailLog.objects.filter(conversation=conv).first()
        self.assertEqual((log.to, log.transport, log.scope), ("a@b.pl", "eml", "last"))

    def test_export_markdown(self):
        conv = ZariaConversation.objects.create(user=self.u, model=self.model, title="Eksport")
        from ui.models import ZariaMessage
        ZariaMessage.objects.create(conversation=conv, role="assistant", content="Treść", model=self.model)
        r = self.client.get(reverse("ui:zaria_export", args=[conv.pk, "md"]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"# Eksport", r.content)
        self.assertIn(b"ZARIA", r.content)

    def test_template_applied_to_system_prompt(self):
        from ui.models import ZariaSystemPromptTemplate, ZariaConfig
        from ui.views.zaria import _system_prompt_with_rag
        tpl = ZariaSystemPromptTemplate.objects.create(name="ACME", body="Jesteś asystentem ACME.")
        cfg = ZariaConfig.load(); cfg.rag_enabled = False; cfg.save()
        sp = _system_prompt_with_rag(self.model, cfg, "hej", tpl)
        self.assertEqual(sp, "Jesteś asystentem ACME.")

    def test_retention_purges_unsaved_only(self):
        from datetime import timedelta
        from django.utils import timezone
        from ui.models import ZariaConfig
        from ui.tasks import zaria_purge_old_conversations
        cfg = ZariaConfig.load(); cfg.retention_days = 30; cfg.save()
        old_unsaved = ZariaConversation.objects.create(user=self.u, model=self.model)
        old_saved = ZariaConversation.objects.create(user=self.u, model=self.model, is_saved=True)
        cutoff = timezone.now() - timedelta(days=40)
        ZariaConversation.objects.filter(pk__in=[old_unsaved.pk, old_saved.pk]).update(updated_at=cutoff)
        res = zaria_purge_old_conversations()
        self.assertEqual(res["deleted"], 1)
        self.assertFalse(ZariaConversation.objects.filter(pk=old_unsaved.pk).exists())
        self.assertTrue(ZariaConversation.objects.filter(pk=old_saved.pk).exists())


class ZariaStreamTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user("zstream", password="x")
        cls.u.groups.add(Group.objects.get_or_create(name=GROUP_ADMIN)[0])
        cls.model = ZariaModel.objects.create(
            key="claude-sonnet-4-5", display_name="Claude Sonnet", provider="anthropic",
            price_input_per_1k="0.01", price_output_per_1k="0.03")
        ZariaModelRoleAccess.objects.create(model=cls.model, group_name=GROUP_ADMIN)

    def setUp(self):
        cache.clear()
        self.client.force_login(self.u)
        self.conv = ZariaConversation.objects.create(user=self.u, model=self.model)

    @patch("ui.zaria_llm.stream_complete")
    def test_stream_persists_reply_and_tokens(self, mock_stream):
        mock_stream.return_value = iter([("delta", "Cześć"), ("delta", " świecie"), ("done", 12, 7)])
        r = self.client.post(reverse("ui:zaria_send_stream", args=[self.conv.pk]), {"content": "Hej"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "text/event-stream")
        body = b"".join(r.streaming_content).decode()   # konsumpcja → generator zapisuje
        self.assertIn('"delta": "Cze', body)
        self.assertIn('"done": true', body)
        msgs = list(self.conv.messages.order_by("created_at"))
        self.assertEqual([m.role for m in msgs], ["user", "assistant"])
        ai = msgs[1]
        self.assertEqual(ai.content, "Cześć świecie")
        self.assertEqual((ai.prompt_tokens, ai.completion_tokens), (12, 7))

    @patch("ui.zaria_llm.stream_complete")
    def test_stream_unexpected_error_persists_partial_and_reports(self, mock_stream):
        """Błąd mid-stream spoza ZariaLLMError NIE znika po cichu: event error do klienta
        (bez wycieku surowego wyjątku) + zapis częściowej odpowiedzi z detalem w .error."""
        def _boom(*a, **k):
            yield ("delta", "częściowa")
            raise RuntimeError("nagły błąd SDK")
        mock_stream.side_effect = _boom
        r = self.client.post(reverse("ui:zaria_send_stream", args=[self.conv.pk]), {"content": "Hej"})
        self.assertEqual(r.status_code, 200)
        body = b"".join(r.streaming_content).decode()
        self.assertIn('"error"', body)                 # użytkownik dostaje błąd, nie urwany strumień
        self.assertNotIn("nagły błąd SDK", body)       # surowy wyjątek nie wycieka do klienta
        ai = self.conv.messages.get(role="assistant")
        self.assertEqual(ai.content, "częściowa")      # częściowa odpowiedź zapisana
        self.assertIn("RuntimeError", ai.error)        # detal zachowany w logu wiadomości

    def test_stream_forbidden_model_403(self):
        ungranted = ZariaModel.objects.create(key="opus", display_name="Opus", provider="anthropic")
        conv = ZariaConversation.objects.create(user=self.u, model=ungranted)
        r = self.client.post(reverse("ui:zaria_send_stream", args=[conv.pk]), {"content": "hej"})
        self.assertEqual(r.status_code, 403)


class ZariaAccessExpiryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User, Group
        cls.u = User.objects.create_user("zexp", password="x")
        cls.u.groups.add(Group.objects.get_or_create(name="Podgląd")[0])   # rola bez dostępu do modelu
        cls.model = ZariaModel.objects.create(key="opus-req", display_name="Opus", provider="anthropic")

    def test_expired_grant_is_ignored(self):
        from datetime import timedelta
        from django.utils import timezone
        from ui.models import ZariaUserModelAccess
        from ui.views.zaria import _accessible_models
        acc = ZariaUserModelAccess.objects.create(user=self.u, model=self.model, allowed=True,
                                                  expires_at=timezone.now() + timedelta(days=1))
        self.assertIn(self.model, _accessible_models(self.u))   # aktywny grant
        acc.expires_at = timezone.now() - timedelta(minutes=1); acc.save()
        self.assertNotIn(self.model, _accessible_models(self.u))  # wygasły → brak dostępu

    def test_can_compare_permission(self):
        from ui.models import ZariaRolePermission
        from ui.views.zaria import can_compare
        self.assertFalse(can_compare(self.u))
        ZariaRolePermission.objects.create(group_name="Podgląd", can_compare=True)
        self.assertTrue(can_compare(self.u))


class ZariaCompareTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from ui.models import ZariaRolePermission
        cls.u = User.objects.create_user("zcmp", password="x")
        g = Group.objects.get_or_create(name=GROUP_ADMIN)[0]
        cls.u.groups.add(g)
        ZariaRolePermission.objects.create(group_name=GROUP_ADMIN, can_compare=True)
        cls.a = ZariaModel.objects.create(key="m-a", display_name="Model A", provider="anthropic",
                                          price_input_per_1k="0.01", price_output_per_1k="0.02")
        cls.b = ZariaModel.objects.create(key="m-b", display_name="Model B", provider="anthropic",
                                          price_input_per_1k="0.01", price_output_per_1k="0.02")
        for m in (cls.a, cls.b):
            ZariaModelRoleAccess.objects.create(model=m, group_name=GROUP_ADMIN)

    def setUp(self):
        cache.clear()
        self.client.force_login(self.u)
        self.conv = ZariaConversation.objects.create(
            user=self.u, model=self.a, model_b=self.b, compare_mode=True)

    def _stream(self, group, side):
        return self.client.post(reverse("ui:zaria_compare_stream", args=[self.conv.pk]),
                                {"group": group, "side": side})

    @patch("ui.zaria_llm.stream_complete")
    def test_full_compare_flow(self, mock_stream):
        # start → tworzy wiadomość usera + zwraca group i oba modele
        r = self.client.post(reverse("ui:zaria_compare_start", args=[self.conv.pk]), {"content": "Porównaj to"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        group = data["group"]
        self.assertEqual(data["a"]["name"], "Model A")
        self.assertEqual(data["b"]["name"], "Model B")
        self.assertTrue(self.conv.messages.filter(compare_group=group, role="user").exists())

        # dwa strumienie (a i b) → dwa warianty asystenta
        mock_stream.side_effect = lambda *a, **k: iter([("delta", "Odp A"), ("done", 5, 3)])
        b"".join(self._stream(group, "a").streaming_content)
        mock_stream.side_effect = lambda *a, **k: iter([("delta", "Odp B"), ("done", 6, 4)])
        b"".join(self._stream(group, "b").streaming_content)
        variants = {m.variant: m for m in self.conv.messages.filter(compare_group=group, role="assistant")}
        self.assertEqual(variants["a"].content, "Odp A")
        self.assertEqual(variants["b"].content, "Odp B")

        # pick a → b odrzucony, wątek kontynuuje modelem A, koniec porównania
        r = self.client.post(reverse("ui:zaria_compare_pick", args=[self.conv.pk]), {"group": group, "side": "a"})
        self.assertEqual(r.status_code, 200)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.model_id, self.a.id)
        self.assertFalse(self.conv.compare_mode)
        self.assertTrue(self.conv.messages.get(compare_group=group, variant="b").rejected)
        self.assertFalse(self.conv.messages.get(compare_group=group, variant="a").rejected)

    def test_compare_page_renders_two_columns(self):
        from ui.models import ZariaMessage
        g = "abc123"
        ZariaMessage.objects.create(conversation=self.conv, role="user", content="Q", compare_group=g)
        ZariaMessage.objects.create(conversation=self.conv, role="assistant", content="Odp A",
                                    model=self.a, variant="a", compare_group=g)
        ZariaMessage.objects.create(conversation=self.conv, role="assistant", content="Odp B",
                                    model=self.b, variant="b", compare_group=g)
        r = self.client.get(reverse("ui:zaria_conversation", args=[self.conv.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "z-cmp")
        self.assertContains(r, "Odp A")
        self.assertContains(r, "Odp B")
        self.assertContains(r, "Wyślij do obu")

    def test_compare_requires_permission(self):
        from ui.models import ZariaRolePermission
        ZariaRolePermission.objects.filter(group_name=GROUP_ADMIN).update(can_compare=False)
        u2 = User.objects.create_user("nocmp", password="x")
        u2.groups.add(Group.objects.get_or_create(name="Podgląd")[0])
        conv = ZariaConversation.objects.create(user=u2, model=self.a, model_b=self.b, compare_mode=True)
        self.client.force_login(u2)
        r = self.client.post(reverse("ui:zaria_compare_start", args=[conv.pk]), {"content": "x"})
        self.assertEqual(r.status_code, 403)
