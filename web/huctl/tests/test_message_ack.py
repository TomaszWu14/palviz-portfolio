"""Pkt 2 — pilne komunikaty do kontrolerów wymagają JAWNEGO potwierdzenia odczytu."""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Notification
from ui.notifications import notify
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


def _u(name, *groups):
    u = get_user_model().objects.create_user(name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class MessageAckTests(TestCase):
    def setUp(self):
        self.ctrl = _u("c1", GROUP_CONTROLLER)
        self.lead = _u("lead", GROUP_LEADER)

    def test_notify_requires_ack_sets_group(self):
        notify([self.ctrl], "Pilne", body="Zejdź do strefy A", requires_ack=True)
        n = Notification.objects.get(recipient=self.ctrl)
        self.assertTrue(n.requires_ack)
        self.assertTrue(n.ack_group)
        self.assertIsNone(n.confirmed_at)

    def test_poll_returns_ack_and_confirm_clears_it(self):
        notify([self.ctrl], "Pilne", body="x", requires_ack=True)
        self.client.force_login(self.ctrl)
        d = json.loads(self.client.get(reverse("ui:notifications_poll")).content)
        self.assertIsNotNone(d["ack"])
        nid = d["ack"]["id"]
        self.client.post(reverse("ui:notification_ack", args=[nid]))
        n = Notification.objects.get(pk=nid)
        self.assertIsNotNone(n.confirmed_at)
        self.assertTrue(n.is_read)
        # po potwierdzeniu poll już nie zwraca ack
        d2 = json.loads(self.client.get(reverse("ui:notifications_poll")).content)
        self.assertIsNone(d2["ack"])

    def test_leader_message_requires_ack_and_shows_unconfirmed(self):
        self.client.force_login(self.lead)
        self.client.post(reverse("ui:hu_control_message"),
                         {"text": "Wszyscy do strefy B", "target": str(self.ctrl.pk)})
        n = Notification.objects.get(recipient=self.ctrl)
        self.assertTrue(n.requires_ack)
        # panel lidera pokazuje niepotwierdzone
        r = self.client.get(reverse("ui:hu_control_leader"))
        self.assertEqual(len(r.context["unacked_groups"]), 1)
        self.assertIn("c1", r.context["unacked_groups"][0]["users"])
