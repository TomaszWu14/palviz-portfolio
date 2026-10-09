"""Leader hub: live "who is controlling what now" + aging recheck/quality backlogs."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import Shipment, HandlingUnit, HandlingUnitItem, HUQualityIssue
from ui.roles import GROUP_ADMIN, GROUP_LEADER, GROUP_CONTROLLER


def _user(*groups, name="u"):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class LeaderLiveHubTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.leader = _user(GROUP_ADMIN, GROUP_LEADER, name="lead")
        cls.ctrl = _user(GROUP_CONTROLLER, name="c1")
        cls.sh = Shipment.objects.create(name="S")
        cls.live = HandlingUnit.objects.create(
            shipment=cls.sh, seq=1, code="LIVE", status="in_control",
            controlled_by=cls.ctrl, control_started_at=timezone.now())
        HandlingUnitItem.objects.create(hu=cls.live, ref_code="A1", base_qty=1)
        cls.rc = HandlingUnit.objects.create(shipment=cls.sh, seq=2, code="RECHK",
                                             status="to_recheck")
        cls.q = HUQualityIssue.objects.create(hu=cls.live, issue_type="damaged", status="open")

    def test_hub_shows_live_and_backlogs(self):
        self.client.force_login(self.leader)
        r = self.client.get(reverse("ui:hu_control_hub"))
        self.assertEqual(r.status_code, 200)
        live = r.context["live_controls"]
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["hu"].code, "LIVE")
        self.assertEqual(live[0]["controller"], self.ctrl)
        self.assertEqual((live[0]["done"], live[0]["total"]), (0, 1))   # annotated progress
        self.assertIn(self.rc, r.context["recheck_backlog"])
        self.assertIn(self.q, r.context["open_quality"])
        self.assertContains(r, "Kontrola na żywo")
