"""Ekrany odczytowe modułu kontroli muszą respektować macierz stref (ControllerZone).

Bramki strefowe pilnowały dotąd wyłącznie akcji zapisujących. Historia palety, ekran
statusu, raport błędów i lista zgłoszeń jakościowych pokazywały wszystko — łącznie
z imiennymi danymi pickerów z obszarów, do których kontroler nie ma dostępu."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import (ControllerZone, HandlingUnit, HandlingUnitItem,
                       HUQualityIssue, Shipment)
from ui.roles import GROUP_CONTROLLER


class ZoneScopedReadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="strefowy", password="x")
        cls.user.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        # Kontroler ma dostęp WYŁĄCZNIE do WT01.
        ControllerZone.objects.create(user=cls.user, code="WT01")

        cls.sh = Shipment.objects.create(name="D1")
        cls.mine = HandlingUnit.objects.create(
            shipment=cls.sh, seq=1, code="MOJA", warehouse_type="WT01", picker="Kowalski")
        cls.theirs = HandlingUnit.objects.create(
            shipment=cls.sh, seq=2, code="OBCA", warehouse_type="WT99", picker="Nowak")

        for hu in (cls.mine, cls.theirs):
            HandlingUnitItem.objects.create(
                hu=hu, ref_code="RG-50", base_unit="OP", base_qty=10,
                controlled=True, result="error", error_flags={"damaged": True})
            HUQualityIssue.objects.create(hu=hu, issue_type="damaged", status="open")

    def setUp(self):
        self.client.force_login(self.user)

    def test_history_of_foreign_zone_is_refused(self):
        resp = self.client.get(reverse("ui:hu_control_history", args=[self.theirs.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_history_of_own_zone_works(self):
        resp = self.client.get(reverse("ui:hu_control_history", args=[self.mine.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_status_screen_hides_foreign_zone(self):
        body = self.client.get(reverse("ui:hu_control_status")).content.decode()
        self.assertIn("MOJA", body)
        self.assertNotIn("OBCA", body)

    def test_error_report_hides_foreign_zone_pickers(self):
        body = self.client.get(reverse("ui:hu_error_report")).content.decode()
        self.assertIn("Kowalski", body)
        self.assertNotIn("Nowak", body)

    def test_quality_list_hides_foreign_zone(self):
        body = self.client.get(reverse("ui:hu_quality_issues")).content.decode()
        self.assertIn("MOJA", body)
        self.assertNotIn("OBCA", body)

    def test_closing_foreign_zone_issue_is_refused(self):
        iss = HUQualityIssue.objects.get(hu=self.theirs)
        self.client.post(reverse("ui:hu_quality_close", args=[iss.pk]),
                         {"resolution_note": "próba"})
        iss.refresh_from_db()
        self.assertEqual(iss.status, "open")

    def test_closing_own_zone_issue_works(self):
        iss = HUQualityIssue.objects.get(hu=self.mine)
        self.client.post(reverse("ui:hu_quality_close", args=[iss.pk]),
                         {"resolution_note": "wymieniono karton"})
        iss.refresh_from_db()
        self.assertEqual(iss.status, "closed")

    def test_reporting_foreign_item_in_foreign_zone_is_refused(self):
        before = HUQualityIssue.objects.filter(hu=self.theirs).count()
        self.client.post(reverse("ui:hu_quality_add_foreign", args=[self.theirs.pk]),
                         {"ref_code": "XX-1", "qty": "1"})
        self.assertEqual(HUQualityIssue.objects.filter(hu=self.theirs).count(), before)
