"""Etap 1 — fabryki, persony i seed: dane, na których staną testy uprawnień/IDOR/E2E.

Testy person mają tag ``persona`` → ``make test-role ROLE=transport`` (GROOVE_TEST_ROLE)
zawęża je do jednej persony.
"""
import os
import unittest
import unittest.mock
from io import StringIO

import yaml
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings, tag

from core.platform_modules import modules_for
from core.roles import ALL_GROUPS
from testkit import factories as f
from testkit import personas
from testkit.seed import HU_STATUSES, SHIPMENT_STATUSES, UKRAINE_SETTINGS, ZONES, SeedDataMixin

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class PersonaCatalogTests(TestCase):
    def test_permissions_yaml_personas_all_exist(self):
        with open(os.path.join(REPO, "tests", "permissions.yaml"), encoding="utf-8") as fh:
            spec = yaml.safe_load(fh)
        self.assertLessEqual(set(spec["persony"]), set(personas.PERSONAS))
        self.assertEqual(len(personas.PERSONAS), 15)      # 13 z macierzy + zablokowany + nadpisanie

    def test_slugs_and_names_resolve_unknown_lists_choices(self):
        self.assertEqual(personas.resolve("optymalizacja"), "Optymalizacja kartonów")
        self.assertEqual(personas.resolve("Podgląd"), "Podgląd")
        with self.assertRaisesMessage(ValueError, "kontrola_hu"):
            personas.resolve("kierownik")

    def test_selected_follows_env(self):
        with unittest.mock.patch.dict(os.environ, {"GROOVE_TEST_ROLE": "lider"}):
            self.assertEqual(personas.selected(), ["Lider kontroli"])
        with unittest.mock.patch.dict(os.environ, {"GROOVE_TEST_ROLE": ""}):
            self.assertEqual(len(personas.selected()), 15)


@tag("persona")
class PersonaBehaviourTests(TestCase):
    def test_each_selected_persona_builds_and_logs_in_as_expected(self):
        for name in personas.selected():
            with self.subTest(persona=name):
                user = personas.make(name)
                self.assertEqual(getattr(personas.make(name), "pk", None), getattr(user, "pk", None))
                r = personas.client_for(name).get("/")
                if name in ("anon", "nieaktywny", "zablokowany"):
                    self.assertEqual(r.status_code, 302)
                    self.assertIn("/login/", r["Location"])
                else:
                    self.assertIn(r.status_code, (200, 302))
                    self.assertNotIn("/login/", r.get("Location", ""))
                if name in ALL_GROUPS:
                    self.assertEqual([g.name for g in user.groups.all()], [name])

    def test_inactive_persona_cannot_log_in_via_form(self):
        personas.make("nieaktywny")
        r = personas.login_form(self.client, "nieaktywny")
        self.assertEqual(r.status_code, 200)                 # formularz z błędem, bez sesji
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_axes_locked_persona_refused_even_with_correct_password(self):
        client = personas.client_for("zablokowany")
        r = personas.login_form(client, "zablokowany")
        self.assertEqual(r.status_code, 429)
        self.assertNotIn("_auth_user_id", client.session)

    # B-009 (naprawione): blokada per login+IP — inni z tego samego adresu logują się normalnie
    def test_axes_lock_of_one_user_does_not_block_others_from_same_ip(self):
        personas.make("zablokowany")
        personas.make("Transport")
        from django.test import Client
        r = personas.login_form(Client(REMOTE_ADDR=personas.LOCKED_IP), "Transport")
        self.assertEqual(r.status_code, 302)

    def test_five_failures_still_lock_that_login(self):
        from django.test import Client
        personas.make("Transport")
        client = Client(REMOTE_ADDR="10.66.0.7")
        for _ in range(5):
            client.post("/login/", {"username": personas.username("Transport"), "password": "zle"})
        self.assertEqual(personas.login_form(client, "Transport").status_code, 429)

    def test_module_override_grants_module_without_role(self):
        user = personas.make("nadpisanie_modulu")
        self.assertFalse(user.groups.exists())
        self.assertIn("transport", [m.key for m in modules_for(user)])
        self.assertNotIn("transport", [m.key for m in modules_for(personas.make("bez_roli"))])


