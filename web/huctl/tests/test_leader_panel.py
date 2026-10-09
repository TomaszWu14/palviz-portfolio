"""Panel lidera + Rekontrola 2.0 (fale 3 roadmapy): przydzielanie HU, preferencja
przydzielonych w "Następna HU", eskalacja wiekowa zaległych rekontroli."""
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from ui.models import HandlingUnit, HUStatusEvent, Shipment, Task
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


def _leader_user(name="lider"):
    u = User.objects.create_user(name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_LEADER)[0])
    return u


def _controller_user(name="kontroler"):
    u = User.objects.create_user(name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class LeaderPanelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.leader = _leader_user()
        cls.ctrl = _controller_user()
        cls.sh = Shipment.objects.create(name="D-L1")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HUL1",
                                             status="to_recheck")

    def test_panel_renders_for_leader(self):
        self.client.force_login(self.leader)
        r = self.client.get("/control/leader/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Panel lidera")
        self.assertContains(r, "HUL1")
        # Auto-odświeżanie „na żywo" (poza-top10): wskaźnik + skrypt z pauzą na interakcję.
        self.assertContains(r, 'id="live-badge"')
        self.assertContains(r, "location.reload()")

    def test_panel_forbidden_for_controller(self):
        self.client.force_login(self.ctrl)
        r = self.client.get("/control/leader/")
        self.assertNotEqual(r.status_code, 200)

    def test_assign_and_unassign(self):
        self.client.force_login(self.leader)
        self.client.post(f"/control/hu/{self.hu.pk}/assign/", {"assignee": self.ctrl.pk})
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.assigned_to, self.ctrl)
        self.client.post(f"/control/hu/{self.hu.pk}/assign/", {"assignee": ""})
        self.hu.refresh_from_db()
        self.assertIsNone(self.hu.assigned_to)

    def test_next_prefers_assigned(self):
        older = HandlingUnit.objects.create(shipment=self.sh, seq=2, code="HUL2",
                                            status="planned")
        HandlingUnit.objects.filter(pk=older.pk).update(
            created_at=timezone.now() - timedelta(days=2))
        self.hu.assigned_to = self.ctrl
        self.hu.save(update_fields=["assigned_to"])
        self.client.force_login(self.ctrl)
        r = self.client.get("/control/next/")
        self.assertEqual(r.status_code, 302)
        self.assertIn(f"/control/hu/{self.hu.pk}/", r["Location"])


class RecheckOverdueTests(TestCase):
    def test_overdue_recheck_raises_leader_task(self):
        _leader_user()
        sh = Shipment.objects.create(name="D-L2")
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HUL9",
                                         status="to_recheck")
        ev = HUStatusEvent.objects.create(hu=hu, from_status="in_control",
                                          to_status="to_recheck")
        HUStatusEvent.objects.filter(pk=ev.pk).update(
            created_at=timezone.now() - timedelta(hours=48))
        from ui.notifications import run_recheck_overdue_checks
        n = run_recheck_overdue_checks()
        self.assertEqual(n, 1)
        self.assertTrue(Task.objects.filter(dedup_key=f"hu_recheck_overdue:{hu.pk}").exists())
        # Re-run nie duplikuje otwartego zadania.
        self.assertEqual(run_recheck_overdue_checks(), 0)
