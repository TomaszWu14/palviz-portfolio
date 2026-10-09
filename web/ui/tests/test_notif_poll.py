"""Polling nieprzeczytanych (dla dźwięku/popupu nowej wiadomości na urządzeniu)."""
import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from django.contrib.auth.models import Group

from ui.models import Notification
from ui.notifications import owner_users
from ui.roles import GROUP_MASTER_DATA


class NotifPollTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("poller", "p@p.pl", "x")
        self.url = reverse("ui:notifications_poll")

    def test_counts_unread_and_latest(self):
        self.client.force_login(self.u)
        Notification.objects.create(recipient=self.u, title="Stara", body="a", is_read=True)
        Notification.objects.create(recipient=self.u, title="Nowa", body="pilne", url="/x/")
        d = json.loads(self.client.get(self.url).content)
        self.assertEqual(d["count"], 1)                      # tylko nieprzeczytane
        self.assertEqual(d["latest"]["title"], "Nowa")
        self.assertEqual(d["latest"]["url"], "/x/")

    def test_none_when_all_read(self):
        self.client.force_login(self.u)
        d = json.loads(self.client.get(self.url).content)
        self.assertEqual(d["count"], 0)
        self.assertIsNone(d["latest"])

    def test_anonymous_gets_zero(self):
        d = json.loads(self.client.get(self.url).content)
        self.assertEqual(d["count"], 0)

    def test_non_master_data_user_can_mark_read(self):
        # poll jest dla każdej roli — read też musi być, inaczej dzwonek utyka nieskasowany.
        self.u.groups.add(Group.objects.get_or_create(name="Transport")[0])  # rola ≠ Master Data
        self.client.force_login(self.u)
        Notification.objects.create(recipient=self.u, title="N", body="b")
        resp = self.client.post(reverse("ui:notifications_read"))
        self.assertIn(resp.status_code, (200, 302))
        self.assertEqual(Notification.objects.filter(recipient=self.u, is_read=False).count(), 0)


class RecipientHelperTests(TestCase):
    def test_owner_users_excludes_inactive(self):
        # Zwolniony (is_active=False) admin/MD nie może dostawać maili/powiadomień.
        md = Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0]
        active = User.objects.create_user("md_on", "on@z.pl", "x")
        gone = User.objects.create_user("md_off", "off@z.pl", "x", is_active=False)
        active.groups.add(md)
        gone.groups.add(md)
        pks = {u.pk for u in owner_users()}
        self.assertIn(active.pk, pks)
        self.assertNotIn(gone.pk, pks)
