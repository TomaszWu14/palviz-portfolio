"""KPI per strefa (typ magazynu) — przełącznik przekroju z grillowania."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from ui.models import Shipment, HandlingUnit, HUControlAttempt
from huctl.views.hu_control import _kpi_stats


class KpiByZoneTest(TestCase):
    def test_group_by_zone(self):
        sh = Shipment.objects.create(name="D-Z")
        hu_a = HandlingUnit.objects.create(shipment=sh, seq=1, code="HA",
                                           status="in_control", warehouse_type="92JU")
        hu_b = HandlingUnit.objects.create(shipment=sh, seq=2, code="HB",
                                           status="in_control", warehouse_type="92EX")
        c = User.objects.create_user("kpi_z")
        HUControlAttempt.objects.create(hu=hu_a, item=hu_a.items.create(ref_code="R1"),
                                        controller=c, result="ok")
        HUControlAttempt.objects.create(hu=hu_b, item=hu_b.items.create(ref_code="R2"),
                                        controller=c, result="ok")
        start = timezone.now() - timedelta(hours=1)
        end = timezone.now() + timedelta(hours=1)
        rows, _ = _kpi_stats(start, end, by="zone")
        labels = {r["controller"] for r in rows}                 # 'controller' = etykieta wiersza
        self.assertEqual(labels, {"92JU", "92EX"})               # przekrój per strefa

    def test_default_still_by_controller(self):
        sh = Shipment.objects.create(name="D-C")
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HC",
                                         status="in_control", warehouse_type="92JU")
        c = User.objects.create_user("kpi_ctrl")
        HUControlAttempt.objects.create(hu=hu, item=hu.items.create(ref_code="R"),
                                        controller=c, result="ok")
        start = timezone.now() - timedelta(hours=1)
        end = timezone.now() + timedelta(hours=1)
        rows, _ = _kpi_stats(start, end)                         # domyślnie per kontroler
        self.assertEqual({r["controller"] for r in rows}, {"kpi_ctrl"})
