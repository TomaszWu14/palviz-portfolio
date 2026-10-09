"""Komunikator wewnętrzny: wiadomość do osoby / grupy → Notification (dzwonek)."""
from django.contrib.auth.models import User, Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Notification, MessageThread


class MessagingTests(TestCase):
    def setUp(self):
        self.sender = User.objects.create_superuser("boss", "b@b.pl", "x")
        self.client.force_login(self.sender)
        self.lead_grp = Group.objects.get_or_create(name="Lider kontroli")[0]
        self.l1 = User.objects.create_user("lead1", "l1@z.pl", "x")
        self.l2 = User.objects.create_user("lead2", "l2@z.pl", "x")
        self.l1.groups.add(self.lead_grp)
        self.l2.groups.add(self.lead_grp)
        self.url = reverse("ui:message_compose")

    def test_messages_admin_paginates(self):
        # 31 wątków → panel nadzoru paginuje po 30 (poza-top10).
        for i in range(31):
            MessageThread.objects.create(subject=f"W{i:02d}", created_by=self.sender)
        r1 = self.client.get(reverse("ui:messages_admin"))
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.context["page_obj"].paginator.count, 31)
        self.assertEqual(len(r1.context["threads"]), 30)
        r2 = self.client.get(reverse("ui:messages_admin"), {"page": 2})
        self.assertEqual(len(r2.context["threads"]), 1)

    def test_send_to_group_fans_out(self):
        self.client.post(self.url, {"target": "g:Lider kontroli", "body": "Błąd ilości na HU 100"})
        self.assertEqual(Notification.objects.filter(recipient=self.l1).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.l2).count(), 1)
        n = Notification.objects.filter(recipient=self.l1).first()
        self.assertIn("Błąd ilości", n.body)
        self.assertIn("boss", n.title)

    def test_send_to_single_user(self):
        self.client.post(self.url, {"target": f"u:{self.l1.pk}", "body": "cześć",
                                    "url": "/hu/5/"})
        self.assertEqual(Notification.objects.filter(recipient=self.l1).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.l2).count(), 0)
        # Powiadomienie linkuje do WĄTKU; link kontekstu „/hu/5/" ląduje na wątku.
        th = MessageThread.objects.get()
        self.assertEqual(Notification.objects.get(recipient=self.l1).url,
                         reverse("ui:message_thread", args=[th.pk]))
        self.assertEqual(th.url, "/hu/5/")

    def test_javascript_url_rejected(self):
        # Stored-XSS guard: schemat inny niż /,http(s) → link kontekstu odrzucony (pusty).
        self.client.post(self.url, {"target": f"u:{self.l1.pk}", "body": "x",
                                    "url": "javascript:alert(1)"})
        self.assertEqual(MessageThread.objects.get().url, "")

    def test_sender_excluded_from_group(self):
        self.sender.groups.add(self.lead_grp)                 # nadawca też w grupie
        self.client.post(self.url, {"target": "g:Lider kontroli", "body": "test"})
        self.assertEqual(Notification.objects.filter(recipient=self.sender).count(), 0)

    def test_empty_body_sends_nothing(self):
        self.client.post(self.url, {"target": "g:Lider kontroli", "body": "  "})
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(MessageThread.objects.count(), 0)

    def test_creates_thread_with_participants(self):
        self.client.post(self.url, {"target": f"u:{self.l1.pk}", "body": "cześć",
                                    "ctx": "HU 100"})
        th = MessageThread.objects.get()
        self.assertEqual(th.subject, "HU 100")
        self.assertEqual(set(th.participants.values_list("pk", flat=True)),
                         {self.sender.pk, self.l1.pk})       # nadawca + odbiorca
        self.assertEqual(th.messages.count(), 1)

    def test_reply_adds_message_and_notifies_others(self):
        self.client.post(self.url, {"target": f"u:{self.l1.pk}", "body": "pytanie"})
        th = MessageThread.objects.get()
        Notification.objects.all().delete()                  # wyczyść pierwsze doręczenie
        # l1 odpowiada w wątku → nadawca (sender) dostaje powiadomienie, l1 nie.
        self.client.force_login(self.l1)
        self.client.post(reverse("ui:message_thread", args=[th.pk]), {"body": "odpowiedź"})
        self.assertEqual(th.messages.count(), 2)
        self.assertEqual(Notification.objects.filter(recipient=self.sender).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.l1).count(), 0)

    def test_non_participant_denied(self):
        self.client.post(self.url, {"target": f"u:{self.l1.pk}", "body": "x"})
        th = MessageThread.objects.get()
        # Osoba spoza wątku i BEZ roli lider/admin nie ma dostępu (l2 jest liderem → nadzór).
        outsider = User.objects.create_user("outsider", "o@o.pl", "x")
        outsider.groups.add(Group.objects.get_or_create(name="Podgląd")[0])
        self.client.force_login(outsider)
        self.client.post(reverse("ui:message_thread", args=[th.pk]),
                         {"body": "wtręt"}, follow=True)
        self.assertEqual(th.messages.count(), 1)             # nic nie dopisano

    def test_leader_can_oversee_any_thread(self):
        # Lider/admin (nadzór) może wejść w cudzy wątek i odpowiedzieć — panel zgłoszeń.
        self.client.post(self.url, {"target": f"u:{self.l1.pk}", "body": "x"})
        th = MessageThread.objects.get()
        self.client.force_login(self.l2)                     # l2 = Lider kontroli, nie uczestnik
        self.client.post(reverse("ui:message_thread", args=[th.pk]), {"body": "nadzór"})
        self.assertEqual(th.messages.count(), 2)
        self.assertTrue(th.participants.filter(pk=self.l2.pk).exists())   # dołączył
