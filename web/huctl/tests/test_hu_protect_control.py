"""DB-002 + BIZ-001: dane kontroli HU nie giną przy regeneracji HU, usunięciu przesyłki
ani reimporcie feedu SAP — HU w kontroli (status ≠ planned lub z próbami) są chronione."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from huctl.models import HUStatusEvent
from huctl.views.hu import _generate_handling_units, _import_hu_rows
from ui.models import (
    HandlingUnit, HandlingUnitItem, PalletizationInstruction, Product, Shipment, ShipmentLine,
)

from .test_hu_control import _user_all_roles


class ProtectControlledHUTests(TestCase):
    def setUp(self):
        self.user = _user_all_roles()
        self.client.force_login(self.user)
        self.sh = Shipment.objects.create(name="Dostawa P", stowage_efficiency_pct=80)
        p = Product.objects.create(code="NL100-100", name="NONVI lux")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=10, demand_pcs=100, is_active=True)
        ShipmentLine.objects.create(shipment=self.sh, product=p, quantity=8, unit="kar",
                                    source_unit="OP")
        _generate_handling_units(self.sh)
        self.hu = self.sh.handling_units.first()

    def _mark_ok(self):
        HandlingUnit.objects.filter(pk=self.hu.pk).update(status="ok", verified_at=timezone.now())
        HUStatusEvent.objects.create(hu=self.hu, from_status="planned", to_status="ok",
                                     by_user=self.user)

    def test_regeneration_refused_when_hu_controlled(self):
        self._mark_ok()
        n_hu = HandlingUnit.objects.count()
        resp = self.client.post(reverse("ui:planner_shipment_generate_hus", args=[self.sh.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(HandlingUnit.objects.count(), n_hu)
        self.assertTrue(HandlingUnit.objects.filter(pk=self.hu.pk, status="ok").exists())
        self.assertEqual(HUStatusEvent.objects.filter(hu_id=self.hu.pk).count(), 1)

    def test_delete_refused_when_hu_controlled(self):
        self._mark_ok()
        resp = self.client.post(reverse("ui:planner_shipment_delete", args=[self.sh.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Shipment.objects.filter(pk=self.sh.pk).exists())
        self.assertEqual(HUStatusEvent.objects.filter(hu_id=self.hu.pk).count(), 1)

    def test_planned_only_regeneration_and_delete_work(self):
        old_pk = self.hu.pk
        self.client.post(reverse("ui:planner_shipment_generate_hus", args=[self.sh.pk]))
        self.assertFalse(HandlingUnit.objects.filter(pk=old_pk).exists())   # przebudowane
        self.assertTrue(self.sh.handling_units.exists())
        self.client.post(reverse("ui:planner_shipment_delete", args=[self.sh.pk]))
        self.assertFalse(Shipment.objects.filter(pk=self.sh.pk).exists())


HDR = ["dostawa", "pickhu", "ref", "ilość", "jm"]


class ReimportProtectsControlledItemsTests(TestCase):
    def setUp(self):
        Product.objects.create(code="RG-50", name="Rękawice")
        ok, _ = _import_hu_rows(HDR, [["81782091", "HU001", "RG-50", "80", "OP"],
                                      ["81782091", "HU002", "RG-50", "40", "OP"]])
        self.assertTrue(ok)

    def test_controlled_hu_items_kept_planned_replaced(self):
        hu1 = HandlingUnit.objects.get(code="HU001")
        HandlingUnitItem.objects.filter(hu=hu1).update(counted_qty=80)
        HandlingUnit.objects.filter(pk=hu1.pk).update(status="ok", verified_at=timezone.now())
        ok, info = _import_hu_rows(HDR, [["81782091", "HU001", "RG-50", "55", "OP"],
                                         ["81782091", "HU002", "RG-50", "33", "OP"]])
        self.assertTrue(ok)
        it1 = HandlingUnitItem.objects.get(hu__code="HU001")
        self.assertEqual((it1.expected_qty, it1.counted_qty), (80, 80))   # zachowane
        self.assertEqual(HandlingUnitItem.objects.get(hu__code="HU002").expected_qty, 33)
        self.assertEqual(info["skipped_locked"], 1)
        self.assertEqual(info["items"], 1)

    def test_file_import_reports_skipped(self):
        HandlingUnit.objects.filter(code="HU001").update(status="ok", verified_at=timezone.now())
        self.client.force_login(_user_all_roles("imp2"))
        f = SimpleUploadedFile("hu.csv", "Dostawa;pickHU;REF;Ilość;JM\n80670980;HU001;RG-50;5;OP\n"
                               .encode("utf-8"), content_type="text/csv")
        resp = self.client.post(reverse("ui:planner_hu_import"), {"file": f}, follow=True)
        self.assertContains(resp, "w kontroli")
