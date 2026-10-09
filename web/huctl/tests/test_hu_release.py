from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import GROUP_ADMIN


class HuReleaseTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.u = User.objects.create_user("a", "a@e.pl", "Zx9!longpass"); self.u.groups.add(g)
        sh = Shipment.objects.create(name="S", customer=Customer.objects.create(name="K"))
        self.hu = HandlingUnit.objects.create(shipment=sh, code="HU1", status="planned",
                                              assigned_to=self.u, called_at=timezone.now())
        self.client.force_login(self.u)

    def test_refuse_returns_to_queue(self):
        self.client.post(reverse("ui:hu_release", args=[self.hu.pk]), {"reason": "brak palety"})
        self.hu.refresh_from_db()
        self.assertIsNone(self.hu.assigned_to)
        self.assertIsNone(self.hu.called_at)

    def test_snooze_sets_until(self):
        self.client.post(reverse("ui:hu_release", args=[self.hu.pk]), {"snooze_min": "10"})
        self.hu.refresh_from_db()
        self.assertIsNone(self.hu.called_at)
        self.assertGreater(self.hu.snooze_until, timezone.now())

    def test_not_found_snoozes_and_alarms(self):
        # Pomiar duchów: jawne „nie znaleziono palety" → snooze 4 h, stała nota
        # w audycie, alarm do lidera (grill 2026-09-05, pyt. 56/73).
        from ui.models import HUStatusEvent, Notification
        self.client.post(reverse("ui:hu_release", args=[self.hu.pk]), {"not_found": "1"})
        self.hu.refresh_from_db()
        self.assertIsNone(self.hu.assigned_to)
        self.assertIsNotNone(self.hu.snooze_until)
        ev = HUStatusEvent.objects.filter(hu=self.hu, kind="release").first()
        self.assertIsNotNone(ev)
        self.assertIn("brak palety", ev.note)
        self.assertTrue(Notification.objects.filter(
            title__icontains="Brak palety").exists())
