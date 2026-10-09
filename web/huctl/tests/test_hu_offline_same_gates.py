"""BIZ-006 (decyzja „Te same reguły + znacznik"): sync offline (hu_control_sync) stosuje
TE SAME bramki co liczenie online — zdjęcie przy uszkodzeniu/ułożeniu, przeliczenie
„w ciemno", całe jednostki, zdjęcie obecności, wymuszenie skanu ze źródłem z SESJI
serwera (nie z deklaracji klienta). Wpis łamiący bramkę jest odrzucany per pozycja z
powodem po polsku; przyjęte wpisy offline są oznaczone w KPI i na panelu lidera."""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from huctl.kpi import kpi_stats
from ui.models import (HandlingUnit, HandlingUnitItem, HUControlAttempt, HUQualityIssue,
                       Shipment)
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


def _user(name, group):
    u = get_user_model().objects.create_user(name, password="x")
    u.groups.add(Group.objects.get_or_create(name=group)[0])
    return u


@override_settings(HU_SCAN_ENFORCE=False, HU_PHOTO_ENFORCE=False, HU_PHOTO_DETECT=False)
class OfflineSameGatesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = _user("c1", GROUP_CONTROLLER)
        self.client.force_login(self.u)
        self.hu = HandlingUnit.objects.create(shipment=Shipment.objects.create(name="D1"),
                                              seq=1, code="H1", status="in_control",
                                              controlled_by=self.u, warehouse_type="92EX")
        self.item = HandlingUnitItem.objects.create(hu=self.hu, ref_code="A1",
                                                    base_unit="OP", base_qty=5)

    def _sync(self, cid="c1", **extra):
        action = {"client_id": cid, "item": self.item.pk, "qty_base": "5"}
        action.update(extra)
        r = self.client.post(reverse("ui:hu_control_sync"), json.dumps({"actions": [action]}),
                             content_type="application/json")
        return r.json()["results"][0]

    def _session(self, **kv):
        s = self.client.session
        s.update(kv)
        s.save()

    def _assert_rejected(self, res, code):
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], code)
        self.assertTrue(res["message"])                   # powód po polsku dla skanera
        self.item.refresh_from_db()
        self.assertFalse(self.item.controlled)
        self.assertFalse(HUControlAttempt.objects.exists())

    def test_damage_or_placement_flag_without_photo_rejected(self):
        for i, flag in enumerate(("damaged", "bad_placement")):
            with self.subTest(flag=flag):
                res = self._sync(cid=f"f{i}", flags=[flag])
                self._assert_rejected(res, "photo_required")
                self.assertIn("zdjęcie", res["message"])
        self.assertFalse(HUQualityIssue.objects.exists())

    def test_flag_not_needing_photo_accepted(self):
        res = self._sync(flags=["wrong_batch"])
        self.assertTrue(res["ok"])
        self.assertTrue(HUQualityIssue.objects.filter(issue_type="wrong_batch").exists())

    def test_blind_mismatch_without_confirmation_rejected(self):
        res = self._sync(qty_base="3")
        self._assert_rejected(res, "recount_required")
        self.assertIn("przelicz", res["message"])

    def test_confirmed_mismatch_booked_as_picker_error(self):
        res = self._sync(qty_base="3", sure="1")          # „Tak, jestem pewien" jak online
        self.assertTrue(res["ok"])
        self.assertEqual(res["result"], "error")

    def test_fractional_units_rejected(self):
        self._assert_rejected(self._sync(qty_base="4.5"), "qty_not_whole")

    def test_additive_units_payload(self):
        res = self._sync(qty_base=None, units={"base": "5", "opz": None, "kar": None, "pal": None})
        self.assertTrue(res["ok"])
        self.assertEqual(res["result"], "ok")

    @override_settings(HU_SCAN_ENFORCE=True)
    def test_client_declared_scan_does_not_bypass_enforce(self):
        res = self._sync(input_source="scan")              # brak skanu HU w sesji serwera
        self._assert_rejected(res, "scan_required")
        self.assertIn("SKANEREM", res["message"])

    @override_settings(HU_SCAN_ENFORCE=True)
    def test_server_side_scan_accepted_and_audited(self):
        self._session(**{f"hu_scan_src_{self.hu.pk}": "scan", "hu_device": "ZEBRA-7"})
        res = self._sync(input_source="keyboard")          # deklaracja klienta ignorowana
        self.assertTrue(res["ok"])
        att = HUControlAttempt.objects.get(client_id="c1")
        self.assertEqual((att.input_source, att.device_id), ("scan", "ZEBRA-7"))

    @override_settings(HU_PHOTO_ENFORCE=True)
    def test_presence_photo_gate_applies_offline(self):
        self._session(device_type="mobile")                # urządzenie z aparatem
        self.client.get(reverse("ui:hu_control_menu"))     # PresenceMiddleware stempluje profil
        self.assertTrue(get_user_model().objects.get(pk=self.u.pk).profile.has_camera)
        self._assert_rejected(self._sync(), "photo_required")
        self._session(**{f"hu_photo_ok_{self.hu.pk}": True})
        self.assertTrue(self._sync(cid="c2")["ok"])

    def test_replay_of_applied_action_is_ok_not_locked(self):
        first = self._sync(qty_base="3", sure="1")
        again = self._sync(qty_base="3", sure="1")         # zgubiona odpowiedź → replay
        self.assertEqual((first["ok"], again["ok"]), (True, True))
        self.assertEqual(again["result"], "error")
        self.assertEqual(HUControlAttempt.objects.count(), 1)


