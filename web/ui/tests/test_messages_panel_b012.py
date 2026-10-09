"""B-012 (BUGS-FOUND.md): komunikator padał (500), gdy istniał wątek/wiadomość bez autora.

Wątki systemowe tworzy ``notifications.py`` bez ``created_by``, a ich wiadomości z
``sender=None`` (też: autor usunięty → SET_NULL). Szablony miały ``default:x.sender.username``
— argument filtra nie jest wyciszany jak zwykła zmienna, więc ``None.username`` wywracał
panel lidera, widok wątku i szufladkę wiadomości. Teraz ``{% if x %}…{% else %}system``.

B-013: ten sam wzorzec w dzienniku wydruku HU (``hu_print/log.html``, ``r.user`` SET_NULL) —
autor z migawki ``username``, bez sięgania do ``r.user``.
"""

from django.test import TestCase
from django.urls import reverse

from huctl.models_print import HUPrintProject, HUPrintRun
from testkit import factories as f
from testkit.personas import client_for, make


class MessagingWithoutAuthorTests(TestCase):
    def setUp(self):
        self.user = make("Lider kontroli")
        self.thread = f.ThreadFactory(subject="Powiadomienia systemowe")  # created_by = None
        self.thread.participants.add(self.user)
        f.MessageFactory(thread=self.thread, sender=None, body="Paleta VIP do kontroli")
        self.client = client_for("Lider kontroli")

    def test_panel_renders_with_system_thread(self):
        r = self.client.get(reverse("ui:messages_admin"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "od: system")

    def test_thread_renders_system_message(self):
        url = reverse("ui:message_thread", args=[self.thread.pk])
        for query in ("", "?fragment=1"):
            with self.subTest(query=query):
                r = self.client.get(url + query)
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, "system")
                self.assertContains(r, "Paleta VIP do kontroli")

    def test_drawer_renders_system_message(self):
        r = self.client.get(reverse("ui:messages_drawer"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "system")


class PrintLogWithoutUserTests(TestCase):
    def test_log_renders_run_of_deleted_user(self):
        project = HUPrintProject.objects.create(name="Projekt Alfa")
        HUPrintRun.objects.create(project=project, user=None, username="usunięty",
                                  quantity=10, from_number=1, to_number=10)
        r = client_for("superuser").get(reverse("ui:hu_print_log"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "usunięty")  # autor z migawki username
