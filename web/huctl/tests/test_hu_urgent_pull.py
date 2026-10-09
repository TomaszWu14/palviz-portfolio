"""Pilne wyjęcie HU (grill 2026-09-05, pyt. 38): lider wypycha paletę na czoło
kolejki; kontroler dostaje ack-powiadomienie; nie-lider nie może."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import HandlingUnit, Notification, Shipment
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


def _u(name, *groups):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class UrgentPullTests(TestCase):
    def setUp(self):
        self.leader = _u("lead", GROUP_LEADER, GROUP_CONTROLLER)
        self.ctrl = _u("ctrl", GROUP_CONTROLLER)
        sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="H1",
                                              status="planned",
                                              assigned_to=self.ctrl,
                                              called_at=timezone.now())

    def test_leader_pull_sets_priority_and_notifies(self):
        self.client.force_login(self.leader)
        self.client.post(reverse("ui:hu_urgent_pull", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertTrue(self.hu.is_priority)
        n = Notification.objects.filter(recipient=self.ctrl).first()
        self.assertIsNotNone(n)
        self.assertIn("PILNE", n.title)
        self.assertTrue(n.requires_ack)

    def test_controller_cannot_pull(self):
        self.client.force_login(self.ctrl)
        self.client.post(reverse("ui:hu_urgent_pull", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertFalse(self.hu.is_priority)

    def test_done_hu_not_pullable(self):
        HandlingUnit.objects.filter(pk=self.hu.pk).update(status="ok",
                                                          verified_at=timezone.now())
        self.client.force_login(self.leader)
        self.client.post(reverse("ui:hu_urgent_pull", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertFalse(self.hu.is_priority)
