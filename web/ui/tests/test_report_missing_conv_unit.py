"""Zgłoszenie „Brak przelicznika" wymaga wskazania jednostki (PAZ/KAR/OPZ) —
zapisywana w current_value zgłoszenia."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import PackagingIssue
from ui.roles import GROUP_WAREHOUSE


class MissingConvUnitTests(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_user(username="mag", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_WAREHOUSE)[0])
        self.client.force_login(u)

    def test_missing_conversion_requires_unit(self):
        r = self.client.post(reverse("ui:phv_report"), {
            "ref_code": "DMOM10001", "issue_type": "missing_conversion",
            "correct_value": "30 szt/karton"})       # brak unit
        self.assertEqual(PackagingIssue.objects.count(), 0)   # odrzucone
        self.assertEqual(r.status_code, 302)                  # redirect z komunikatem

    def test_unit_stored_when_provided(self):
        self.client.post(reverse("ui:phv_report"), {
            "ref_code": "DMOM10001", "issue_type": "missing_conversion",
            "unit": "KAR", "correct_value": "30 szt/karton"})
        issue = PackagingIssue.objects.get()
        self.assertEqual(issue.current_value, "KAR")          # jednostka zapisana

    def test_bad_unit_rejected(self):
        self.client.post(reverse("ui:phv_report"), {
            "ref_code": "DMOM10001", "issue_type": "missing_conversion",
            "unit": "XYZ", "correct_value": "30 szt/karton"})
        self.assertEqual(PackagingIssue.objects.count(), 0)
