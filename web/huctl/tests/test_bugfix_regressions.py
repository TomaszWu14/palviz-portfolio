"""Regression tests for a batch of correctness/security fixes.

Each test pins a specific bug that was fixed: role-badge precedence, the
non-unique pickHU resolve, the container fit guard, the task due-date parse,
and the layout-export robustness.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.test import TestCase

from ui.models import HandlingUnit, Shipment
from ui.roles import (GROUP_CONTROLLER, GROUP_LEADER, ROLE_LABELS,
                      get_user_primary_role)
from huctl.views.hu_control import _resolve_hu


class RolePrecedenceTests(TestCase):
    def test_leader_outranks_controller_badge(self):
        """A user who is both Lider kontroli and Kontrola HU shows the Leader badge,
        not whichever role happens to come first in ALL_GROUPS."""
        u = get_user_model().objects.create_user(username="dual", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        u.groups.add(Group.objects.get_or_create(name=GROUP_LEADER)[0])
        self.assertEqual(get_user_primary_role(u), ROLE_LABELS[GROUP_LEADER])


class ResolveHuAmbiguityTests(TestCase):
    def test_shared_pickhu_is_impossible(self):
        """A pickHU is the label of ONE physical pallet, so the stock-vs-transport
        duplicate this test used to assert can no longer exist.

        A pallet that leaves stock for a delivery is the same pallet: the import
        re-points the existing HU (see test_hu_code_unique) instead of creating a
        second row, so the controller always lands on the current one — which is
        what the old 'prefer transport' preference was approximating."""
        stock = Shipment.objects.create(name="STOCK", is_stock=True)
        transport = Shipment.objects.create(name="DELIVERY", is_stock=False)
        HandlingUnit.objects.create(shipment=stock, seq=1, code="SSCC-1")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                HandlingUnit.objects.create(shipment=transport, seq=1, code="SSCC-1")

    def test_case_insensitive_resolve(self):
        sh = Shipment.objects.create(name="D", is_stock=False)
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="AbC123")
        self.assertEqual(_resolve_hu("abc123").pk, hu.pk)


class DeepBugHuntRegressions(TestCase):
    """Poprawki z głębokiego bug-huntu 2026-09-03 (3 soczewki: race/security/logika)."""

    def setUp(self):
        from django.urls import reverse as _r
        self._r = _r
        self.u = get_user_model().objects.create_user("ctrl2", password="x")
        self.u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        self.sh = Shipment.objects.create(name="DBH")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=1, code="HB1",
                                              warehouse_type="92EX")
        self.client.force_login(self.u)

    def test_request_bring_no_500_double_redirect(self):
        """S1: redirect(_safe_referer(...)) rzucał NoReverseMatch → 500 na każdy klik."""
        r = self.client.post(self._r("ui:hu_request_bring", args=[self.hu.pk]))
        self.assertEqual(r.status_code, 302)

    def test_parse_count_form_rejects_garbage_not_zero(self):
        """Nieparsowalna NIEPUSTA ilość → błąd walidacji, nie ciche 0 (fałszywy błąd pickera)."""
        from huctl.views.hu_count import parse_count_form
        from ui.models import HandlingUnitItem
        item = HandlingUnitItem.objects.create(hu=self.hu, ref_code="R1", base_qty=10)
        _, _, err = parse_count_form({"qty_base": "1..2"}, item)
        self.assertIsNotNone(err)

    def test_families_counts_escaped_as_started(self):
        from huctl.views.hu_dashboard import families_in_progress
        sh2 = Shipment.objects.create(name="DBH2", kunnr="900")
        h1 = HandlingUnit.objects.create(shipment=sh2, seq=1, code="HE1",
                                         warehouse_type="92EX", status="escaped")
        h2 = HandlingUnit.objects.create(shipment=sh2, seq=2, code="HE2",
                                         warehouse_type="92EX")
        self.assertIn("900", families_in_progress([h1, h2]))

    def test_investigation_detail_blocked_for_viewer_role(self):
        """S3: rola Podgląd nie enumeruje wyjaśnień (dane pickerów/stref)."""
        from huctl.models_control import HUErrorInvestigation
        from ui.roles import GROUP_VIEWER
        inv = HUErrorInvestigation.objects.create(hu=self.hu, controller=self.u,
                                                  error_type="missing")
        viewer = get_user_model().objects.create_user("viewer1", password="x")
        viewer.groups.add(Group.objects.get_or_create(name=GROUP_VIEWER)[0])
        self.client.force_login(viewer)
        r = self.client.get(self._r("ui:hu_investigation_detail", args=[inv.pk]))
        self.assertEqual(r.status_code, 302)   # odbity, nie wyrenderowany
