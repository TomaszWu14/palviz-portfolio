"""Presence: PresenceMiddleware stempluje last_seen_at/last_device (throttling 60 s) —
last_device WYŁĄCZNIE z jawnej deklaracji (ekran wyboru urządzenia), bez zgadywania z
User-Agenta; wysyłka wiadomości lidera ostrzega o odbiorcy offline (>8 h = prawdopodobnie
poza pracą) — wiadomość i tak wychodzi."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import Notification
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


class PresenceStampTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = get_user_model().objects.create_user(username="p1", password="x")
        self.u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        self.client.force_login(self.u)

    def test_no_declaration_leaves_device_blank(self):
        # Brak wyboru = puste pole — User-Agent (nawet Zebry) niczego nie ustawia.
        self.client.get(reverse("ui:hu_control_menu"),
                        HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 8.1; TC20) Zebra")
        self.u.profile.refresh_from_db()
        self.assertIsNotNone(self.u.profile.last_seen_at)
        self.assertEqual(self.u.profile.last_device, "")

    def test_declared_device_stamped(self):
        s = self.client.session
        s["device_type"] = "zebra"
        s.save()
        self.client.get(reverse("ui:hu_control_menu"))
        self.u.profile.refresh_from_db()
        self.assertEqual(self.u.profile.last_device, "zebra")


class OfflineWarningTests(TestCase):
    def setUp(self):
        cache.clear()
        U = get_user_model()
        self.leader = U.objects.create_user(username="lead", password="x")
        self.leader.groups.add(Group.objects.get_or_create(name=GROUP_LEADER)[0])
        self.picker = U.objects.create_user(username="pick", password="x")
        self.picker.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        self.client.force_login(self.leader)

    def _send(self):
        return self.client.post(reverse("ui:hu_control_message"),
                                {"text": "sprawdź HU", "target": str(self.picker.pk)},
                                follow=True)

    def test_offline_recipient_warns_but_message_sent(self):
        p = self.picker.profile
        p.last_seen_at = timezone.now() - timedelta(minutes=10)
        p.save(update_fields=["last_seen_at"])
        r = self._send()
        msgs = [str(m) for m in get_messages(r.wsgi_request)]
        self.assertTrue(any("nie jest zalogowany" in m and "min temu" in m for m in msgs), msgs)
        self.assertTrue(Notification.objects.filter(recipient=self.picker).exists())

    def test_offline_over_8h_flags_probably_absent(self):
        p = self.picker.profile
        p.last_seen_at = timezone.now() - timedelta(hours=9)
        p.save(update_fields=["last_seen_at"])
        r = self._send()
        msgs = [str(m) for m in get_messages(r.wsgi_request)]
        self.assertTrue(any("nie ma go/jej w pracy" in m for m in msgs), msgs)
