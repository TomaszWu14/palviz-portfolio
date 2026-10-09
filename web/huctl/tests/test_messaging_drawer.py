"""BLOK A — komunikator: naprawa otwierania wiadomości + drawer + żywy licznik.

Pokrywa obie przyczyny buga „nie da się otworzyć wiadomości":
  1) kanał lider→kontroler tworzył gołe Notification bez wątku (url → płaska lista),
  2) @_any_role odbijał 403 uczestnika wątku bez żadnej grupy.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import MessageThread, Message, MessageRead, Notification
from ui.roles import GROUP_LEADER, GROUP_CONTROLLER


def _user(name, *groups):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class ThreadAccessTests(TestCase):
    def test_participant_without_any_role_can_open_and_reply(self):
        """Bug-fix #2: uczestnik bez ŻADNEJ grupy otwiera wątek i odpowiada (guard
        per uczestnictwo, nie rola — wcześniej @_any_role → 403)."""
        sender = _user("lead", GROUP_LEADER)
        norole = _user("norole")                     # zero grup
        t = MessageThread.objects.create(subject="Test", created_by=sender)
        t.participants.add(sender, norole)
        Message.objects.create(thread=t, sender=sender, body="Cześć")
        self.client.force_login(norole)
        r = self.client.get(reverse("ui:message_thread", args=[t.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Cześć")
        self.client.post(reverse("ui:message_thread", args=[t.pk]), {"body": "Odpowiadam"})
        self.assertEqual(t.messages.count(), 2)

    def test_non_participant_still_blocked(self):
        a, b, x = _user("a1"), _user("b1"), _user("x1")
        t = MessageThread.objects.create(subject="Prywatne", created_by=a)
        t.participants.add(a, b)
        self.client.force_login(x)
        r = self.client.get(reverse("ui:message_thread", args=[t.pk]))
        self.assertEqual(r.status_code, 302)          # redirect z komunikatem braku dostępu


class LeaderScannerMessageTests(TestCase):
    def test_leader_message_creates_thread_and_links_notification(self):
        """Bug-fix #1: wiadomość lidera tworzy WĄTEK, a powiadomienie linkuje do niego
        (wcześniej url = /control/notifications/ — pętla bez wejścia w treść)."""
        leader = _user("lider", GROUP_LEADER)
        ctrl = _user("kontroler", GROUP_CONTROLLER)
        self.client.force_login(leader)
        self.client.post(reverse("ui:hu_control_message"),
                         {"text": "Proszę o pilny kontakt", "target": str(ctrl.pk)})
        t = MessageThread.objects.latest("created_at")
        self.assertIn(ctrl, t.participants.all())
        self.assertEqual(t.messages.first().body, "Proszę o pilny kontakt")
        n = Notification.objects.get(recipient=ctrl)
        self.assertEqual(n.url, reverse("ui:message_thread", args=[t.pk]))
        self.assertTrue(n.requires_ack)
        # Kontroler otwiera wątek z powiadomienia i odpowiada — ≤2 kliknięcia.
        self.client.force_login(ctrl)
        r = self.client.get(n.url)
        self.assertEqual(r.status_code, 200)
        self.client.post(n.url, {"body": "Już idę"})
        self.assertEqual(t.messages.count(), 2)


class UnreadAndDrawerTests(TestCase):
    def setUp(self):
        self.a = _user("ua", GROUP_LEADER)
        self.b = _user("ub", GROUP_CONTROLLER)
        self.t = MessageThread.objects.create(subject="Wątek X", created_by=self.a)
        self.t.participants.add(self.a, self.b)
        Message.objects.create(thread=self.t, sender=self.a, body="Hej")

    def test_poll_reports_unread_threads_and_open_marks_read(self):
        self.client.force_login(self.b)
        d = self.client.get(reverse("ui:notifications_poll")).json()
        self.assertEqual(d["msg_unread"], 1)
        # Otwarcie wątku = odczyt → licznik spada do 0.
        self.client.get(reverse("ui:message_thread", args=[self.t.pk]))
        self.assertTrue(MessageRead.objects.filter(thread=self.t, user=self.b).exists())
        d = self.client.get(reverse("ui:notifications_poll")).json()
        self.assertEqual(d["msg_unread"], 0)
        # Nowa cudza wiadomość → znowu 1; własna NIE podbija licznika.
        Message.objects.create(thread=self.t, sender=self.a, body="Jeszcze jedno")
        self.assertEqual(self.client.get(reverse("ui:notifications_poll")).json()["msg_unread"], 1)
        self.client.get(reverse("ui:message_thread", args=[self.t.pk]))
        Message.objects.create(thread=self.t, sender=self.b, body="Moja odpowiedź")
        self.assertEqual(self.client.get(reverse("ui:notifications_poll")).json()["msg_unread"], 0)

    def test_drawer_stays_hidden_until_opened(self):
        """Regresja: inline display:flex wygrywał z [hidden] — drawer zasłaniał
        każdy ekran od wejścia. Strażnik CSS musi być obecny na stronie."""
        self.client.force_login(self.b)
        # follow=True: operator control-only jest przekierowywany na ekran skanera,
        # a dzwonek (i strażnik) ma być obecny także tam.
        r = self.client.get(reverse("ui:home"), follow=True)
        self.assertContains(r, 'id="msg-drawer" hidden')
        self.assertContains(r, "#msg-drawer[hidden], #msg-drawer-ovl[hidden] { display: none !important; }")

    def test_drawer_fragment_lists_threads_with_unread_flag(self):
        self.client.force_login(self.b)
        r = self.client.get(reverse("ui:messages_drawer"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Wątek X")
        self.assertContains(r, "fragment=1")          # wejście w wątek jako fragment

    def test_thread_fragment_mode(self):
        self.client.force_login(self.b)
        r = self.client.get(reverse("ui:message_thread", args=[self.t.pk]) + "?fragment=1")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "data-drawer-reply")   # inline-odpowiedź w drawerze
        self.assertNotContains(r, "<html")            # fragment, nie pełna strona


class LeaderTargetTests(TestCase):
    """A-bis: „Napisz do lidera" adresuje przypisanego przełożonego (G), fallback grupa."""

    def test_assigned_leader_wins(self):
        from ui.notifications import leader_target
        u = _user("pod1")
        boss = _user("szef1")
        u.profile.leader = boss
        u.profile.save(update_fields=["leader"])
        self.assertEqual(leader_target(u), f"u:{boss.pk}")

    def test_fallback_to_group_without_leader_or_inactive(self):
        from ui.notifications import leader_target
        u = _user("pod2")
        self.assertEqual(leader_target(u), "g:Lider kontroli")
        boss = _user("szef2")
        boss.is_active = False
        boss.save(update_fields=["is_active"])
        u.profile.leader = boss
        u.profile.save(update_fields=["leader"])
        self.assertEqual(leader_target(u), "g:Lider kontroli")

    def test_drawer_links_use_assigned_leader(self):
        u = _user("pod3", GROUP_CONTROLLER)
        boss = _user("szef3")
        u.profile.leader = boss
        u.profile.save(update_fields=["leader"])
        self.client.force_login(u)
        r = self.client.get(reverse("ui:messages_drawer"))
        self.assertContains(r, f"to=u%3A{boss.pk}")
