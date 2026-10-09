"""Zwrotka do zgłaszającego przy zmianie statusu zgłoszenia (grill 2026-09-05,
pyt. 29/91) — sygnał na modelu, działa niezależnie od miejsca zmiany (admin/panel)."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from ui.models import LocationIssue, Notification, PackagingIssue


class IssueStatusFeedbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reporter = get_user_model().objects.create_user(username="rep", password="x")

    def test_packaging_issue_status_change_notifies_reporter(self):
        iss = PackagingIssue.objects.create(ref_code="RG-50", issue_type="other",
                                            description="zły przelicznik",
                                            reporter=self.reporter)
        self.assertFalse(Notification.objects.filter(recipient=self.reporter).exists())
        iss.status = "resolved"
        iss.resolver_notes = "poprawiono przelicznik"
        iss.save()
        n = Notification.objects.filter(recipient=self.reporter).first()
        self.assertIsNotNone(n)
        self.assertIn("RG-50", n.title)
        self.assertIn("poprawiono przelicznik", n.body)

    def test_location_issue_status_change_notifies_reporter(self):
        iss = LocationIssue.objects.create(location_code="A-12-3", issue_type="other",
                                           description="zła etykieta miejsca",
                                           reporter=self.reporter)
        iss.status = "in_review"
        iss.save()
        self.assertTrue(Notification.objects.filter(recipient=self.reporter).exists())

    def test_no_notification_without_status_change(self):
        iss = PackagingIssue.objects.create(ref_code="RG-50", issue_type="other",
                                            description="x", reporter=self.reporter)
        iss.description = "y"
        iss.save()
        self.assertFalse(Notification.objects.filter(recipient=self.reporter).exists())
