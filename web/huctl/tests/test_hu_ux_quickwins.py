"""UX quick-wins z grilla 2026-09-05: VIP widoczny na listach operatora (pyt. 7)
i licznik czasu kontroli na karcie HU (pyt. 27)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import ALL_GROUPS


def _user():
    u = get_user_model().objects.create_user(username="ux", password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class UXQuickwinsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user()
        cls.vip = Customer.objects.create(name="VIP Klient", is_vip=True, kunnr="111")
        cls.sh = Shipment.objects.create(name="D1", customer=cls.vip,
                                         recipient_name="VIP Klient")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HV1",
                                             status="planned", warehouse_type="WT01")

    def setUp(self):
        self.client.force_login(self.user)

    def test_vip_badge_in_find_recipient(self):
        r = self.client.get(reverse("ui:hu_control_find_recipient"), {"q": "VIP"})
        self.assertContains(r, "lucide.svg#star")  # ikona Lucide zamiast emoji ★ (etap 3 UX)
        self.assertContains(r, "</svg> VIP")

    def test_vip_badge_on_status_screen(self):
        r = self.client.get(reverse("ui:hu_control_status"))
        self.assertContains(r, "lucide.svg#star")  # ikona Lucide zamiast emoji ★ (etap 3 UX)
        self.assertContains(r, "</svg> VIP")

    def test_incontrol_timer_on_detail(self):
        HandlingUnit.objects.filter(pk=self.hu.pk).update(
            status="in_control", controlled_by=self.user,
            control_started_at=timezone.now() - timedelta(hours=2))
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertContains(r, "w kontroli")

    def test_scanner_base_has_touch_targets(self):
        # Grill pyt. 18: przyciski/pola ≥44px na ekranach skanera (poza kompaktem Zebra).
        r = self.client.get(reverse("ui:hu_control_menu"))
        self.assertContains(r, "min-height: 44px")
