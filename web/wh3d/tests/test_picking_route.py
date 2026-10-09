"""Magazyn 3D — trasa kompletacji (wężowe uporządkowanie lokalizacji)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import WarehouseSnapshot, WarehouseSnapshotRow
from wh3d.views.warehouse_map import _serpentine_route


class SerpentineUnitTests(TestCase):
    def test_snake_alternates_stack_direction(self):
        # aisle 1: stacks 10,20 ; aisle 2: stacks 30,40
        idx = {
            "A": (1, 10, 1), "B": (1, 20, 1),
            "C": (2, 30, 1), "D": (2, 40, 1),
        }
        ordered, unknown = _serpentine_route(["D", "C", "B", "A"], idx)
        # aisle 1 ascending (10,20) → A,B ; aisle 2 descending (40,30) → D,C
        self.assertEqual(ordered, ["A", "B", "D", "C"])
        self.assertEqual(unknown, [])

    def test_level_orders_within_stack(self):
        idx = {"L1": (1, 10, 1), "L3": (1, 10, 3), "L2": (1, 10, 2)}
        ordered, _ = _serpentine_route(["L3", "L1", "L2"], idx)
        self.assertEqual(ordered, ["L1", "L2", "L3"])

    def test_unknown_separated(self):
        idx = {"A": (1, 10, 1)}
        ordered, unknown = _serpentine_route(["A", "ZZZ"], idx)
        self.assertEqual(ordered, ["A"])
        self.assertEqual(unknown, ["ZZZ"])


class PickingRouteViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("p", password="x")
        cls.user.groups.add(Group.objects.get_or_create(name="Podgląd")[0])  # _planner wymaga roli
        snap = WarehouseSnapshot.objects.create(name="Snap")

        def row(code, aisle, stack, level):
            WarehouseSnapshotRow.objects.create(snapshot=snap, location_code=code,
                                                aisle=aisle, stack=stack, level=level)
        row("A1-01-100A", "01", "100", 1)
        row("A1-02-200A", "02", "200", 1)
        row("A1-01-110A", "01", "110", 1)

    def setUp(self):
        self.client.force_login(self.user)

    def test_get_renders_form(self):
        r = self.client.get(reverse("ui:warehouse_picking_route"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["route"], [])

    def test_post_orders_route(self):
        r = self.client.post(reverse("ui:warehouse_picking_route"),
                             {"locations": "A1-02-200A\nA1-01-110A\nA1-01-100A"})
        codes = [s["code"] for s in r.context["route"]]
        # aisle 01 first (ascending stack 100,110), then aisle 02
        self.assertEqual(codes, ["A1-01-100A", "A1-01-110A", "A1-02-200A"])
        self.assertEqual(r.context["unknown"], [])

    def test_post_flags_unknown(self):
        r = self.client.post(reverse("ui:warehouse_picking_route"),
                             {"locations": "A1-01-100A, NIEZNANA"})
        # Known codes form the numbered route; unknowns are reported separately.
        self.assertEqual([s["code"] for s in r.context["route"]], ["A1-01-100A"])
        self.assertEqual(r.context["unknown"], ["NIEZNANA"])

    def test_dedupes_input(self):
        r = self.client.post(reverse("ui:warehouse_picking_route"),
                             {"locations": "A1-01-100A\nA1-01-100A"})
        self.assertEqual(len(r.context["route"]), 1)
