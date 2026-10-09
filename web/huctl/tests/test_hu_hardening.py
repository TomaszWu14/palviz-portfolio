"""Hardening + funkcjonalność Kontroli HU: idempotencja, walidacja, inwarianty,
guard rekontroli z audytu, dyspozycja 'escaped', routing lidera, rotacja tokena API."""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from datetime import timedelta

from ui.models import (Product, Shipment, ShipmentLine, PalletizationInstruction,
                       HandlingUnit, HandlingUnitItem, HUControlAttempt, HUQualityIssue,
                       HUStatusEvent, Customer, Task, Notification)
from ui.roles import ALL_GROUPS, GROUP_CONTROLLER, GROUP_LEADER
from huctl.views.hu import _generate_handling_units


def _ctrl_user():
    u = get_user_model().objects.create_user(username="ctrlh", password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class HUWritePathHardeningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _ctrl_user()
        cls.sh = Shipment.objects.create(name="Dostawa H", recipient_name="ODB")
        p = Product.objects.create(code="P1", name="Prod")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=10, demand_pcs=100, is_active=True)
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=8, unit="kar", source_unit="OP")
        _generate_handling_units(cls.sh)
        cls.hu = cls.sh.handling_units.first()
        cls.it = cls.hu.items.first()

    def setUp(self):
        self.client.force_login(self.user)

    def _sync(self, actions):
        return self.client.post(reverse("ui:hu_control_sync"),
                                data=json.dumps({"actions": actions}),
                                content_type="application/json")

    def test_idempotent_client_id_no_double_kpi(self):
        act = [{"client_id": "act-1", "item": self.it.pk, "qty_base": self.it.base_qty}]
        self._sync(act)
        self._sync(act)                                  # replay tego samego batcha
        self.assertEqual(HUControlAttempt.objects.filter(client_id="act-1").count(), 1)

    def test_negative_qty_rejected_sync(self):
        data = json.loads(self._sync([{"client_id": "n", "item": self.it.pk, "qty_base": -5}]).content)
        self.assertEqual(data["applied"], 0)
        self.assertEqual(data["results"][0]["error"], "qty_negative")

    def test_negative_qty_rejected_online(self):
        self.client.post(reverse("ui:hu_control_start", args=[self.hu.pk]))
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.it.pk]),
                         {"action": "confirm", "qty_base": "-5"})
        self.it.refresh_from_db()
        self.assertFalse(self.it.controlled)

    def test_sync_batch_cap(self):
        actions = [{"client_id": str(i), "item": self.it.pk, "qty_base": 1} for i in range(501)]
        r = self._sync(actions)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(json.loads(r.content)["error"], "batch_too_large")

    def test_ok_requires_verified_at_invariant(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                HandlingUnit.objects.filter(pk=self.hu.pk).update(status="ok", verified_at=None)

    def test_quality_issue_open_dedup(self):
        HUQualityIssue.objects.create(hu=self.hu, item=self.it, issue_type="damaged", status="open")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                HUQualityIssue.objects.create(hu=self.hu, item=self.it, issue_type="damaged", status="open")

    def test_recheck_guard_reads_audit_not_controlled_by(self):
        # c1 zrobił pierwotną kontrolę (audyt); po takeover controlled_by=c2, ale c1 dalej
        # zablokowany od rekontroli własnej pracy (guard czyta HUControlAttempt).
        U = get_user_model()
        grp = Group.objects.get_or_create(name=GROUP_CONTROLLER)[0]
        c1 = U.objects.create_user("rc1", password="x"); c1.groups.add(grp)
        c2 = U.objects.create_user("rc2", password="x"); c2.groups.add(grp)
        HUControlAttempt.objects.create(hu=self.hu, item=self.it, controller=c1,
                                        is_recheck=False, result="error")
        HandlingUnit.objects.filter(pk=self.hu.pk).update(status="to_recheck", controlled_by=c2)
        self.client.force_login(c1)
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.it.pk]),
                         {"action": "confirm", "qty_base": str(self.it.base_qty)})
        self.it.refresh_from_db()
        self.assertFalse(self.it.controlled)             # oryginalny kontroler zablokowany


class HUDispositionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.leader = get_user_model().objects.create_user("lead", password="x")
        cls.leader.groups.add(Group.objects.get_or_create(name=GROUP_LEADER)[0])
        cls.sh = Shipment.objects.create(name="Dostawa D")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, warehouse_type="A")
        cls.hu2 = HandlingUnit.objects.create(shipment=cls.sh, seq=2, status="ok",
                                              verified_at=timezone.now(), warehouse_type="A")

    def test_leader_disposition_marks_escaped_and_unblocks_readiness(self):
        self.client.force_login(self.leader)
        self.client.post(reverse("ui:hu_control_disposition", args=[self.hu.pk]),
                         {"note": "wyjechało"})
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "escaped")
        # escaped wykluczone z gotowości → shipment 'gotowy' (druga HU jest ok)
        self.assertTrue(self.sh.hu_checked_ready())
        ok, total = self.sh.pallet_readiness()
        self.assertEqual((ok, total), (1, 1))            # escaped poza licznikiem


