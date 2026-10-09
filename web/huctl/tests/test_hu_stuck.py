"""HU utknęła w kontroli: kontrola rozpoczęta (in_control), ale nieukończona ponad
HU_INCONTROL_MAX_HOURS → sygnał na hubie lidera + zadanie (dedup)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import Shipment, HandlingUnit, Task
from ui.roles import GROUP_ADMIN, GROUP_LEADER


def _leader(name="lead"):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in (GROUP_ADMIN, GROUP_LEADER):
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class HuStuckTests(TestCase):
    def test_hub_flags_and_raises_task_for_stuck_control(self):
        leader = _leader()
        sh = Shipment.objects.create(name="Stock", is_stock=True)
        now = timezone.now()
        stuck = HandlingUnit.objects.create(shipment=sh, seq=1, code="STUCK",
                                            status="in_control",
                                            control_started_at=now - timedelta(hours=3))
        fresh = HandlingUnit.objects.create(shipment=sh, seq=2, code="FRESH",
                                            status="in_control", control_started_at=now)
        planned = HandlingUnit.objects.create(shipment=sh, seq=3, code="PLANNED",
                                              status="planned",
                                              control_started_at=now - timedelta(hours=3))
        self.client.force_login(leader)
        r = self.client.get(reverse("ui:hu_control_hub"))
        stuck_hus = r.context["stuck_hus"]
        self.assertIn(stuck, stuck_hus)              # in_control ponad próg
        self.assertNotIn(fresh, stuck_hus)           # dopiero zaczęta
        self.assertNotIn(planned, stuck_hus)         # nie w kontroli
        # zadanie dla lidera powstało, dedupowane (jedno na HU/miesiąc)
        self.assertEqual(Task.objects.filter(category="hu_stuck", related_hu=stuck).count(), 1)
        self.client.get(reverse("ui:hu_control_hub"))
        self.assertEqual(Task.objects.filter(category="hu_stuck", related_hu=stuck).count(), 1)
