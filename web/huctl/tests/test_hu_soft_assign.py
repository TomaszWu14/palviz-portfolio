"""Soft-assign HU: rezerwacja widoczna + miękko przejmowalna.
- inny kontroler widzi rezerwację (reserved_by) i może ją przejąć (zostaje 'planned'),
- przejęcie KONTROLI przenosi też assigned_to (spójność),
- reasignacja lidera powiadamia poprzedniego i przenosi controlled_by gdy in_control."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import HandlingUnit, Shipment, Notification
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


def _u(name, *groups):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class SoftAssignTests(TestCase):
    def setUp(self):
        self.u1 = _u("c1", GROUP_CONTROLLER)
        self.u2 = _u("c2", GROUP_CONTROLLER)
        self.sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=1, code="H1",
                                              status="planned", warehouse_type="WT01")

    def _reserve_by(self, u):
        self.hu.assigned_to = u
        self.hu.called_at = timezone.now()
        self.hu.save(update_fields=["assigned_to", "called_at"])

    def test_other_controller_sees_reservation(self):
        self._reserve_by(self.u1)
        self.client.force_login(self.u2)
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertEqual(r.context["reserved_by"], self.u1)
        self.assertFalse(r.context["can_start"])          # najpierw przejęcie rezerwacji

    def test_reserve_takeover_moves_reservation_and_notifies(self):
        self._reserve_by(self.u1)
        self.client.force_login(self.u2)
        self.client.post(reverse("ui:hu_reserve_takeover", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.assigned_to, self.u2)
        self.assertEqual(self.hu.status, "planned")        # rezerwacja, nie start kontroli
        self.assertTrue(Notification.objects.filter(recipient=self.u1).exists())

    def test_control_takeover_also_moves_assigned_to(self):
        self.hu.status = "in_control"
        self.hu.controlled_by = self.u1
        self.hu.assigned_to = self.u1
        self.hu.save()
        self.client.force_login(self.u2)
        self.client.post(reverse("ui:hu_control_takeover", args=[self.hu.pk]), {"reason": "test"})
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.controlled_by, self.u2)
        self.assertEqual(self.hu.assigned_to, self.u2)     # rezerwacja podąża za kontrolą

    def test_recipient_group_panel_lists_siblings(self):
        self.sh.recipient_name = "PHARMO"; self.sh.save()
        sib = HandlingUnit.objects.create(shipment=self.sh, seq=2, code="H2",
                                          status="planned", warehouse_type="WT01")
        self.client.force_login(self.u1)
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        codes = {h.code for h in r.context["sibling_hus"]}
        self.assertEqual(codes, {"H2"})
        self.assertContains(r, "Grupa do odb.")

    def test_reserve_group_reserves_in_zone_planned(self):
        self.sh.recipient_name = "PHARMO"; self.sh.save()
        s_in = HandlingUnit.objects.create(shipment=self.sh, seq=2, code="H2",
                                           status="planned", warehouse_type="WT01")
        s_out = HandlingUnit.objects.create(shipment=self.sh, seq=3, code="H3",
                                            status="planned", warehouse_type="ZZZ")
        from ui.models import ControllerZone
        ControllerZone.objects.create(user=self.u1, code="WT01")   # tylko WT01
        self.client.force_login(self.u1)
        self.client.post(reverse("ui:hu_reserve_group", args=[self.hu.pk]))
        self.hu.refresh_from_db(); s_in.refresh_from_db(); s_out.refresh_from_db()
        self.assertEqual(self.hu.assigned_to, self.u1)
        self.assertEqual(s_in.assigned_to, self.u1)
        self.assertIsNone(s_out.assigned_to)                       # cross-strefa nie rezerwowana

    def test_stale_reservation_expires_via_call_queue(self):
        # Rezerwacja starsza niż HU_RESERVED_MAX_HOURS (planned) wygasa przy budowie kolejki.
        from datetime import timedelta
        from django.core.cache import cache
        from huctl.views.hu_control import _expire_stale_reservations
        self.hu.assigned_to = self.u1
        self.hu.called_at = timezone.now() - timedelta(hours=9)   # próg domyślny 8 h
        self.hu.save(update_fields=["assigned_to", "called_at"])
        cache.delete("hu_res_sweep")            # throttle 1×/min — zwolnij dla testu
        _expire_stale_reservations()
        self.hu.refresh_from_db()
        self.assertIsNone(self.hu.assigned_to)
        self.assertIsNone(self.hu.called_at)

    def test_fresh_reservation_not_expired(self):
        from django.core.cache import cache
        from huctl.views.hu_control import _expire_stale_reservations
        self.hu.assigned_to = self.u1
        self.hu.called_at = timezone.now()
        self.hu.save(update_fields=["assigned_to", "called_at"])
        cache.delete("hu_res_sweep")
        _expire_stale_reservations()
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.assigned_to, self.u1)

    def test_abandoned_incontrol_released_back_to_queue(self):
        # Porzucona kontrola (in_control, brak liczenia > HU_INCONTROL_RELEASE_HOURS)
        # wraca do 'planned' bez rezerwacji — auto-next znów ją poda; przejście logowane.
        from datetime import timedelta
        from django.core.cache import cache
        from ui.models import HUStatusEvent
        from huctl.views.hu_control import _expire_stale_reservations
        self.hu.status = "in_control"
        self.hu.controlled_by = self.u1
        self.hu.assigned_to = self.u1
        self.hu.called_at = timezone.now()
        self.hu.control_started_at = timezone.now() - timedelta(hours=5)   # próg domyślny 4 h
        self.hu.save()
        cache.delete("hu_res_sweep")
        _expire_stale_reservations()
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "planned")
        self.assertIsNone(self.hu.assigned_to)
        self.assertIsNone(self.hu.called_at)
        self.assertTrue(HUStatusEvent.objects.filter(hu=self.hu, kind="release",
                                                     to_status="planned").exists())

    def test_incontrol_with_recent_count_not_released(self):
        # Aktywna kontrola dużej palety (świeża próba liczenia) NIE jest zwalniana,
        # nawet gdy start był dawniej niż próg.
        from datetime import timedelta
        from django.core.cache import cache
        from ui.models import HandlingUnitItem, HUControlAttempt
        from huctl.views.hu_control import _expire_stale_reservations
        self.hu.status = "in_control"
        self.hu.controlled_by = self.u1
        self.hu.control_started_at = timezone.now() - timedelta(hours=5)
        self.hu.save()
        item = HandlingUnitItem.objects.create(hu=self.hu, ref_code="A1",
                                               base_unit="OP", base_qty=5)
        HUControlAttempt.objects.create(hu=self.hu, item=item, controller=self.u1,
                                        counted_qty=5, result="ok")
        cache.delete("hu_res_sweep")
        _expire_stale_reservations()
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "in_control")

    def test_leader_assign_group_reassigns_whole_recipient(self):
        self.sh.recipient_name = "PHARMO"; self.sh.save()
        sib = HandlingUnit.objects.create(shipment=self.sh, seq=2, code="H2",
                                          status="planned", warehouse_type="WT01")
        self.hu.assigned_to = self.u1; self.hu.save(update_fields=["assigned_to"])
        leader = _u("lead2", GROUP_LEADER)
        self.client.force_login(leader)
        self.client.post(reverse("ui:hu_control_assign_group", args=[self.hu.pk]),
                         {"assignee": str(self.u2.pk)})
        self.hu.refresh_from_db(); sib.refresh_from_db()
        self.assertEqual(self.hu.assigned_to, self.u2)
        self.assertEqual(sib.assigned_to, self.u2)                 # cała grupa przepięta
        self.assertTrue(Notification.objects.filter(recipient=self.u1).exists())  # wyparty powiadomiony

    def test_leader_reassign_notifies_previous_and_moves_control(self):
        leader = _u("lead", GROUP_LEADER)
        self.hu.status = "in_control"
        self.hu.controlled_by = self.u1
        self.hu.assigned_to = self.u1
        self.hu.save()
        self.client.force_login(leader)
        self.client.post(reverse("ui:hu_control_assign", args=[self.hu.pk]),
                         {"assignee": str(self.u2.pk)})
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.assigned_to, self.u2)
        self.assertEqual(self.hu.controlled_by, self.u2)   # kontrola przeniesiona
        self.assertTrue(Notification.objects.filter(recipient=self.u1).exists())  # poprzedni powiadomiony


class SweepInvestigationAndNotifyTests(TestCase):
    """Fix grill 2026-09-05 (pyt. 23/54): otwarta inwestygacja = aktywność (sweep nie
    zabiera HU w trakcie wyjaśniania), a każdy auto-zwrot/wygaśnięcie powiadamia operatora."""

    def setUp(self):
        self.u1 = _u("c1", GROUP_CONTROLLER)
        self.sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=1, code="H1",
                                              status="planned", warehouse_type="WT01")

    def _sweep(self):
        from django.core.cache import cache
        from huctl.views.hu_control import _expire_stale_reservations
        cache.delete("hu_res_sweep")
        _expire_stale_reservations()

    def _stale_incontrol(self):
        from datetime import timedelta
        self.hu.status = "in_control"
        self.hu.controlled_by = self.u1
        self.hu.assigned_to = self.u1
        self.hu.control_started_at = timezone.now() - timedelta(hours=5)
        self.hu.save()

    def test_open_investigation_blocks_release(self):
        from huctl.models_control import HUErrorInvestigation
        self._stale_incontrol()
        HUErrorInvestigation.objects.create(hu=self.hu, controller=self.u1,
                                            error_type="missing")
        self._sweep()
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "in_control")     # pauza to praca, nie porzucenie

    def test_closed_old_investigation_does_not_block(self):
        from datetime import timedelta
        from huctl.models_control import HUErrorInvestigation
        self._stale_incontrol()
        inv = HUErrorInvestigation.objects.create(hu=self.hu, controller=self.u1,
                                                  error_type="missing")
        old = timezone.now() - timedelta(hours=6)
        HUErrorInvestigation.objects.filter(pk=inv.pk).update(started_at=old,
                                                              ended_at=old)
        self._sweep()
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "planned")

    def test_release_notifies_controller(self):
        self._stale_incontrol()
        self._sweep()
        self.assertTrue(Notification.objects.filter(
            recipient=self.u1, title__icontains="wróciła do kolejki").exists())

    def test_expired_reservation_notifies_owner(self):
        from datetime import timedelta
        self.hu.assigned_to = self.u1
        self.hu.called_at = timezone.now() - timedelta(hours=9)
        self.hu.save(update_fields=["assigned_to", "called_at"])
        self._sweep()
        self.hu.refresh_from_db()
        self.assertIsNone(self.hu.assigned_to)
        self.assertTrue(Notification.objects.filter(
            recipient=self.u1, title__icontains="wygasła").exists())
