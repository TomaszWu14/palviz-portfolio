"""Poprawki UX skanera: blokada re-liczenia własnej niezgodności + zgłoszenie AJM."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import HandlingUnit, HandlingUnitItem, Shipment, PackagingIssue
from ui.roles import GROUP_CONTROLLER


def _ctrl(name):
    u = get_user_model().objects.create_user(name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class ErrorLockTests(TestCase):
    def setUp(self):
        self.u = _ctrl("c1")
        self.client.force_login(self.u)
        self.sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=1, code="H1",
                                              status="in_control", controlled_by=self.u,
                                              warehouse_type="92EX")
        self.item = HandlingUnitItem.objects.create(hu=self.hu, ref_code="A1",
                                                    base_unit="OP", base_qty=10)

    def _count(self, data):
        return self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]), data)

    def test_original_controller_cannot_recount_own_error(self):
        # wpisz złą ilość i potwierdź „jestem pewien" → błąd pickera (result=error)
        self._count({"qty_base": "7", "action": "confirm", "sure": "1"})
        self.item.refresh_from_db()
        self.assertEqual(self.item.result, "error")
        self.assertEqual(self.item.counted_qty, 7)
        # próba ponownego policzenia przez TEGO SAMEGO kontrolera — odrzucona, bez zmiany
        self._count({"qty_base": "10", "action": "confirm", "sure": "1"})
        self.item.refresh_from_db()
        self.assertEqual(self.item.result, "error")
        self.assertEqual(self.item.counted_qty, 7)          # nie nadpisano na 10
        # „Edytuj" też zablokowane
        self._count({"action": "edit"})
        self.item.refresh_from_db()
        self.assertTrue(self.item.controlled)               # nie odblokowano


class MdAjmReportTests(TestCase):
    def test_ajm_report_builds_conversion(self):
        u = _ctrl("c2"); self.client.force_login(u)
        sh = Shipment.objects.create(name="D2")
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="H2", warehouse_type="92EX")
        it = HandlingUnitItem.objects.create(hu=hu, ref_code="B1", base_unit="OP", base_qty=5)
        self.client.post(reverse("ui:hu_md_report", args=[it.pk]),
                         {"md_aspect": "ajm", "md_unit": "karton", "md_factor": "10", "md_base": "OP"})
        iss = PackagingIssue.objects.get(ref_code="B1")
        self.assertEqual(iss.issue_type, "wrong_conversion")
        self.assertIn("1 KARTON = 10 OP", iss.description)