@override_settings(HU_SCAN_ENFORCE=False, HU_PHOTO_ENFORCE=False, HU_PHOTO_DETECT=False)
class OfflineMarkerTests(TestCase):
    """Przyjęte wpisy offline (niepusty client_id) oznaczone w KPI i u lidera."""

    def setUp(self):
        cache.clear()
        self.u = _user("c1", GROUP_CONTROLLER)
        self.hu = HandlingUnit.objects.create(shipment=Shipment.objects.create(name="D1"),
                                              seq=1, code="H1", status="in_control",
                                              controlled_by=self.u, warehouse_type="92EX")
        self.it_off = HandlingUnitItem.objects.create(hu=self.hu, ref_code="A1",
                                                      base_unit="OP", base_qty=5)
        self.it_on = HandlingUnitItem.objects.create(hu=self.hu, ref_code="A2",
                                                     base_unit="OP", base_qty=5)
        self.client.force_login(self.u)
        self.client.post(reverse("ui:hu_control_sync"),
                         json.dumps({"actions": [{"client_id": "off-1", "item": self.it_off.pk,
                                                  "qty_base": "5"}]}),
                         content_type="application/json")
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.it_on.pk]),
                         {"qty_base": "5", "action": "confirm"})

    def test_kpi_counts_offline_attempts_only(self):
        self.assertEqual(HUControlAttempt.objects.count(), 2)   # online bez zmian
        now = timezone.now()
        rows, totals = kpi_stats(now - timedelta(hours=1), now + timedelta(hours=1))
        self.assertEqual((rows[0]["positions"], rows[0]["offline"]), (2, 1))
        self.assertEqual(totals["offline"], 1)
        zrows, _t = kpi_stats(now - timedelta(hours=1), now + timedelta(hours=1), by="zone")
        self.assertEqual(zrows[0]["offline"], 1)

    def test_leader_panel_and_kpi_screen_show_offline(self):
        self.client.force_login(_user("lead", GROUP_LEADER))
        self.assertContains(self.client.get(reverse("ui:hu_control_leader")),
                            "1× wpis offline (sync)")
        self.assertContains(self.client.get(reverse("ui:hu_control_kpi")), "1 offline")
