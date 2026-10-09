"""Leader TV wall dashboard: read-only, leader-only, shows live status + today's KPI."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import Shipment, HandlingUnit, HandlingUnitItem
from ui.roles import GROUP_ADMIN, GROUP_LEADER, GROUP_CONTROLLER


def _user(*groups, name="u"):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class TvModeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.leader = _user(GROUP_ADMIN, GROUP_LEADER, name="lead")
        cls.controller = _user(GROUP_CONTROLLER, name="c1")
        cls.sh = Shipment.objects.create(name="S")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="LIVE",
                                             status="in_control", controlled_by=cls.controller,
                                             control_started_at=timezone.now())
        HandlingUnitItem.objects.create(hu=cls.hu, ref_code="A1", base_qty=1)

    def test_leader_sees_tv_dashboard(self):
        self.client.force_login(self.leader)
        r = self.client.get(reverse("ui:hu_control_tv"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["status_counts"]["in_control"], 1)
        self.assertEqual(len(r.context["live"]), 1)
        self.assertContains(r, "Kontrola HU — na żywo")
        self.assertContains(r, "http-equiv=\"refresh\"")     # auto-refresh

    def test_controller_cannot_see_tv(self):
        self.client.force_login(self.controller)
        r = self.client.get(reverse("ui:hu_control_tv"))
        self.assertEqual(r.status_code, 403)                 # leader/admin only