class FactoryEdgeTests(TestCase):
    def test_polish_and_max_length_text_round_trip(self):
        p = f.ProductFactory(name=f.long_text(250))
        c = f.CustomerFactory(name="Łódź — Świętokrzyska ąęśćźżółń ĄĘŚĆŹŻÓŁŃ")
        p.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual(len(p.name), 250)
        self.assertIn("ĄĘŚĆŹŻÓŁŃ", c.name)

    def test_factories_repeatable_in_one_test(self):
        a, b = f.HandlingUnitItemFactory(), f.HandlingUnitItemFactory()
        self.assertNotEqual(a.hu.code, b.hu.code)
        self.assertEqual(f.UserFactory(username="x").pk, f.UserFactory(username="x").pk)


class SeedTests(SeedDataMixin, TestCase):
    def test_every_persona_present(self):
        self.assertEqual(set(self.data.users), set(personas.PERSONAS))
        self.assertIsNone(self.data.users["anon"])

    def test_domain_coverage(self):
        from huctl.models import HandlingUnit
        from transport.models import Shipment
        d = self.data
        self.assertLessEqual(set(SHIPMENT_STATUSES), set(Shipment.objects.values_list("status", flat=True)))
        grid = set(HandlingUnit.objects.exclude(shipment__is_stock=True)
                   .values_list("status", "warehouse_type"))
        self.assertEqual(grid, {(s, z) for s in HU_STATUSES for z in ZONES})
        self.assertTrue(d.hu.reserved.assigned_to and d.hu.reserved.called_at and d.hu.reserved.is_priority)
        self.assertTrue(d.hu.vip_shipment.customer.is_vip)
        self.assertTrue(any(n.requires_ack for n in d.comms.notifications))
        from wh3d.models import WarehouseSnapshotRow
        self.assertEqual(WarehouseSnapshotRow.objects.filter(snapshot=d.warehouse.snapshot).count(), 24)
        self.assertEqual(len(d.warehouse.racks), 4)
        self.assertEqual(d.warehouse.ewm_tasks[0].batch_id, d.warehouse.task_batch.pk)
        self.assertEqual(len(d.catalog.materials), 4)
        self.assertTrue(any(r.alert_email for r in d.customers.rules))

    def test_dates_cover_boundaries(self):
        from transport.models import Shipment
        dates = set(Shipment.objects.values_list("outbound_delivery_date", flat=True))
        for key in ("przeszla", "dzis", "przyszla", "koniec_roku"):
            self.assertIn(self.data.dates[key], dates, key)
        from wh3d.models import WarehouseTask
        stamps = list(WarehouseTask.objects.order_by("confirmed_at").values_list("confirmed_at", flat=True))
        by_kind = dict(WarehouseTask.objects.values_list("kind", "confirmed_at"))
        # 02:30 wystąpiło dwa razy (jesienna zmiana czasu) — w bazie to dwa momenty odległe o 1 h
        self.assertEqual((by_kind["putaway"] - by_kind["picking"]).total_seconds(), 3600)
        self.assertEqual(len(stamps), 4)

    def test_zaria_conversations_are_per_user(self):
        from ui.models import ZariaConversation
        md, tr = self.data.users["Master Data"], self.data.users["Transport"]
        self.assertEqual(ZariaConversation.objects.filter(user=md).count(), 1)
        self.assertNotEqual(self.data.zaria.conversations["Master Data"].pk,
                            self.data.zaria.conversations["Transport"].pk)
        self.assertFalse(ZariaConversation.objects.filter(user=tr, title__contains="Master").exists())

    def test_ukraine_stock_split_acme_dlt(self):
        from ui.views.ukraine import stock_by_batch
        with self.settings(**UKRAINE_SETTINGS):
            got = stock_by_batch({("REF100001", "LOT0000001")})
        self.assertEqual(got[("REF100001", "LOT0000001")], {"acme": 60.0, "dlt": 30.0, "other": 5.0})

    def test_seeded_data_renders_key_screens(self):
        client = personas.client_for("superuser")
        for url in ("/", "/control/", "/planner/products/"):
            with self.subTest(url=url):
                self.assertLess(client.get(url).status_code, 400)


class SeedCommandTests(TestCase):
    @override_settings(DEBUG=False)
    def test_refuses_without_debug(self):
        with self.assertRaises(CommandError):
            call_command("seed_testdata")

    @override_settings(DEBUG=True)
    def test_seeds_once_then_skips(self):
        out = StringIO()
        call_command("seed_testdata", stdout=out)
        self.assertIn("Zasiano", out.getvalue())
        n = get_user_model().objects.count()
        call_command("seed_testdata", stdout=out)
        self.assertIn("pomijam", out.getvalue())
        self.assertEqual(get_user_model().objects.count(), n)
