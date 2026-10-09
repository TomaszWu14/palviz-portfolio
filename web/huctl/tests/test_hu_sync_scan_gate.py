"""Offline sync a wymuszenie skanu: źródło inputu to SESJA serwera (skan HU online,
hu_control_scan) — jak na torze online; samodeklaracja `input_source` klienta PWA jest
ignorowana (BIZ-006: obchodziła HU_SCAN_ENFORCE). Bez skanu przy enforce — odrzut."""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from ui.models import HandlingUnit, HandlingUnitItem, HUControlAttempt, Shipment
from ui.roles import GROUP_CONTROLLER


class SyncScanGateTests(TestCase):
    def setUp(self):
        self.u = get_user_model().objects.create_user("c1", password="x")
        self.u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        self.client.force_login(self.u)
        sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="H1",
                                              status="in_control", controlled_by=self.u,
                                              warehouse_type="92EX")
        self.item = HandlingUnitItem.objects.create(hu=self.hu, ref_code="A1",
                                                    base_unit="OP", base_qty=5)

    def _scanned(self, src):
        s = self.client.session                            # jak hu_control_scan online
        s[f"hu_scan_src_{self.hu.pk}"] = src
        s.save()

    def _sync(self, **extra):
        action = {"client_id": "cid1", "item": self.item.pk, "qty_base": "5"}
        action.update(extra)
        return self.client.post(reverse("ui:hu_control_sync"),
                                json.dumps({"actions": [action]}),
                                content_type="application/json").json()

    @override_settings(HU_SCAN_ENFORCE=True)
    def test_sync_without_scan_rejected_when_enforced(self):
        d = self._sync()                                   # brak input_source
        self.assertEqual(d["results"][0]["error"], "scan_required")
        self.item.refresh_from_db()
        self.assertFalse(self.item.controlled)

    @override_settings(HU_SCAN_ENFORCE=True)
    def test_sync_with_scan_applied_and_audited(self):
        self._scanned("scan")
        d = self._sync(input_source="scan")
        self.assertTrue(d["results"][0]["ok"])
        att = HUControlAttempt.objects.get(client_id="cid1")
        self.assertEqual(att.input_source, "scan")         # audyt Warstwy C z offline

    def test_sync_without_scan_ok_when_not_enforced(self):
        self._scanned("keyboard")
        d = self._sync(input_source="scan")                # deklaracja klienta ignorowana
        self.assertTrue(d["results"][0]["ok"])
        self.assertEqual(HUControlAttempt.objects.get(client_id="cid1").input_source,
                         "keyboard")
