"""Foreign / excess goods on a pallet auto-raise a top-priority team task to put the
excess back to its source location (Q37)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Shipment, HandlingUnit, HandlingUnitItem, HUQualityIssue, Task, Product
from ui.roles import GROUP_CONTROLLER


def _controller(name="ctrl"):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class ForeignGoodsTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.controller = _controller()
        cls.sh = Shipment.objects.create(name="Dostawa")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HU1",
                                             status="in_control", controlled_by=cls.controller,
                                             location="A1-01-100A")
        HandlingUnitItem.objects.create(hu=cls.hu, ref_code="A1", base_qty=5)

    def _report(self, ref="OBCY-9", qty="3"):
        return self.client.post(reverse("ui:hu_quality_add_foreign", args=[self.hu.pk]),
                                {"ref_code": ref, "qty": qty, "hu_code": self.hu.ref})

    def test_creates_high_priority_team_task(self):
        self.client.force_login(self.controller)
        self._report()
        self.assertTrue(HUQualityIssue.objects.filter(hu=self.hu, issue_type="foreign_item").exists())
        task = Task.objects.get(related_hu=self.hu)
        self.assertEqual(task.priority, "high")
        self.assertIsNone(task.assignee)                 # team task → whole group sees it
        self.assertEqual(task.related_location, "A1-01-100A")
        self.assertNotEqual(task.status, "done")

    def test_dedup_no_duplicate_task(self):
        self.client.force_login(self.controller)
        self._report()
        self._report()                                    # same REF again
        self.assertEqual(Task.objects.filter(related_hu=self.hu).count(), 1)

    def test_stores_product_code_even_without_a_product_row(self):
        # The operator typed a REF with no matching Product; the durable business-key field
        # must still capture it (survives the master-data DB split — a FK could not).
        self.client.force_login(self.controller)
        self._report(ref="OBCY-42")
        task = Task.objects.get(related_hu=self.hu)
        self.assertIsNone(task.related_product)           # no Product row → FK stays empty
        self.assertEqual(task.related_product_code, "OBCY-42")
        self.assertEqual(task.related_product_ref, "OBCY-42")

    def test_ref_prefers_code_field_and_reads_master_data(self):
        Product.objects.create(code="A1", name="Produkt A1")
        self.client.force_login(self.controller)
        self._report(ref="A1")
        task = Task.objects.get(related_hu=self.hu)
        self.assertEqual(task.related_product_code, "A1")
        self.assertEqual(task.related_product_ref, "A1")  # code field, not the FK
        self.assertEqual(task.related_product_data()["name"], "Produkt A1")

    def test_ref_falls_back_to_fk_when_code_missing(self):
        p = Product.objects.create(code="B2", name="Produkt B2")
        task = Task.objects.create(title="t", related_product=p)   # legacy row: FK only
        self.assertEqual(task.related_product_ref, "B2")
        self.assertEqual(task.related_product_data()["code"], "B2")
