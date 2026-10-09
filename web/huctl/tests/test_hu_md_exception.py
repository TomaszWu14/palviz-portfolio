# Fala 4: zgłaszanie niezgodności master daty per pozycja + decyzje lidera.
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from ui.models import HandlingUnit, HandlingUnitItem, HUStatusEvent, Shipment, Task
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


def _user(name, group):
    g, _ = Group.objects.get_or_create(name=group)
    u = User.objects.create_user(name, f"{name}@example.com", "Zx9!longpass")
    u.groups.add(g)
    return u


class MdExceptionTests(TestCase):
    def setUp(self):
        self.controller = _user("ctrl", GROUP_CONTROLLER)
        self.leader = _user("lead", GROUP_LEADER)
        sh = Shipment.objects.create(name="D-1")
        self.hu = HandlingUnit.objects.create(shipment=sh, code="HU1")
        self.item = HandlingUnitItem.objects.create(hu=self.hu, ref_code="ZR-1")

    def test_controller_reports_exception_creates_task(self):
        self.client.force_login(self.controller)
        r = self.client.post(reverse("ui:hu_item_md_exception", args=[self.item.pk]),
                             {"note": "zły przelicznik KAR"})
        self.assertEqual(r.status_code, 302)
        self.item.refresh_from_db()
        self.assertTrue(self.item.md_exception)
        self.assertEqual(self.item.md_exception_note, "zły przelicznik KAR")
        task = Task.objects.get(category="md_exception")
        self.assertIn("ZR-1", task.title)
        self.assertEqual(task.related_hu_id, self.hu.pk)

    def test_leader_close_clears_flag_and_task(self):
        self.item.md_exception = True
        self.item.save(update_fields=["md_exception"])
        Task.objects.create(title="t", category="md_exception",
                            dedup_key=f"md_exc:{self.item.pk}")
        self.client.force_login(self.leader)
        self.client.post(reverse("ui:hu_item_md_exception_close", args=[self.item.pk]))
        self.item.refresh_from_db()
        self.assertFalse(self.item.md_exception)
        self.assertEqual(Task.objects.get(category="md_exception").status, "done")
        self.assertTrue(HUStatusEvent.objects.filter(hu=self.hu,
                                                     note__icontains="zamknięty").exists())

    def test_leader_delete_removes_item_with_audit(self):
        self.client.force_login(self.leader)
        self.client.post(reverse("ui:hu_item_md_exception_delete", args=[self.item.pk]))
        self.assertFalse(HandlingUnitItem.objects.filter(pk=self.item.pk).exists())
        self.assertTrue(HUStatusEvent.objects.filter(hu=self.hu,
                                                     note__icontains="Usunięto pozycję ZR-1").exists())

    def test_controller_cannot_delete(self):
        self.client.force_login(self.controller)
        r = self.client.post(reverse("ui:hu_item_md_exception_delete", args=[self.item.pk]))
        self.assertIn(r.status_code, (302, 403))   # rola odrzucona
        self.assertTrue(HandlingUnitItem.objects.filter(pk=self.item.pk).exists())
