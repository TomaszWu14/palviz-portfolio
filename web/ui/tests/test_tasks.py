"""Tasks & notifications module + the stock-discrepancy engine."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ui.models import (Product, PalletizationInstruction, Shipment, HandlingUnit,
                       HandlingUnitItem, WarehouseLocationMasterBatch, WarehouseLocationMaster,
                       Task, Notification)
from ui.notifications import run_stock_discrepancy_checks
from ui.roles import GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT


def _user(*groups, name="u"):
    u = get_user_model().objects.create_user(name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class StockDiscrepancyEngineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = _user(GROUP_MASTER_DATA, name="md")
        # P1 has an active instruction (carton dims → volume); P2 has none.
        cls.p1 = Product.objects.create(code="P1", name="Z danymi", ean="111")
        PalletizationInstruction.objects.create(product=cls.p1, version=1, carton_l=40, carton_w=30,
                                                carton_h=25, unit_weight=0.5, pcs_per_carton=10,
                                                demand_pcs=100, is_active=True)
        cls.p2 = Product.objects.create(code="P2", name="Bez instrukcji", ean="222")

        batch = WarehouseLocationMasterBatch.objects.create(name="loc")
        WarehouseLocationMaster.objects.create(batch=batch, location_code="LOC-OK", max_volume_m3=5.0)

        cls.sh = Shipment.objects.create(name="Stock magazynowy", is_stock=True)

        def hu(seq, loc):
            return HandlingUnit.objects.create(shipment=cls.sh, seq=seq, code=f"HU{seq}",
                                               warehouse_type="WMS", location=loc)
        # badloc: location not in master
        cls.hu_badloc = hu(1, "LOC-UNKNOWN")
        HandlingUnitItem.objects.create(hu=cls.hu_badloc, ref_code="P1", product=cls.p1,
                                        alt_unit="KAR", alt_qty=2, base_unit="OP", base_qty=20)
        # nodata: item product without instruction
        cls.hu_nodata = hu(2, "LOC-OK")
        HandlingUnitItem.objects.create(hu=cls.hu_nodata, ref_code="P2", product=cls.p2,
                                        alt_unit="KAR", alt_qty=2, base_unit="OP", base_qty=20)
        # overcap: 200 cartons × 0.03 m³ = 6 m³ > 5 m³
        cls.hu_over = hu(3, "LOC-OK")
        HandlingUnitItem.objects.create(hu=cls.hu_over, ref_code="P1", product=cls.p1,
                                        alt_unit="KAR", alt_qty=200, base_unit="OP", base_qty=2000)
        # expiry: past date
        cls.hu_exp = hu(4, "LOC-OK")
        HandlingUnitItem.objects.create(hu=cls.hu_exp, ref_code="P1", product=cls.p1,
                                        alt_unit="KAR", alt_qty=1, base_unit="OP", base_qty=10,
                                        expiry=timezone.localdate() - timedelta(days=3))

    def test_engine_raises_each_rule(self):
        n = run_stock_discrepancy_checks()
        self.assertGreaterEqual(n, 4)
        self.assertTrue(Task.objects.filter(dedup_key=f"stock:badloc:{self.hu_badloc.pk}").exists())
        self.assertTrue(Task.objects.filter(dedup_key=f"stock:nodata:{self.hu_nodata.pk}").exists())
        self.assertTrue(Task.objects.filter(dedup_key=f"stock:overcap:{self.hu_over.pk}").exists())
        self.assertTrue(Task.objects.filter(dedup_key=f"stock:expiry:{self.hu_exp.pk}").exists())
        # owner (Master Data) gets notified
        self.assertTrue(Notification.objects.filter(recipient=self.owner).exists())

    def test_tasks_hard_linked_to_hu(self):
        run_stock_discrepancy_checks()
        t = Task.objects.get(dedup_key=f"stock:badloc:{self.hu_badloc.pk}")
        self.assertEqual(t.related_hu_id, self.hu_badloc.pk)
        self.assertEqual(t.related_location, "LOC-UNKNOWN")

    def test_created_by_is_recorded(self):
        # CODE-008: parametr created_by był ignorowany — zadanie ma wskazywać wywołującego.
        run_stock_discrepancy_checks(created_by=self.owner)
        t = Task.objects.get(dedup_key=f"stock:badloc:{self.hu_badloc.pk}")
        self.assertEqual(t.created_by_id, self.owner.pk)

    def test_rerun_is_deduplicated(self):
        run_stock_discrepancy_checks()
        before = Task.objects.count()
        again = run_stock_discrepancy_checks()
        self.assertEqual(again, 0)
        self.assertEqual(Task.objects.count(), before)


class TasksModuleTests(TestCase):
    def setUp(self):
        self.client.force_login(_user(GROUP_MASTER_DATA, name="md"))

    def test_home_renders(self):
        r = self.client.get(reverse("ui:tasks_home"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Zadania")

    def test_create_and_complete_task(self):
        self.client.post(reverse("ui:task_create"), {"title": "Zrób X", "priority": "high"})
        t = Task.objects.get(title="Zrób X")
        self.assertEqual(t.priority, "high")
        self.client.post(reverse("ui:task_set_status", args=[t.pk]), {"status": "done"})
        t.refresh_from_db()
        self.assertEqual(t.status, "done")
        self.assertIsNotNone(t.assignee)        # claimed on completion

    def test_status_change_logs_activity(self):
        from ui.models import TaskComment
        t = Task.objects.create(title="Loguj", created_by=get_user_model().objects.get(username="md"))
        self.client.post(reverse("ui:task_set_status", args=[t.pk]), {"status": "in_progress"})
        self.assertTrue(TaskComment.objects.filter(task=t, is_system=True,
                                                   body__icontains="Status").exists())

    def test_user_comment_added(self):
        from ui.models import TaskComment
        t = Task.objects.create(title="Komentarz")
        self.client.post(reverse("ui:task_comment", args=[t.pk]), {"body": "Uwaga do zadania"})
        self.assertTrue(TaskComment.objects.filter(task=t, is_system=False,
                                                   body="Uwaga do zadania").exists())

    def test_checklist_add_and_toggle(self):
        from ui.models import TaskChecklistItem
        t = Task.objects.create(title="Z list\u0105")
        self.client.post(reverse("ui:task_checklist_add", args=[t.pk]), {"text": "Krok 1"})
        it = TaskChecklistItem.objects.get(task=t, text="Krok 1")
        self.assertFalse(it.done)
        self.client.post(reverse("ui:task_checklist_toggle", args=[it.pk]))
        it.refresh_from_db()
        self.assertTrue(it.done)

    def test_stock_discrepancy_close_requires_admin(self):
        t = Task.objects.create(title="Niezg.", category="stock_discrepancy")
        # plain Master Data user cannot close it
        self.client.post(reverse("ui:task_set_status", args=[t.pk]), {"status": "done"})
        t.refresh_from_db(); self.assertNotEqual(t.status, "done")
        # an admin can
        self.client.force_login(_user(GROUP_ADMIN, name="adm"))
        self.client.post(reverse("ui:task_set_status", args=[t.pk]), {"status": "done"})
        t.refresh_from_db(); self.assertEqual(t.status, "done")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                       EMAIL_HOST="smtp.test", DEFAULT_FROM_EMAIL="groove@test.pl")
    def test_assignment_sends_email(self):
        from django.core import mail
        bob = _user(GROUP_MASTER_DATA, name="bob2")
        bob.email = "bob@test.pl"; bob.save(update_fields=["email"])
        t = Task.objects.create(title="Mail przy przypisaniu")
        self.client.post(reverse("ui:task_edit", args=[t.pk]), {
            "title": t.title, "priority": "normal", "status": "todo", "assignee": str(bob.pk)})
        self.assertTrue(any("bob@test.pl" in m.to for m in mail.outbox))

    def test_transport_only_denied(self):
        self.client.force_login(_user(GROUP_TRANSPORT, name="tr"))
        self.assertEqual(self.client.get(reverse("ui:tasks_home")).status_code, 403)

    def test_edit_assigns_and_notifies(self):
        bob = _user(GROUP_MASTER_DATA, name="bob")
        t = Task.objects.create(title="Do przydziału", created_by=get_user_model().objects.get(username="md"))
        self.client.post(reverse("ui:task_edit", args=[t.pk]), {
            "title": "Do przydziału", "priority": "high", "status": "todo", "assignee": str(bob.pk)})
        t.refresh_from_db()
        self.assertEqual(t.assignee, bob)
        self.assertEqual(t.priority, "high")
        self.assertTrue(Notification.objects.filter(recipient=bob, title__icontains="Przydzielono").exists())

    def test_mine_filter(self):
        me = get_user_model().objects.get(username="md")
        Task.objects.create(title="Moje", assignee=me)
        Task.objects.create(title="Cudze")
        r = self.client.get(reverse("ui:tasks_home"), {"mine": "1"})
        self.assertContains(r, "Moje")
        self.assertNotContains(r, "Cudze")

    def test_overdue_reminder_daily(self):
        me = get_user_model().objects.get(username="md")
        t = Task.objects.create(title="Spóźnione", assignee=me,
                                due_date=timezone.localdate() - timedelta(days=2))
        self.client.get(reverse("ui:tasks_home"))      # first reminder
        t.refresh_from_db()
        self.assertEqual(t.overdue_last_reminded, timezone.localdate())
        self.assertEqual(Notification.objects.filter(recipient=me, level="error").count(), 1)
        self.client.get(reverse("ui:tasks_home"))      # same day → no duplicate
        self.assertEqual(Notification.objects.filter(recipient=me, level="error").count(), 1)
        # simulate next day → reminded again
        Task.objects.filter(pk=t.pk).update(overdue_last_reminded=timezone.localdate() - timedelta(days=1))
        self.client.get(reverse("ui:tasks_home"))
        self.assertEqual(Notification.objects.filter(recipient=me, level="error").count(), 2)
