"""P1 #12 — powiadomienia na skanerze: dzwonek z badge, lista (auto-odczyt),
rekontrola powiadamia zakwestionowanego kontrolera."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Shipment, HandlingUnit, Notification
from ui.roles import GROUP_CONTROLLER
from huctl.views.hu_control import _notify_recheck


def _controller(name):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class ScannerNotifications(TestCase):
    def setUp(self):
        self.u = _controller("ctrl")
        self.client.force_login(self.u)

    def test_list_shows_and_marks_read(self):
        Notification.objects.create(recipient=self.u, title="HU X → do rekontroli",
                                    level="warning")
        r = self.client.get(reverse("ui:hu_notifications"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "HU X → do rekontroli")
        self.assertFalse(Notification.objects.filter(recipient=self.u, is_read=False).exists())

    def test_bell_removed_from_scanner_header(self):
        # Przebudowa 2026-09: dzwonek zniknął — jedynym kanałem w nagłówku jest koperta.
        Notification.objects.create(recipient=self.u, title="T1")
        r = self.client.get(reverse("ui:hu_control_menu"))
        self.assertNotContains(r, 'aria-label="Powiadomienia"')
        self.assertNotContains(r, reverse("ui:hu_notifications"))
        self.assertContains(r, 'id="msg-bell"')


class RecheckNotifiesController(TestCase):
    def test_controlled_by_gets_pinged(self):
        original = _controller("first")
        rechecker = _controller("second")
        sh = Shipment.objects.create(name="D")
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HU1",
                                         status="to_recheck", controlled_by=original)
        _notify_recheck(hu, by_user=rechecker)
        self.assertTrue(Notification.objects.filter(
            recipient=original, title__contains="wraca do rekontroli").exists())

    def test_self_report_not_self_notified(self):
        u = _controller("solo")
        sh = Shipment.objects.create(name="D")
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HU2",
                                         status="to_recheck", controlled_by=u)
        _notify_recheck(hu, by_user=u)
        self.assertFalse(Notification.objects.filter(
            recipient=u, title__contains="wraca do rekontroli").exists())
