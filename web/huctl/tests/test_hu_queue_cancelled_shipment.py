"""BIZ-009 („Domknij wszystko”): nieliczone (planned) HU anulowanej wysyłki znikają z
kolejek kontroli — „Następna HU” (_call_queue / hu_control_next) i „Moja zmiana” /
„Weź następną” (_queue / hu_take_next). Wiersze HU zostają (audyt); HU już w kontroli
(to_recheck itd.) bez zmian. Powrót wysyłki do roboczej (requote) = HU wracają.
"""
from django.contrib.messages import get_messages
from django.test import RequestFactory, TestCase
from django.urls import reverse

from ui.roles import GROUP_CONTROLLER, GROUP_LEADER
from huctl.models import HandlingUnit
from huctl.views.hu_dashboard import _queue
from huctl.views.hu_helpers import _call_queue
from testkit.factories import HandlingUnitFactory, ShipmentFactory, UserFactory


class CancelledShipmentQueueTests(TestCase):
    def setUp(self):
        self.user = UserFactory(groups=[GROUP_CONTROLLER])
        self.client.force_login(self.user)
        self.cancelled = ShipmentFactory(status="cancelled")
        self.hu = HandlingUnitFactory(shipment=self.cancelled, status="planned")

    def _req(self):
        req = RequestFactory().get("/")
        req.user = self.user
        return req

    def _call_queue_ids(self):
        return set(_call_queue(self._req()).values_list("pk", flat=True))

    def _takeable_ids(self):
        return {h.pk for h in _queue(self._req())[1]}

    def test_planned_hu_of_cancelled_shipment_not_in_queues(self):
        active = HandlingUnitFactory(shipment=ShipmentFactory(), status="planned")
        self.assertEqual(self._call_queue_ids(), {active.pk})
        self.assertEqual(self._takeable_ids(), {active.pk})

    def test_recheck_hu_of_cancelled_shipment_stays(self):
        # Już liczona (w kontroli) — decyzja dotyczy tylko nieliczonych.
        rec = HandlingUnitFactory(shipment=self.cancelled, status="to_recheck")
        self.assertEqual(self._call_queue_ids(), {rec.pk})
        self.assertEqual(self._takeable_ids(), {rec.pk})

    def test_control_next_does_not_offer_it(self):
        r = self.client.get(reverse("ui:hu_control_next"))
        self.assertRedirects(r, reverse("ui:hu_control_menu"), fetch_redirect_response=False)
        self.hu.refresh_from_db()
        self.assertIsNone(self.hu.assigned_to_id)

    def test_take_next_does_not_offer_it(self):
        r = self.client.post(reverse("ui:hu_take_next"))
        self.assertRedirects(r, reverse("ui:hu_my_shift"), fetch_redirect_response=False)
        self.assertIn("Kolejka pusta", " ".join(str(m) for m in get_messages(r.wsgi_request)))
        self.hu.refresh_from_db()
        self.assertIsNone(self.hu.assigned_to_id)

    def test_menu_and_shift_counts_exclude_it(self):
        self.assertEqual(self.client.get(reverse("ui:hu_control_menu")).context["queue_count"], 0)
        self.assertEqual(self.client.get(reverse("ui:hu_my_shift")).context["queue_count"], 0)

    def test_hu_row_untouched_and_back_in_queue_after_draft(self):
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "planned")
        self.assertTrue(HandlingUnit.objects.filter(pk=self.hu.pk).exists())
        self.cancelled.status = "draft"          # ponowna wycena → robocza
        self.cancelled.save(update_fields=["status"])
        self.assertEqual(self._call_queue_ids(), {self.hu.pk})
        self.assertEqual(self._takeable_ids(), {self.hu.pk})

    def test_hu_card_shows_cancelled_marker(self):
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertContains(r, "Wysyłka anulowana")
        other = HandlingUnitFactory(shipment=ShipmentFactory(), status="planned")
        r = self.client.get(reverse("ui:hu_control_detail", args=[other.pk]))
        self.assertNotContains(r, "Wysyłka anulowana")


class CancelledShipmentLeaderAndGroupTests(TestCase):
    """Licznik „nieprzydzielone” w panelu lidera i grupa odbiorcy też pomijają nieliczone HU
    anulowanej wysyłki (dotąd filtrowały je tylko kolejki)."""

    def setUp(self):
        self.user = UserFactory(groups=[GROUP_CONTROLLER, GROUP_LEADER])
        self.client.force_login(self.user)
        self.active_sh = ShipmentFactory(recipient_name="PHARMO")
        self.cancelled_sh = ShipmentFactory(recipient_name="PHARMO", status="cancelled")
        self.hu = HandlingUnitFactory(shipment=self.active_sh, status="planned")
        self.sib_active = HandlingUnitFactory(shipment=self.active_sh, status="planned")
        self.sib_cancelled = HandlingUnitFactory(shipment=self.cancelled_sh, status="planned")

    def test_leader_unassigned_count_excludes_cancelled(self):
        r = self.client.get(reverse("ui:hu_control_leader"))
        self.assertEqual(r.context["planned_unassigned"], 2)

    def test_group_panel_offers_no_action_for_cancelled(self):
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        actions = {h.pk: h.action for h in r.context["sibling_hus"]}
        self.assertEqual(actions[self.sib_active.pk], "act")
        self.assertIsNone(actions[self.sib_cancelled.pk])

    def test_reserve_group_skips_cancelled(self):
        self.client.post(reverse("ui:hu_reserve_group", args=[self.hu.pk]))
        for h in (self.hu, self.sib_active, self.sib_cancelled):
            h.refresh_from_db()
        self.assertEqual(self.hu.assigned_to, self.user)
        self.assertEqual(self.sib_active.assigned_to, self.user)
        self.assertIsNone(self.sib_cancelled.assigned_to)
