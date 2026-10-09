"""Parametr `next` nie może wyprowadzić użytkownika poza nasz host (open redirect).

`next` przychodzi z formularza, więc `redirect()` na surowej wartości pozwalał podstawić
`next=https://obcy.example/login` — kontroler po zaksięgowaniu akcji lądował na cudzej
stronie logowania, z paskiem adresu, który przed chwilą pokazywał zaufaną domenę."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import HandlingUnit, HUQualityIssue, Shipment, Task
from ui.roles import ALL_GROUPS

EVIL = "https://obcy.example/login"


def _user(name="nxt"):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class SafeNextTests(TestCase):
    def setUp(self):
        self.user = _user()
        self.client.force_login(self.user)
        self.sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=1, code="NXT1")

    def _assert_not_external(self, resp):
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(resp["Location"].startswith("https://obcy.example"),
                         f"open redirect: {resp['Location']}")

    def test_assign_rejects_external_next(self):
        self._assert_not_external(self.client.post(
            reverse("ui:hu_control_assign", args=[self.hu.pk]),
            {"user": self.user.pk, "next": EVIL}))

    def test_disposition_rejects_external_next(self):
        self._assert_not_external(self.client.post(
            reverse("ui:hu_control_disposition", args=[self.hu.pk]),
            {"disposition": "escaped", "next": EVIL}))

    def test_quality_close_rejects_external_next(self):
        iss = HUQualityIssue.objects.create(hu=self.hu, issue_type="damaged",
                                            status="open", raised_by=self.user)
        self._assert_not_external(self.client.post(
            reverse("ui:hu_quality_close", args=[iss.pk]),
            {"resolution_note": "wyjaśnione", "next": EVIL}))

    def test_relative_next_still_works(self):
        # Bramka ma odcinać obce hosty, nie normalną nawigację po aplikacji.
        target = reverse("ui:hu_control_hub")
        resp = self.client.post(
            reverse("ui:hu_control_disposition", args=[self.hu.pk]),
            {"disposition": "escaped", "next": target})
        self.assertEqual(resp["Location"], target)


class TaskSafeNextTests(TestCase):
    def setUp(self):
        self.user = _user("nxt2")
        self.client.force_login(self.user)

    def test_task_actions_reject_external_next(self):
        task = Task.objects.create(title="T1", created_by=self.user)
        resp = self.client.post(reverse("ui:task_set_status", args=[task.pk]),
                                {"status": "done", "next": EVIL})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(resp["Location"].startswith("https://obcy.example"))
