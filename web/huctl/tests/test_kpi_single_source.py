"""Pkt 3 — KPI panelu lidera z JEDNEGO źródła (HandlingUnit), spójne z widokiem stref.
- „kontrolerów dziś" liczy kontrolerów, którzy DOTKNĘLI kontroli (trzymają HU), nie tylko
  autorów prób audytu (naprawa „0 przy 14 w kontroli"),
- sumy kafli (in_control / to_recheck) == sumy per-strefa z widoku stref."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import HandlingUnit, Shipment
from ui.roles import GROUP_CONTROLLER


def _ctrl(name):
    u = get_user_model().objects.create_user(name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class KpiSingleSourceTests(TestCase):
    def setUp(self):
        self.lead = get_user_model().objects.create_superuser("lead", "l@l.pl", "x")
        self.c1, self.c2 = _ctrl("c1"), _ctrl("c2")
        self.sh = Shipment.objects.create(name="D1")
        now = timezone.now()
        mk = lambda seq, wt, st, by=None: HandlingUnit.objects.create(
            shipment=self.sh, seq=seq, code=f"H{seq}", warehouse_type=wt, status=st,
            controlled_by=by, control_started_at=now if st == "in_control" else None)
        mk(1, "92EX", "in_control", self.c1)
        mk(2, "92EX", "in_control", self.c1)
        mk(3, "92GE", "in_control", self.c2)
        mk(4, "92EX", "to_recheck")
        mk(5, "92EX", "planned")
        self.client.force_login(self.lead)

    def test_controllers_today_counts_holders_without_attempts(self):
        r = self.client.get(reverse("ui:hu_control_leader"))
        # c1 (2 HU) + c2 (1 HU) trzymają kontrolę, choć NIE zapisali jeszcze próby audytu.
        self.assertEqual(r.context["controllers_today"], 2)
        self.assertEqual(len(r.context["live"]), 3)          # in_control
        self.assertEqual(len(r.context["rechecks"]), 1)      # to_recheck

    def test_kpi_matches_zone_view_sums(self):
        lead_ctx = self.client.get(reverse("ui:hu_control_leader")).context
        zones_ctx = self.client.get(reverse("ui:hu_control_status")).context
        zone_in_control = sum(z["in_control"] for z in zones_ctx["zones"])
        zone_recheck = sum(z["to_recheck"] for z in zones_ctx["zones"])
        self.assertEqual(len(lead_ctx["live"]), zone_in_control)
        self.assertEqual(len(lead_ctx["rechecks"]), zone_recheck)
        self.assertEqual(zone_in_control, zones_ctx["summary"]["in_control"])
