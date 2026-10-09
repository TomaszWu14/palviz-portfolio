"""Liczenie „w ciemno": zgodność partii dostawcy i daty ważności jest DOMYŚLNA — bez
checkboxów potwierdzenia. Pozycja z poprawną ilością domyka się „OK" bez potwierdzania.
Niezgodność partii/daty zgłasza się flagą błędu (wrong_batch / wrong_expiry), która
tworzy zgłoszenie jakościowe (osobny tor, nie wymusza przeliczenia ilości)."""
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import HandlingUnit, HandlingUnitItem, HUQualityIssue, Shipment
from ui.roles import GROUP_CONTROLLER


def _controller(name="cnt"):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class BlindCountBatchExpiryTests(TestCase):
    def setUp(self):
        self.client.force_login(_controller())
        self.sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=1, code="CNT1",
                                              status="in_control", warehouse_type="WT01")

    def _item(self, vb="193716114N", exp=date(2031, 5, 1)):
        return HandlingUnitItem.objects.create(
            hu=self.hu, ref_code="RG-50", base_unit="OP", base_qty=10,
            vendor_batch=vb, expiry=exp)

    def _count(self, item, data):
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, item.pk]),
                         {"action": "confirm", "qty_base": "10", **data})
        item.refresh_from_db()
        return item

    def test_correct_count_ok_without_confirmation(self):
        # Brak checkboxów — poprawna ilość zamyka pozycję „OK" bez potwierdzania partii/daty.
        it = self._count(self._item(), {})
        self.assertEqual(it.result, "ok")

    def test_wrong_expiry_flag_raises_quality_issue(self):
        it = self._count(self._item(), {"flag_wrong_expiry": "on"})
        self.assertTrue(HUQualityIssue.objects.filter(
            item=it, issue_type="wrong_expiry", status="open").exists())

    def test_wrong_batch_flag_raises_quality_issue(self):
        it = self._count(self._item(), {"flag_wrong_batch": "on"})
        self.assertTrue(HUQualityIssue.objects.filter(
            item=it, issue_type="wrong_batch", status="open").exists())

    def test_md_report_creates_packaging_issue(self):
        # Master data przeniesione z kodów błędów do osobnego zgłoszenia MATINFO (PackagingIssue).
        from ui.models import PackagingIssue
        it = self._item()
        self.client.post(reverse("ui:hu_md_report", args=[it.pk]),
                         {"md_aspect": "przelicznik", "md_desc": "zły przelicznik KAR"})
        iss = PackagingIssue.objects.get(ref_code="RG-50")
        self.assertEqual(iss.issue_type, "wrong_conversion")      # „przelicznik" → wrong_conversion
        self.assertIn("zły przelicznik KAR", iss.description)
        it.refresh_from_db()
        self.assertFalse(it.md_exception)                        # nie ustawia już wyjątku/kodu błędu
