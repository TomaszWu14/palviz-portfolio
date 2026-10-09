"""HU status audit trail: transitions are logged with who/when/why (Q46)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Shipment, HandlingUnit, HandlingUnitItem, HUStatusEvent
from ui.roles import GROUP_CONTROLLER


def _controller(name="ctrl"):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class StatusAuditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.controller = _controller()
        cls.sh = Shipment.objects.create(name="Dostawa")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HU1", status="planned")
        cls.item = HandlingUnitItem.objects.create(hu=cls.hu, ref_code="A1", base_qty=1)

    def test_full_cycle_logs_status_events(self):
        self.client.force_login(self.controller)
        # Jawny POST startuje kontrolę (planned → in_control) — GET detail jest read-only (P5).
        self.client.post(reverse("ui:hu_control_start", args=[self.hu.pk]))
        # Count the single position OK, then post.
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
                         {"action": "confirm", "qty_base": "1"})
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))

        events = list(HUStatusEvent.objects.filter(hu=self.hu).order_by("created_at"))
        transitions = [(e.from_status, e.to_status) for e in events]
        self.assertIn(("planned", "in_control"), transitions)
        self.assertIn(("in_control", "ok"), transitions)
        # Every event records who did it.
        self.assertTrue(all(e.by_user == self.controller for e in events))

    def test_takeover_is_audited_even_when_status_unchanged(self):
        other = _controller("other")
        hu2 = HandlingUnit.objects.create(shipment=self.sh, seq=2, code="HU2",
                                          status="in_control", controlled_by=other)
        self.client.force_login(self.controller)
        self.client.post(reverse("ui:hu_control_takeover", args=[hu2.pk]))
        hu2.refresh_from_db()
        self.assertEqual(hu2.controlled_by, self.controller)
        ev = HUStatusEvent.objects.filter(hu=hu2).first()
        self.assertIsNotNone(ev)                      # take-over logged though status stays in_control
        self.assertEqual((ev.from_status, ev.to_status), ("in_control", "in_control"))
        self.assertEqual(ev.by_user, self.controller)