class HULeaderRoutingTests(TestCase):
    def _login(self, *groups):
        u = get_user_model().objects.create_user("u_" + "_".join(groups)[:20], password="x")
        for g in groups:
            u.groups.add(Group.objects.get_or_create(name=g)[0])
        self.client.force_login(u)

    def test_leader_only_lands_on_hub(self):
        self._login(GROUP_LEADER)
        r = self.client.get(reverse("ui:home"))
        self.assertRedirects(r, reverse("ui:hu_control_hub"), fetch_redirect_response=False)

    def test_controller_only_lands_on_scanner(self):
        self._login(GROUP_CONTROLLER)
        r = self.client.get(reverse("ui:home"))
        # Spec UX 2026-09-03 §1: pulpit „Moja zmiana" jest ekranem domowym kontrolera.
        self.assertRedirects(r, reverse("ui:hu_my_shift"), fetch_redirect_response=False)


class HUApiTokenTests(TestCase):
    @override_settings(PALVIZ_API_TOKEN="old-token", PALVIZ_API_TOKENS="new-token, spare-token")
    def test_rotation_accepts_any_configured_token(self):
        for tok in ("old-token", "new-token", "spare-token"):
            r = self.client.get("/api/v2/health", **{"HTTP_X_API_KEY": tok})
            self.assertEqual(r.status_code, 200, tok)
        self.assertEqual(self.client.get("/api/v2/health",
                                         **{"HTTP_X_API_KEY": "wrong"}).status_code, 401)

    @override_settings(PALVIZ_API_TOKEN="", PALVIZ_API_TOKENS="")
    def test_disabled_when_no_token(self):
        self.assertEqual(self.client.get("/api/v2/health",
                                         **{"HTTP_X_API_KEY": "anything"}).status_code, 401)


class HUShelfLifeTests(TestCase):
    """F6/F7: krótki termin ważności — miękka bramka + potwierdzenie (propagacja F8)."""
    @classmethod
    def setUpTestData(cls):
        cls.user = _ctrl_user()
        cls.cust = Customer.objects.create(name="MED", min_shelf_life_months=6)
        cls.sh = Shipment.objects.create(name="Dostawa S", customer=cls.cust)
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="SH1", status="in_control")
        from django.utils import timezone as _tz
        soon = _tz.localdate() + timedelta(days=60)          # < 6 miesięcy → krótkodatowe
        cls.item = HandlingUnitItem.objects.create(hu=cls.hu, ref_code="A1", base_qty=1,
                                                   base_unit="OP", expiry=soon)

    def setUp(self):
        self.client.force_login(self.user)

    def test_short_dated_blocks_until_ack(self):
        self.assertTrue(self.hu.has_short_dated)
        # policz pozycję OK (data potwierdzona), ale krótki termin nieuznany → finalize odbija
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
                         {"action": "confirm", "qty_base": "1", "expiry_ok": "1"})
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertNotEqual(self.hu.status, "ok")
        # potwierdź krótki termin → finalize przechodzi
        self.client.post(reverse("ui:hu_control_ack_short_dated", args=[self.hu.pk]))
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "ok")

    def test_finalize_button_disabled_until_short_dated_ack(self):
        """UX #6: skoro serwer odbija finalize przy nieuznanym krótkim terminie,
        przycisk „Zaksięguj" musi być disabled (jak dla wymagań klienta),
        a nie wyglądać na klikalny i dopiero potem odbijać."""
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertContains(r, "Najpierw potwierdź krótki termin")
        self.client.post(reverse("ui:hu_control_ack_short_dated", args=[self.hu.pk]))
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertNotContains(r, "Najpierw potwierdź krótki termin")


class HUConfirmPropagationTests(TestCase):
    """F8: potwierdzenie wymagań klienta raz na wysyłkę propaguje na wszystkie palety."""
    @classmethod
    def setUpTestData(cls):
        cls.user = _ctrl_user()
        cls.cust = Customer.objects.create(name="ACME", requires_fumigated_pallet=True)
        cls.sh = Shipment.objects.create(name="Dostawa P", customer=cls.cust)
        cls.hu1 = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="P1", status="in_control")
        cls.hu2 = HandlingUnit.objects.create(shipment=cls.sh, seq=2, code="P2", status="planned")

    def test_confirm_reqs_propagates_across_shipment(self):
        self.client.force_login(self.user)
        self.client.post(reverse("ui:hu_control_confirm_reqs", args=[self.hu1.pk]))
        self.hu1.refresh_from_db(); self.hu2.refresh_from_db()
        self.assertIsNotNone(self.hu1.client_reqs_confirmed_at)
        self.assertIsNotNone(self.hu2.client_reqs_confirmed_at)   # siostra też potwierdzona


