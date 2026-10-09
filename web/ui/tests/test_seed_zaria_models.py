"""Scalanie duplikatu klucza Haiku przez `zaria_seed` (dawny seed_zaria_models
tworzył wiersz z kluczem datowanym). Reszta testów seeda: test_zaria.ZariaSeedCommandTests."""
import io

from django.core.management import call_command
from django.test import TestCase

from ui.models import ZariaModel


def _seed(*args):
    call_command("zaria_seed", *args, stdout=io.StringIO(), stderr=io.StringIO())


class ZariaSeedHaikuDedupTest(TestCase):
    def test_legacy_dated_haiku_key_is_renamed(self):
        ZariaModel.objects.create(key="claude-haiku-4-5-20251001", display_name="Claude Haiku 4.5",
                                  provider="anthropic")
        _seed()
        self.assertFalse(ZariaModel.objects.filter(key="claude-haiku-4-5-20251001").exists())
        self.assertEqual(ZariaModel.objects.filter(key="claude-haiku-4-5").count(), 1)

    def test_legacy_sonnet_45_renamed_to_sonnet_5(self):
        ZariaModel.objects.create(key="claude-sonnet-4-5", display_name="Claude Sonnet",
                                  provider="anthropic")
        _seed()
        self.assertFalse(ZariaModel.objects.filter(key="claude-sonnet-4-5").exists())
        row = ZariaModel.objects.get(key="claude-sonnet-5")
        self.assertEqual(row.display_name, "Claude Sonnet 5")

    def test_legacy_dated_haiku_deactivated_when_alias_exists(self):
        ZariaModel.objects.create(key="claude-haiku-4-5", display_name="Claude Haiku 4.5",
                                  provider="anthropic")
        ZariaModel.objects.create(key="claude-haiku-4-5-20251001", display_name="Claude Haiku 4.5",
                                  provider="anthropic")
        _seed()
        legacy = ZariaModel.objects.get(key="claude-haiku-4-5-20251001")
        self.assertFalse(legacy.is_active)
        self.assertEqual(ZariaModel.objects.filter(key="claude-haiku-4-5", is_active=True).count(), 1)


class ZariaSeedOllamaTest(TestCase):
    def test_seed_creates_active_ollama_model_with_role_access(self):
        from ui.models import ZariaModelRoleAccess
        _seed()
        row = ZariaModel.objects.get(key="llama3.2:3b")
        self.assertEqual(row.provider, "ollama")
        self.assertTrue(row.is_active)
        self.assertEqual(float(row.price_input_per_1k), 0.0)   # samohostowany — koszt 0
        self.assertTrue(ZariaModelRoleAccess.objects.filter(model=row).exists())

    def test_seed_idempotent_for_ollama(self):
        _seed(); _seed()
        self.assertEqual(ZariaModel.objects.filter(key="llama3.2:3b").count(), 1)


class ZariaSeedOllamaRenameTest(TestCase):
    def test_legacy_llama31_renamed_to_3b(self):
        ZariaModel.objects.create(key="llama3.1", display_name="Llama 3.1 (lokalnie)",
                                  provider="ollama")
        _seed()
        self.assertFalse(ZariaModel.objects.filter(key="llama3.1").exists())
        row = ZariaModel.objects.get(key="llama3.2:3b")
        self.assertEqual(row.display_name, "Llama 3.2 3B (lokalnie)")


class ZariaSeedAuditTest(TestCase):
    """Audyt AI-001 (cena Sonnet 5) i AI-002 (Fable/Opus tylko Admin + Master Data)."""

    def test_sonnet_5_price_2_10_usd_per_1m(self):
        from decimal import Decimal
        _seed("--rate", "4.0")
        row = ZariaModel.objects.get(key="claude-sonnet-5")
        self.assertEqual(row.price_input_per_1k, Decimal("0.008000"))    # $2/1M × 4 / 1000
        self.assertEqual(row.price_output_per_1k, Decimal("0.040000"))   # $10/1M × 4 / 1000

    def test_premium_models_only_for_admin_and_master_data(self):
        from ui.roles import GROUP_ADMIN, GROUP_WAREHOUSE
        from ui.views.zaria_budget import _accessible_models
        from .test_zaria import _user
        _seed()
        mag = {m.key for m in _accessible_models(_user("zmag", GROUP_WAREHOUSE))}
        adm = {m.key for m in _accessible_models(_user("zadm", GROUP_ADMIN))}
        self.assertNotIn("claude-fable-5", mag)
        self.assertNotIn("claude-opus-4-8", mag)
        self.assertLessEqual({"claude-haiku-4-5", "claude-sonnet-5", "llama3.2:3b"}, mag)
        self.assertIn("claude-fable-5", adm)

    def test_force_revokes_premium_from_narrow_roles_but_plain_seed_does_not(self):
        from ui.models import ZariaModelRoleAccess
        from ui.roles import GROUP_ADMIN, GROUP_WAREHOUSE
        _seed()
        fable = ZariaModel.objects.get(key="claude-fable-5")
        ZariaModelRoleAccess.objects.create(model=fable, group_name=GROUP_WAREHOUSE)  # stary grant
        _seed()   # bez --force: granty zostają
        self.assertTrue(ZariaModelRoleAccess.objects.filter(
            model=fable, group_name=GROUP_WAREHOUSE).exists())
        _seed("--force")
        self.assertFalse(ZariaModelRoleAccess.objects.filter(
            model=fable, group_name=GROUP_WAREHOUSE).exists())
        self.assertTrue(ZariaModelRoleAccess.objects.filter(
            model=fable, group_name=GROUP_ADMIN).exists())
