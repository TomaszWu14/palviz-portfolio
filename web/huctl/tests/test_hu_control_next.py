from datetime import date
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import GROUP_ADMIN


class HuControlNextTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.u = User.objects.create_user("a", "a@e.pl", "Zx9!longpass"); self.u.groups.add(g)
        self.client.force_login(self.u)

    def _hu(self, name, cdate, vip=False):
        c = Customer.objects.create(name=name, is_vip=vip)
        sh = Shipment.objects.create(name=name, customer=c, outbound_created_date=cdate)
        return HandlingUnit.objects.create(shipment=sh, code=name, status="planned")

    def test_next_picks_queue_head_and_reserves(self):
        old = self._hu("OLD", date(2026, 8, 1))
        self._hu("VIP", date(2026, 8, 9), vip=True)  # VIP bije FIFO
        r = self.client.get(reverse("ui:hu_control_next"))
        vip = HandlingUnit.objects.get(code="VIP")
        self.assertRedirects(r, reverse("ui:hu_control_detail", args=[vip.pk]))
        vip.refresh_from_db()
        self.assertEqual(vip.assigned_to, self.u)  # zarezerwowane