class HUCorrectiveAndEscalationTests(TestCase):
    """F9 zadania naprawcze + F10 eskalacja rekontroli."""
    @classmethod
    def setUpTestData(cls):
        cls.user = _ctrl_user()
        cls.sh = Shipment.objects.create(name="Dostawa C")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="C1", status="in_control")
        cls.item = HandlingUnitItem.objects.create(hu=cls.hu, ref_code="A1", base_qty=10, base_unit="OP")

    def setUp(self):
        self.client.force_login(self.user)

    def test_shortage_raises_corrective_task(self):
        # policz 6 z oczekiwanych 10 → brak 4 → error → finalize → zadanie naprawcze
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
                         {"action": "confirm", "sure": "1", "qty_base": "6"})
        with self.captureOnCommitCallbacks(execute=True):   # zadania naprawcze po commicie
            self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "to_recheck")
        self.assertTrue(Task.objects.filter(dedup_key__startswith=f"hu_fix:{self.hu.pk}:").exists())

    def test_escalation_after_repeated_rechecks(self):
        from huctl.views.hu_control import _maybe_escalate_recheck
        # dwie wcześniejsze rundy rekontroli w audycie statusu
        for _ in range(2):
            HUStatusEvent.objects.create(hu=self.hu, from_status="in_control", to_status="to_recheck")
        _maybe_escalate_recheck(self.hu, self.user)
        self.assertTrue(Task.objects.filter(
            dedup_key=f"hu_recheck_escalate:{self.hu.pk}").exists())


class HUGovernanceTests(TestCase):
    """G1 takeover (powód + powiadomienie), G2 reopen (blok po wysyłce + powód),
    G3 cofnięcie gotowości (reset ready_notified_at + alert)."""
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.leader = U.objects.create_user("glead", password="x")
        cls.leader.groups.add(Group.objects.get_or_create(name=GROUP_LEADER)[0])
        grp = Group.objects.get_or_create(name=GROUP_CONTROLLER)[0]
        cls.c1 = U.objects.create_user("gc1", password="x"); cls.c1.groups.add(grp)
        cls.c2 = U.objects.create_user("gc2", password="x"); cls.c2.groups.add(grp)

    def test_takeover_records_reason_and_notifies_displaced(self):
        sh = Shipment.objects.create(name="GT")
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="GT1",
                                         status="in_control", controlled_by=self.c1)
        self.client.force_login(self.c2)
        self.client.post(reverse("ui:hu_control_takeover", args=[hu.pk]), {"reason": "c1 na przerwie"})
        hu.refresh_from_db()
        self.assertEqual(hu.controlled_by, self.c2)
        ev = HUStatusEvent.objects.filter(hu=hu).first()
        self.assertIn("c1 na przerwie", ev.note)                     # powód w audycie
        self.assertTrue(Notification.objects.filter(recipient=self.c1).exists())   # przejmowany powiadomiony

    def test_reopen_blocked_after_dispatch(self):
        from django.utils import timezone as _tz
        sh = Shipment.objects.create(name="GT2", status="sent")      # wysłane
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="GT2H",
                                         status="ok", verified_at=_tz.now())
        self.client.force_login(self.leader)
        self.client.post(reverse("ui:hu_control_reopen", args=[hu.pk]), {"reason": "x"})
        hu.refresh_from_db()
        self.assertEqual(hu.status, "ok")                            # zablokowane — towar wyjechał

    def test_reopen_requires_reason(self):
        from django.utils import timezone as _tz
        sh = Shipment.objects.create(name="GT3")
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="GT3H",
                                         status="ok", verified_at=_tz.now())
        self.client.force_login(self.leader)
        self.client.post(reverse("ui:hu_control_reopen", args=[hu.pk]))   # bez powodu
        hu.refresh_from_db()
        self.assertEqual(hu.status, "ok")                            # nie otwarto bez powodu

    def test_reopen_withdraws_readiness(self):
        from django.utils import timezone as _tz
        sh = Shipment.objects.create(name="GT4", ready_notified_at=_tz.now())
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="GT4H",
                                         status="ok", verified_at=_tz.now())
        self.client.force_login(self.leader)
        self.client.post(reverse("ui:hu_control_reopen", args=[hu.pk]), {"reason": "błąd"})
        sh.refresh_from_db()
        self.assertIsNone(sh.ready_notified_at)                      # gotowość cofnięta
