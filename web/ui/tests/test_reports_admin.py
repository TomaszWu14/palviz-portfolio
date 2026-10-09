"""Panel lidera „Zgłoszenia": listuje WSZYSTKIE PackagingIssue + LocationIssue (wszystkich
userów), filtr statusu; nie-lider dostaje 403. Naprawia pusty panel (czytał MessageThread)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import PackagingIssue, LocationIssue
from ui.roles import GROUP_ADMIN, GROUP_LEADER, GROUP_CONTROLLER


def _user(username, *groups):
    u = get_user_model().objects.create_user(username=username, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class ReportsAdminTests(TestCase):
    def setUp(self):
        self.kontroler1 = _user("Kontroler1", GROUP_CONTROLLER)
        PackagingIssue.objects.create(ref_code="DMOM10001", issue_type="missing_conversion",
                                      description="brak przelicznika", reporter=self.kontroler1,
                                      status="open")
        LocationIssue.objects.create(location_code="23L.04", issue_type="damaged_label",
                                     description="zdarta etykieta", reporter=self.kontroler1,
                                     status="resolved")

    def test_leader_sees_all_reports(self):
        self.client.force_login(_user("lead", GROUP_ADMIN, GROUP_LEADER))
        r = self.client.get(reverse("ui:reports_admin"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context["rows"]), 2)          # oba zgłoszenia Kontroler1
        self.assertContains(r, "DMOM10001")
        self.assertContains(r, "23L.04")
        self.assertContains(r, "Kontroler1")                     # zgłaszający widoczny
        self.assertEqual(r.context["open_count"], 1)

    def test_status_filter(self):
        self.client.force_login(_user("lead2", GROUP_ADMIN, GROUP_LEADER))
        r = self.client.get(reverse("ui:reports_admin"), {"status": "open"})
        self.assertEqual(len(r.context["rows"]), 1)          # tylko otwarte
        self.assertEqual(r.context["rows"][0]["label"], "DMOM10001")

    def test_non_leader_forbidden(self):
        self.client.force_login(_user("ctrl", GROUP_CONTROLLER))
        r = self.client.get(reverse("ui:reports_admin"))
        self.assertIn(r.status_code, (302, 403))             # brak dostępu dla nie-lidera
