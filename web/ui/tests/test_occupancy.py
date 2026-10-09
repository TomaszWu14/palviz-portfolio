"""Magazyn — przegląd wypełnienia: KPI ogółem + alerty stref (niemal pełne / zablokowane)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import WarehouseSnapshot, WarehouseSnapshotRow


class OccupancyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("p", password="x")
        cls.user.groups.add(Group.objects.get_or_create(name="Podgląd")[0])  # _planner wymaga roli
        cls.snap = WarehouseSnapshot.objects.create(name="Migawka A")

        def row(zone, empty=False, bp=False, bput=False):
            WarehouseSnapshotRow.objects.create(snapshot=cls.snap, location_code=f"{zone}-x",
                                                zone=zone, is_empty=empty,
                                                blocked_pick=bp, blocked_put=bput)
        # Zone A: 9 occupied + 1 empty = 90% → near-full alert
        for _ in range(9):
            row("A")
        row("A", empty=True)
        # Zone B: 1 occupied (blocked) + 3 empty = 25% → blocked alert (not near-full)
        row("B", bp=True)
        for _ in range(3):
            row("B", empty=True)

    def setUp(self):
        self.client.force_login(self.user)

    def test_totals(self):
        ctx = self.client.get(reverse("ui:planner_occupancy")).context
        t = ctx["totals"]
        self.assertEqual(t["total"], 14)
        self.assertEqual(t["occupied"], 10)      # 9 (A) + 1 (B)
        self.assertEqual(t["empty"], 4)
        self.assertEqual(t["blocked"], 1)
        self.assertEqual(t["pct"], round(10 / 14 * 100))

    def test_near_full_alert(self):
        ctx = self.client.get(reverse("ui:planner_occupancy")).context
        near = [a for a in ctx["alerts"] if a["kind"] == "near_full"]
        self.assertEqual(len(near), 1)
        self.assertEqual(near[0]["zone"], "A")
        self.assertEqual(near[0]["pct"], 90)

    def test_blocked_alert(self):
        ctx = self.client.get(reverse("ui:planner_occupancy")).context
        blocked = [a for a in ctx["alerts"] if a["kind"] == "blocked"]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["zone"], "B")
        self.assertEqual(blocked[0]["count"], 1)

    def test_csv_export(self):
        resp = self.client.get(reverse("ui:planner_occupancy"), {"export": "csv"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", resp["Content-Disposition"])
        body = resp.content.decode("utf-8")
        self.assertIn("Strefa", body)            # header
        self.assertIn("RAZEM", body)             # totals row
        lines = [l for l in body.strip().splitlines() if l]
        # header + zone A + zone B + RAZEM = 4 rows
        self.assertEqual(len(lines), 4)

    def test_no_snapshot_empty_state(self):
        WarehouseSnapshotRow.objects.all().delete()
        WarehouseSnapshot.objects.all().delete()
        resp = self.client.get(reverse("ui:planner_occupancy"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["totals"]["pct"], 0)
        self.assertEqual(resp.context["alerts"], [])
