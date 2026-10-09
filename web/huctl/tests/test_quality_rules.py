"""Quality-track rules: damage needs a photo, and closing a quality issue needs a
mandatory resolution note (audit trail)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import (Shipment, HandlingUnit, HandlingUnitItem, HUQualityIssue,
                       Customer)
from ui.roles import GROUP_CONTROLLER


def _controller(name="ctrl"):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class QualityRuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.controller = _controller()
        cls.sh = Shipment.objects.create(name="Dostawa")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HU1",
                                             status="in_control", controlled_by=cls.controller)
        cls.item = HandlingUnitItem.objects.create(hu=cls.hu, ref_code="A1",
                                                   base_qty=5, base_unit="OP")

    def test_damaged_requires_photo(self):
        self.client.force_login(self.controller)
        r = self.client.post(
            reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
            {"qty_base": "5", "flag_damaged": "on", "action": "confirm"})
        self.assertRedirects(r, reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.item.refresh_from_db()
        self.assertFalse(self.item.controlled)      # blocked without a photo

    def test_close_quality_needs_note(self):
        iss = HUQualityIssue.objects.create(hu=self.hu, item=self.item,
                                            issue_type="damaged", status="open")
        self.client.force_login(self.controller)
        # No note → stays open.
        self.client.post(reverse("ui:hu_quality_close", args=[iss.pk]), {"resolution_note": "  "})
        iss.refresh_from_db()
        self.assertEqual(iss.status, "open")
        # With a note → closed.
        self.client.post(reverse("ui:hu_quality_close", args=[iss.pk]),
                         {"resolution_note": "wymieniono"})
        iss.refresh_from_db()
        self.assertEqual(iss.status, "closed")
        self.assertEqual(iss.resolution_note, "wymieniono")

    def test_quality_flag_needs_no_photo(self):
        """Flagi jakościowe niewymagające zdjęcia (np. wrong_batch) domykają pozycję bez foto.
        (wrong_label/missing_document wygaszone — soft-delete — więc test na aktywnym kodzie.)"""
        self.client.force_login(self.controller)
        self.client.post(
            reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
            {"qty_base": "5", "flag_wrong_batch": "on", "action": "confirm"})
        self.item.refresh_from_db()
        self.assertTrue(self.item.controlled)
        self.assertTrue(HUQualityIssue.objects.filter(
            hu=self.hu, issue_type="wrong_batch", status="open").exists())


class CustomerRequirementFieldTests(TestCase):
    def test_wz_copies_surfaces_in_requirements(self):
        c = Customer.objects.create(name="ACME", wz_copies=3)
        self.assertTrue(c.has_requirements())
        self.assertIn("WZ w 3 kopiach", c.requirement_summary())


class QualityLoopVisibilityTests(TestCase):
    """Pętla zgłoszeń (grill 2026-09-05): zaległość widoczna w menu (pyt. 91),
    zwrotka do zgłaszającego przy zamknięciu (pyt. 29)."""

    @classmethod
    def setUpTestData(cls):
        cls.controller = _controller()
        cls.raiser = _controller("raiser")
        cls.sh = Shipment.objects.create(name="Dostawa")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HU1",
                                             status="in_control",
                                             controlled_by=cls.controller)
        cls.item = HandlingUnitItem.objects.create(hu=cls.hu, ref_code="A1",
                                                   base_qty=5, base_unit="OP")

    def test_menu_shows_open_quality_count(self):
        HUQualityIssue.objects.create(hu=self.hu, item=self.item,
                                      issue_type="damaged", status="open",
                                      raised_by=self.raiser)
        self.client.force_login(self.controller)
        r = self.client.get(reverse("ui:hu_control_menu"))
        self.assertContains(r, "1 otwarte")

    def test_close_notifies_raiser(self):
        from ui.models import Notification
        iss = HUQualityIssue.objects.create(hu=self.hu, item=self.item,
                                            issue_type="damaged", status="open",
                                            raised_by=self.raiser)
        self.client.force_login(self.controller)
        self.client.post(reverse("ui:hu_quality_close", args=[iss.pk]),
                         {"resolution_note": "wymieniono karton"})
        iss.refresh_from_db()
        self.assertEqual(iss.status, "closed")
        n = Notification.objects.filter(recipient=self.raiser).first()
        self.assertIsNotNone(n)
        self.assertIn("zamknięte", n.title)
        self.assertIn("wymieniono karton", n.body)

    def test_close_by_raiser_skips_self_notification(self):
        from ui.models import Notification
        iss = HUQualityIssue.objects.create(hu=self.hu, item=self.item,
                                            issue_type="damaged", status="open",
                                            raised_by=self.controller)
        self.client.force_login(self.controller)
        self.client.post(reverse("ui:hu_quality_close", args=[iss.pk]),
                         {"resolution_note": "poprawiono"})
        self.assertFalse(Notification.objects.filter(recipient=self.controller).exists())
