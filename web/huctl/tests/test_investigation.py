"""Wyjaśnianie błędu (spec UX §3): bramka 2. liczenia, pauza KPI ważna po
potwierdzeniu pickera/lidera, odrzucenie lidera, jedno otwarte per HU."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from huctl.models_control import HUErrorInvestigation
from huctl.views.hu_investigation import _can_confirm
from ui.models import HandlingUnit, HandlingUnitItem, Shipment
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


def _user(name, group):
    u = get_user_model().objects.create_user(name, password="x")
    u.groups.add(Group.objects.get_or_create(name=group)[0])
    return u


class InvestigationTests(TestCase):
    def setUp(self):
        self.ctrl = _user("ctrl", GROUP_CONTROLLER)
        self.lead = _user("lead", GROUP_LEADER)
        self.picker = _user("jkowal", GROUP_CONTROLLER)   # login = pole picker z SAP
        sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(
            shipment=sh, seq=1, code="H1", warehouse_type="92EX", picker="JKOWAL")
        self.item = HandlingUnitItem.objects.create(
            hu=self.hu, ref_code="REF1", base_qty=10)
        self.client.force_login(self.ctrl)

    def _start(self):
        return self.client.post(
            reverse("ui:hu_investigation_start", args=[self.hu.pk]),
            {"error_type": "missing"})

    def test_start_requires_confirmed_error(self):
        self._start()
        self.assertFalse(self.hu.investigations.exists())  # brak pozycji z błędem

    def test_start_after_error_and_single_open(self):
        HandlingUnitItem.objects.filter(pk=self.item.pk).update(
            controlled=True, result="error")
        self._start()
        self.assertEqual(self.hu.investigations.filter(ended_at__isnull=True).count(), 1)
        self._start()  # drugie otwarcie → odmowa
        self.assertEqual(self.hu.investigations.count(), 1)

    def test_confirm_by_picker_and_kpi_flag(self):
        HandlingUnitItem.objects.filter(pk=self.item.pk).update(
            controlled=True, result="error")
        self._start()
        inv = self.hu.investigations.get()
        self.assertFalse(inv.kpi_excluded())               # niepotwierdzone = liczy się do KPI
        self.assertTrue(_can_confirm(self.picker, inv))    # login==picker (case-insens)
        self.assertFalse(_can_confirm(self.ctrl, inv))     # autor NIE potwierdza sam
        self.client.force_login(self.picker)
        self.client.post(reverse("ui:hu_investigation_confirm", args=[inv.pk]))
        inv.refresh_from_db()
        self.assertTrue(inv.kpi_excluded())

    def test_leader_reject_closes_and_counts_to_kpi(self):
        HandlingUnitItem.objects.filter(pk=self.item.pk).update(
            controlled=True, result="error")
        self._start()
        inv = self.hu.investigations.get()
        self.client.force_login(self.lead)
        self.client.post(reverse("ui:hu_investigation_reject", args=[inv.pk]))
        inv.refresh_from_db()
        self.assertTrue(inv.rejected)
        self.assertIsNotNone(inv.ended_at)
        self.assertFalse(inv.kpi_excluded())

    def test_end_by_author(self):
        HandlingUnitItem.objects.filter(pk=self.item.pk).update(
            controlled=True, result="error")
        self._start()
        inv = self.hu.investigations.get()
        self.client.post(reverse("ui:hu_investigation_end", args=[inv.pk]))
        inv.refresh_from_db()
        self.assertIsNotNone(inv.ended_at)

    def test_kpi_excludes_confirmed_investigation_time(self):
        """Potwierdzone wyjaśnianie odejmuje swój czas z czasu netto KPI kontrolera."""
        from datetime import timedelta
        from huctl.views.hu_reports import _kpi_stats
        from ui.models import HUControlAttempt
        now = timezone.now()
        # dwie próby w odstępie 300 s → gap_sum=300
        a1 = HUControlAttempt.objects.create(hu=self.hu, item=self.item,
                                             controller=self.ctrl, seconds_since_prev=None)
        a2 = HUControlAttempt.objects.create(hu=self.hu, item=self.item,
                                             controller=self.ctrl, seconds_since_prev=300)
        inv = HUErrorInvestigation.objects.create(
            hu=self.hu, controller=self.ctrl, error_type="missing",
            confirmed_by=self.lead, confirmed_at=now)
        HUErrorInvestigation.objects.filter(pk=inv.pk).update(
            started_at=now - timedelta(seconds=120), ended_at=now)
        rows, _ = _kpi_stats(now - timedelta(hours=1), now + timedelta(hours=1))
        row = next(r for r in rows if r["controller"] == "ctrl")
        self.assertEqual(row["net_sec"], 180)   # 300 - 120 potwierdzonego wyjaśniania

    def test_late_confirm_does_not_exclude_kpi(self):
        """Ack po oknie HU_INVESTIGATION_CONFIRM_MIN nie ratuje pauzy (anty-nadużycie)."""
        from datetime import timedelta
        now = timezone.now()
        inv = HUErrorInvestigation.objects.create(
            hu=self.hu, controller=self.ctrl, error_type="missing",
            confirmed_by=self.lead, confirmed_at=now)
        HUErrorInvestigation.objects.filter(pk=inv.pk).update(
            started_at=now - timedelta(minutes=30), ended_at=now)  # ack po 30 min > okno 15
        inv.refresh_from_db()
        self.assertTrue(inv.is_overdue())
        self.assertFalse(inv.kpi_excluded())

    def test_leader_panel_shows_pending_section(self):
        HandlingUnitItem.objects.filter(pk=self.item.pk).update(
            controlled=True, result="error")
        self._start()
        self.client.force_login(self.lead)
        r = self.client.get(reverse("ui:hu_control_leader"))
        self.assertContains(r, "Wyjaśniane błędy do potwierdzenia")
        self.assertContains(r, self.hu.ref)

    def test_late_ack_rejected_at_confirm_endpoint(self):
        from datetime import timedelta
        HandlingUnitItem.objects.filter(pk=self.item.pk).update(
            controlled=True, result="error")
        self._start()
        inv = self.hu.investigations.get()
        HUErrorInvestigation.objects.filter(pk=inv.pk).update(
            started_at=timezone.now() - timedelta(minutes=30))
        self.client.force_login(self.picker)
        self.client.post(reverse("ui:hu_investigation_confirm", args=[inv.pk]))
        inv.refresh_from_db()
        self.assertIsNone(inv.confirmed_at)   # spóźniony ack odrzucony na wejściu
