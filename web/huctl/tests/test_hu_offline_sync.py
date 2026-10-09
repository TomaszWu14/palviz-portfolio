"""Kontrola HU — offline: batch sync replays counts recorded while disconnected."""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import (Product, Shipment, ShipmentLine, PalletizationInstruction,
                       HUControlAttempt)
from ui.roles import ALL_GROUPS
from huctl.views.hu import _generate_handling_units


def _user_all_roles():
    u = get_user_model().objects.create_user(username="ctrl", password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class OfflineSyncTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.sh = Shipment.objects.create(name="Dostawa", recipient_name="PHARMO")
        p = Product.objects.create(code="NL100", name="NONVI")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=10, demand_pcs=100, is_active=True)
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=8, unit="kar", source_unit="OP")
        _generate_handling_units(cls.sh)
        cls.hu = cls.sh.handling_units.first()

    def setUp(self):
        self.client.force_login(self.user)

    def _sync(self, actions):
        return self.client.post(reverse("ui:hu_control_sync"),
                                data=json.dumps({"actions": actions}),
                                content_type="application/json")

    def test_sync_rejects_locked_hu(self):
        # A finalized (ok) HU must not be mutated by offline sync.
        from django.utils import timezone as _tz
        self.hu.status = "ok"
        self.hu.verified_at = _tz.now()
        self.hu.save(update_fields=["status", "verified_at"])
        it = self.hu.items.first()
        data = json.loads(self._sync([{"client_id": "c1", "item": it.pk, "qty_base": it.base_qty}]).content)
        self.assertEqual(data["applied"], 0)
        self.assertEqual(data["results"][0]["error"], "hu_locked")

    def test_sync_recheck_derived_from_status_not_client(self):
        # Client claims recheck=True but the HU is in normal control → attempt is NOT a recheck.
        from ui.models import HUControlAttempt
        it = self.hu.items.first()
        self._sync([{"client_id": "c1", "item": it.pk, "qty_base": it.base_qty, "recheck": True}])
        att = HUControlAttempt.objects.filter(item=it).first()
        self.assertFalse(att.is_recheck)

    def test_sync_applies_correct_count(self):
        it = self.hu.items.first()
        resp = self._sync([{"client_id": "c1", "item": it.pk, "qty_base": it.base_qty}])
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data["applied"], 1)
        self.assertEqual(data["results"][0]["result"], "ok")
        it.refresh_from_db()
        self.assertTrue(it.controlled)
        self.assertEqual(it.result, "ok")
        # An audit attempt was recorded (KPI trail).
        self.assertEqual(HUControlAttempt.objects.filter(item=it).count(), 1)

    def test_sync_flags_mismatch_as_error(self):
        # BIZ-006: rozbieżność wymaga „Tak, jestem pewien" (sure=1) jak online; bez tego
        # wpis jest odrzucony do przeliczenia online (test_hu_offline_same_gates).
        it = self.hu.items.first()
        self._sync([{"client_id": "c2", "item": it.pk, "qty_base": it.base_qty + 5, "sure": "1"}])
        it.refresh_from_db()
        self.assertEqual(it.result, "error")

    def test_sync_quality_flag_creates_issue(self):
        # BIZ-006: „damaged" wymaga zdjęcia (offline go nie przenosi → odrzut); flaga
        # jakości bez wymogu zdjęcia nadal zakłada zgłoszenie z replayu offline.
        from ui.models import HUQualityIssue
        it = self.hu.items.first()
        self._sync([{"client_id": "c3", "item": it.pk, "qty_base": it.base_qty,
                     "flags": ["wrong_batch"]}])
        self.assertTrue(HUQualityIssue.objects.filter(item=it, issue_type="wrong_batch").exists())

    def test_sync_batch_with_bad_item(self):
        it = self.hu.items.first()
        data = json.loads(self._sync([
            {"client_id": "ok", "item": it.pk, "qty_base": it.base_qty},
            {"client_id": "bad", "item": 999999, "qty_base": 1},
        ]).content)
        self.assertEqual(data["applied"], 1)
        by_id = {r["client_id"]: r for r in data["results"]}
        self.assertTrue(by_id["ok"]["ok"])
        self.assertFalse(by_id["bad"]["ok"])
        self.assertEqual(by_id["bad"]["error"], "item_not_found")

    def test_bad_json_rejected(self):
        resp = self.client.post(reverse("ui:hu_control_sync"), data="not json",
                                content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_requires_controller_role(self):
        u = get_user_model().objects.create_user("plain", password="x")
        self.client.force_login(u)
        resp = self._sync([])
        self.assertEqual(resp.status_code, 403)
