"""Bramki liczenia pozycji: online i offline muszą przepuszczać dokładnie to samo.

Kanał online (`hu_control_count`) blokował tylko status `ok`, nie sprawdzał kontrolowanego
typu magazynu i nie wymagał rozpoczętej kontroli — więc paleta oznaczona jako „wyjechało
bez kontroli” była policzalna, a liczenie na palecie `planned` szło bez rejestracji
`controlled_by`/`control_started_at` (niewidoczne w panelu lidera, bez podstawy do reguły
„rekontrolę robi inny kontroler”). Offline sync miał te bramki od początku."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import (ControlledWarehouseType, HandlingUnit, HandlingUnitItem,
                       HUStatusEvent, Shipment)
from ui.roles import ALL_GROUPS, GROUP_CONTROLLER


def _controller(name="cnt"):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class CountGateTests(TestCase):
    def setUp(self):
        self.user = _controller()
        self.client.force_login(self.user)
        self.sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(
            shipment=self.sh, seq=1, code="CNT1", status="planned", warehouse_type="WT01")
        self.item = HandlingUnitItem.objects.create(
            hu=self.hu, ref_code="RG-50", base_unit="OP", base_qty=10)

    def _count(self, qty="10"):
        return self.client.post(
            reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
            {"action": "confirm", "qty_base": qty})

    def test_escaped_hu_cannot_be_counted(self):
        self.hu.status = "escaped"
        self.hu.save(update_fields=["status"])

        self._count()

        self.item.refresh_from_db()
        self.assertFalse(self.item.controlled)

    def test_uncontrolled_warehouse_type_cannot_be_counted(self):
        # Lider zawęził kontrolę do innego typu — deep-link nie może tego obejść.
        ControlledWarehouseType.objects.create(code="WT99")

        self._count()

        self.item.refresh_from_db()
        self.assertFalse(self.item.controlled)

    def test_counting_a_planned_hu_registers_the_controller(self):
        self._count()

        self.item.refresh_from_db()
        self.hu.refresh_from_db()
        self.assertTrue(self.item.controlled)
        self.assertEqual(self.hu.status, "in_control")
        self.assertEqual(self.hu.controlled_by, self.user)
        self.assertIsNotNone(self.hu.control_started_at)
        self.assertTrue(HUStatusEvent.objects.filter(
            hu=self.hu, from_status="planned", to_status="in_control").exists())

    def test_started_hu_keeps_its_original_controller(self):
        # _ensure_started nie może przejmować HU już prowadzonej przez kogoś innego —
        # od tego jest osobna ścieżka takeover (z powodem i powiadomieniem).
        other = _controller("inny")
        self.hu.status = "in_control"
        self.hu.controlled_by = other
        self.hu.save(update_fields=["status", "controlled_by"])

        self._count()

        self.hu.refresh_from_db()
        self.assertEqual(self.hu.controlled_by, other)


class SyncGateTests(TestCase):
    """Offline sync ma dostać ten sam auto-start — inaczej praca zebrana offline
    na palecie `planned` wracałaby bez zarejestrowanego kontrolera."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="sync", password="x")
        for g in ALL_GROUPS:
            self.user.groups.add(Group.objects.get_or_create(name=g)[0])
        self.client.force_login(self.user)
        self.sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(
            shipment=self.sh, seq=1, code="SYN1", status="planned")
        self.item = HandlingUnitItem.objects.create(
            hu=self.hu, ref_code="RG-50", base_unit="OP", base_qty=10)

    def test_sync_on_planned_hu_registers_the_controller(self):
        resp = self.client.post(
            reverse("ui:hu_control_sync"),
            data={"actions": [{"client_id": "c1", "item": self.item.pk, "qty_base": 10}]},
            content_type="application/json")

        self.assertEqual(resp.json()["applied"], 1)
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "in_control")
        self.assertEqual(self.hu.controlled_by, self.user)
