"""Zadanie lidera „Zrób zdjęcie produktu": pilne powiadomienie z REF, url otwiera
MATinfo (/phv/?q=REF); zły REF/odbiorca odrzucone."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Notification, Product
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


class PhotoTaskTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.leader = U.objects.create_user(username="lead", password="x")
        self.leader.groups.add(Group.objects.get_or_create(name=GROUP_LEADER)[0])
        self.picker = U.objects.create_user(username="pick", password="x")
        self.picker.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        Product.objects.create(code="DMOM10001", name="Rękawice")
        self.client.force_login(self.leader)

    def _post(self, **data):
        return self.client.post(reverse("ui:hu_control_photo_task"), data)

    def test_sends_ack_notification_with_matinfo_url(self):
        self._post(target=str(self.picker.pk), ref_code="DMOM10001")
        n = Notification.objects.get(recipient=self.picker)
        self.assertIn("DMOM10001", n.title)
        self.assertTrue(n.requires_ack)
        self.assertEqual(n.url, "/phv/?q=DMOM10001")

    def test_unknown_ref_rejected(self):
        self._post(target=str(self.picker.pk), ref_code="NIE-MA")
        self.assertEqual(Notification.objects.count(), 0)
