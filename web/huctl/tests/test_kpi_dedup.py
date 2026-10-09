"""Regresja: _kpi_stats totals liczą UNIKALNE HU/pozycje (unia), nie sumę per-kontroler.
HU po rekontroli dotyka jej DRUGI kontroler → wcześniej liczona 2× w totals."""
from datetime import timedelta

from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone

from ui.models import Shipment, HandlingUnit, HUControlAttempt
from huctl.views.hu_control import _kpi_stats


class KpiTotalsDedupTest(TestCase):
    def test_hu_touched_by_two_controllers_counted_once(self):
        sh = Shipment.objects.create(name="D-KPI")
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HUK", status="in_control")
        it = hu.items.create(ref_code="R1", base_unit="OP", base_qty=10)
        c1 = User.objects.create_user("kpi_c1")
        c2 = User.objects.create_user("kpi_c2")
        # Oryginalna kontrola (c1) + rekontrola (c2) tej samej pozycji.
        HUControlAttempt.objects.create(hu=hu, item=it, controller=c1, result="ok")
        HUControlAttempt.objects.create(hu=hu, item=it, controller=c2, is_recheck=True, result="ok")

        start = timezone.now() - timedelta(hours=1)
        end = timezone.now() + timedelta(hours=1)
        rows, totals = _kpi_stats(start, end)

        self.assertEqual(totals["hus"], 1, "HU po rekontroli musi liczyć się raz (unia), nie 2×")
        self.assertEqual(totals["positions"], 1, "distinct pozycja (hu,item) = 1")
        self.assertEqual(totals["controllers"], 2)
