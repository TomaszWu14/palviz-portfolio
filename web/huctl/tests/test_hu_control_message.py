"""Panel lidera HU → wiadomość do kontrolera (in-app) + broadcast do zalogowanych."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Notification
from ui.roles import GROUP_LEADER, GROUP_CONTROLLER

U = get_user_model()


def _u(name, group):
    u = U.objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=group)[0])
    return u


class HuControlMessageTest(TestCase):
    def setUp(self):
        self.leader = _u("lead", GROUP_LEADER)
        self.c1 = _u("c1", GROUP_CONTROLLER)
        self.c2 = _u("c2", GROUP_CONTROLLER)
        self.client.force_login(self.leader)
        self.url = reverse("ui:hu_control_message")

    def test_message_single_controller_creates_notification(self):
        r = self.client.post(self.url, {"target": str(self.c1.id), "text": "Podejdź do biura"})
        self.assertEqual(r.status_code, 302)
        n = Notification.objects.get(recipient=self.c1)
        self.assertEqual(n.body, "Podejdź do biura")
        self.assertIn("Wiadomość od", n.title)
        self.assertFalse(Notification.objects.filter(recipient=self.c2).exists())

    def test_empty_text_rejected(self):
        self.client.post(self.url, {"target": str(self.c1.id), "text": "   "})
        self.assertEqual(Notification.objects.count(), 0)

    def test_broadcast_reaches_only_online_controllers(self):
        # _online_user_ids zwraca zalogowanych — zamockuj: online tylko c1.
        with patch("ui.views.admin._online_user_ids", return_value=({self.c1.id}, 1)):
            self.client.post(self.url, {"target": "all_active", "text": "Zbiórka"})
        self.assertTrue(Notification.objects.filter(recipient=self.c1).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.c2).exists())

    def test_broadcast_no_one_online_sends_nothing(self):
        with patch("ui.views.admin._online_user_ids", return_value=(set(), 0)):
            self.client.post(self.url, {"target": "all_active", "text": "Halo"})
        self.assertEqual(Notification.objects.count(), 0)

    def test_non_leader_forbidden(self):
        self.client.force_login(self.c1)
        r = self.client.post(self.url, {"target": str(self.c2.id), "text": "x"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Notification.objects.count(), 0)

    def test_leader_page_renders_comms_card(self):
        r = self.client.get(reverse("ui:hu_control_leader"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Komunikacja z kontrolerami")
