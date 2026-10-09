from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import GROUP_ADMIN


class HuCallLockTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.u1 = User.objects.create_user("a", "a@e.pl", "Zx9!longpass"); self.u1.groups.add(g)
        self.u2 = User.objects.create_user("b", "b@e.pl", "Zx9!longpass"); self.u2.groups.add(g)
        sh = Shipment.objects.create(name="S", customer=Customer.objects.create(name="K"))
        self.hu = HandlingUnit.objects.create(shipment=sh, code="HU1", status="planned")

    def test_first_wins(self):
        self.client.force_login(self.u1)
        r = self.client.post(reverse("ui:hu_call", args=[self.hu.pk]))
        self.assertRedirects(r, reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.assigned_to, self.u1)
        self.assertIsNotNone(self.hu.called_at)

    def test_second_gets_blocked(self):
        self.hu.assigned_to = self.u1
        from django.utils import timezone
        self.hu.called_at = timezone.now(); self.hu.save()
        self.client.force_login(self.u2)
        r = self.client.post(reverse("ui:hu_call", args=[self.hu.pk]))
        self.assertRedirects(r, reverse("ui:hu_control_menu"))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.assigned_to, self.u1)  # nie przejęte
