from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import GROUP_ADMIN


class IncompleteShipmentTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.u = User.objects.create_user("a", "a@e.pl", "Zx9!longpass"); self.u.groups.add(g)
        self.client.force_login(self.u)
        self.sh = Shipment.objects.create(name="S", customer=Customer.objects.create(name="K"),
                                          picking_complete=False)
        HandlingUnit.objects.create(shipment=self.sh, seq=1, code="H1", is_completed=True)
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=2, code="H2", is_completed=True,
                                              status="planned")

    def test_summary_counts_ready_and_waiting(self):
        s = self.sh.picking_summary()
        self.assertEqual(s["ready"], 2)
        self.assertTrue(s["waiting"])

    def test_detail_shows_incomplete_banner(self):
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertContains(r, "NIEKOMPLETN")
