"""Liczenie ADDYTYWNE: kontroler wpisuje ile zliczył w różnych jednostkach, a suma po
przeliczeniu na bazę = ilość oczekiwana (np. 1 KAR + 5 OP = 15 OP). Kafle to niezależne
składniki, nie lustro jednej ilości."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import (Product, PalletizationInstruction, Shipment, HandlingUnit,
                       HandlingUnitItem)
from ui.roles import GROUP_CONTROLLER


def _controller():
    u = get_user_model().objects.create_user(username="ctrl", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class AdditiveCountingTests(TestCase):
    def setUp(self):
        self.user = _controller()
        self.client.force_login(self.user)
        self.p = Product.objects.create(code="RG-10", name="X")
        PalletizationInstruction.objects.create(
            product=self.p, version=1, is_active=True, unit_weight=0.5,
            pcs_per_carton=10,                              # 1 KAR = 10 OP
            carton_l=29, carton_w=25, carton_h=22,
            pallet_length_cm=120, pallet_width_cm=80,
            pallet_base_height_cm=15, max_height_total_cm=200)
        self.sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=1, code="HU1",
                                              status="in_control", controlled_by=self.user)
        self.item = HandlingUnitItem.objects.create(hu=self.hu, product=self.p, ref_code="RG-10",
                                                    base_unit="OP", base_qty=15, alt_qty=1.5)

    def _count(self, **data):
        data.setdefault("action", "confirm")
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]), data)
        self.item.refresh_from_db()
        return self.item

    def test_kar_plus_op_sums_to_expected(self):
        it = self._count(qty_base="5", c_kar="1")     # 5 OP + 1 KAR(=10) = 15 = oczekiwane
        self.assertEqual(it.result, "ok")
        self.assertEqual(it.counted_qty, 15)

    def test_all_in_base_unit_also_ok(self):
        it = self._count(qty_base="15")                # 15 OP wprost
        self.assertEqual(it.result, "ok")

    def test_sum_below_expected_is_error(self):
        it = self._count(qty_base="5", c_kar="1", sure="1")   # tylko 15? nie — 5+10=15... użyj mniej
        # 1 KAR (=10) + 0 OP = 10 < 15 → niezgodność
        it = self._count(qty_base="", c_kar="1", sure="1")
        self.assertEqual(it.result, "error")
        self.assertEqual(it.counted_qty, 10)
