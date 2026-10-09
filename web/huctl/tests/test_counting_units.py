"""Counting screen: whole units only — the operator can't book a fractional piece
("half a carton"). The scanner keypad is integer-only; this covers the server guard."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Shipment, HandlingUnit, HandlingUnitItem
from ui.roles import GROUP_CONTROLLER


def _controller(name="ctrl"):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class WholeUnitCountingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.controller = _controller()
        cls.sh = Shipment.objects.create(name="Dostawa")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HU1",
                                             status="in_control", controlled_by=cls.controller)
        cls.item = HandlingUnitItem.objects.create(hu=cls.hu, ref_code="A1",
                                                   base_qty=2, base_unit="OP")

    def _post(self, **data):
        data.setdefault("action", "confirm")
        return self.client.post(
            reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]), data)

    def test_fractional_entry_rejected(self):
        self.client.force_login(self.controller)
        r = self._post(qty_base="1,5")
        self.assertRedirects(r, reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.item.refresh_from_db()
        self.assertFalse(self.item.controlled)          # not booked

    def test_whole_entry_accepted(self):
        self.client.force_login(self.controller)
        self._post(qty_base="2")
        self.item.refresh_from_db()
        self.assertTrue(self.item.controlled)
        self.assertEqual(self.item.result, "ok")

    def test_whole_alt_count_with_rounded_factor_not_rejected(self):
        """A whole alt-unit count must not be rejected just because the conversion factor
        is a rounded ratio (no product → _alt_conv falls back to base/alt ≈ 12.0012)."""
        hu = HandlingUnit.objects.create(shipment=self.sh, seq=9, code="HU9",
                                         status="in_control", controlled_by=self.controller)
        item = HandlingUnitItem.objects.create(hu=hu, ref_code="B1", base_unit="OP",
                                               base_qty=12, alt_unit="KAR", alt_qty=0.9999)
        self.client.force_login(self.controller)
        self.client.post(reverse("ui:hu_control_count", args=[hu.pk, item.pk]),
                         {"action": "confirm", "c_kar": "1"})   # 1 whole KAR ≈ 12.0012 base
        item.refresh_from_db()
        self.assertTrue(item.controlled)                          # booked, not rejected as "fractional"
