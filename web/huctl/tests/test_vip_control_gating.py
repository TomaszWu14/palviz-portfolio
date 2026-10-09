"""Wyjątek gatingu: HU klienta VIP zawsze podlega kontroli, niezależnie od typu magazynu."""
from django.test import TestCase

from ui.models import (ControlledWarehouseType, Customer, Shipment, HandlingUnit)
from huctl.views.hu_control import _type_controlled, _filter_controlled


class VipControlGatingTests(TestCase):
    def setUp(self):
        ControlledWarehouseType.objects.create(code="A")     # tylko typ A objęty kontrolą
        vip = Customer.objects.create(name="VIP", is_vip=True)
        reg = Customer.objects.create(name="Reg", is_vip=False)
        self.sh_vip = Shipment.objects.create(customer=vip)
        self.sh_reg = Shipment.objects.create(customer=reg)
        self.vb = HandlingUnit.objects.create(shipment=self.sh_vip, seq=1, code="VB",
                                              warehouse_type="B")   # VIP, typ NIE-kontrolowany
        self.rb = HandlingUnit.objects.create(shipment=self.sh_reg, seq=1, code="RB",
                                              warehouse_type="B")   # zwykły, typ NIE-kontrolowany
        self.ra = HandlingUnit.objects.create(shipment=self.sh_reg, seq=2, code="RA",
                                              warehouse_type="A")   # zwykły, typ kontrolowany

    def test_type_controlled_vip_exception(self):
        self.assertTrue(_type_controlled(self.vb))               # VIP w B → kontrola
        self.assertFalse(_type_controlled(self.rb))              # zwykły w B → nie
        self.assertTrue(_type_controlled(self.ra))               # zwykły w A → tak

    def test_filter_includes_vip_excludes_regular_offtype(self):
        codes = set(_filter_controlled(HandlingUnit.objects.all())
                    .values_list("code", flat=True))
        self.assertEqual(codes, {"VB", "RA"})                    # VIP-B + reg-A; reg-B poza
