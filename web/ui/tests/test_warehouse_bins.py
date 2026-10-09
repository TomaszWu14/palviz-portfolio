"""BLOK D — widok „Miejsca składowania": puste/zablokowane z filtrami w sesji."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import WarehouseSnapshot, WarehouseSnapshotRow
from ui.roles import GROUP_WAREHOUSE


def _user():
    u = get_user_model().objects.create_user(username="bins", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_WAREHOUSE)[0])
    return u


def _row(snap, code, wt="0052", empty=True, bp=False, col="A", stack="100", aisle="01"):
    return WarehouseSnapshotRow.objects.create(
        snapshot=snap, location_code=code, warehouse_type=wt, is_empty=empty,
        blocked_pick=bp, zone="B0", aisle=aisle, stack=stack, col_code=col)


class WarehouseBinsTests(TestCase):
    def setUp(self):
        self.client.force_login(_user())
        self.snap = WarehouseSnapshot.objects.create(name="S1")
        _row(self.snap, "B0-01-300A", "0052", empty=True, stack="300")
        _row(self.snap, "B0-01-100C-1", "0010", empty=True, col="C-1", stack="100")
        _row(self.snap, "B0-02-200B", "0011", empty=False, bp=True, stack="200")
        _row(self.snap, "B0-02-400D", "9010", empty=True, stack="400")
        _row(self.snap, "B0-03-500A", "0052", empty=False)      # zajęte, nieblok. → poza listą

    def test_lists_only_empty_or_blocked_sorted_by_stack(self):
        r = self.client.get(reverse("ui:warehouse_bins"))
        codes = [row.location_code for row in r.context["rows"]]
        self.assertEqual(codes, ["B0-01-100C-1", "B0-02-200B", "B0-01-300A", "B0-02-400D"])
        self.assertNotIn("B0-03-500A", codes)                   # zajęte niepokazywane

    def test_type_filter_groups_0010_0011(self):
        r = self.client.get(reverse("ui:warehouse_bins"), {"t": "0010"})
        codes = {row.location_code for row in r.context["rows"]}
        self.assertEqual(codes, {"B0-01-100C-1", "B0-02-200B"})  # 0010 i 0011 razem

    def test_size_and_status_filters_with_counts(self):
        r = self.client.get(reverse("ui:warehouse_bins"), {"s": "half"})
        self.assertEqual([row.location_code for row in r.context["rows"]], ["B0-01-100C-1"])
        r = self.client.get(reverse("ui:warehouse_bins"), {"s": "all", "st": "blocked"})
        self.assertEqual([row.location_code for row in r.context["rows"]], ["B0-02-200B"])
        self.assertEqual(r.context["counts"]["st_empty"], 3)
        self.assertEqual(r.context["counts"]["st_blocked"], 1)

    def test_filters_persist_in_session(self):
        self.client.get(reverse("ui:warehouse_bins"), {"t": "0052", "st": "empty"})
        r = self.client.get(reverse("ui:warehouse_bins"))       # bez parametrów
        self.assertEqual(r.context["f_type"], "0052")
        self.assertEqual(r.context["f_status"], "empty")
        self.assertEqual([row.location_code for row in r.context["rows"]], ["B0-01-300A"])

    def test_no_snapshot_renders_hint(self):
        WarehouseSnapshot.objects.all().delete()
        r = self.client.get(reverse("ui:warehouse_bins"))
        self.assertContains(r, "Brak snapshotu")
