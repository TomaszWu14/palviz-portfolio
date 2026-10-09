"""Magazyn 3D — JSON zawartości lokalizacji (panel po kliknięciu na mapie)."""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Product, Shipment, HandlingUnit, HandlingUnitItem


class LocationContentsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("p", password="x")
        cls.user.groups.add(Group.objects.get_or_create(name="Podgląd")[0])  # _planner wymaga roli
        cls.p = Product.objects.create(code="ABC-1", name="Rękawice", ean="59001")
        cls.stock = Shipment.objects.create(name="Stock", is_stock=True)
        cls.other = Shipment.objects.create(name="Wysyłka", is_stock=False)

        hu = HandlingUnit.objects.create(shipment=cls.stock, seq=1, code="HU1", location="A0-01-100A")
        HandlingUnitItem.objects.create(hu=hu, ref_code="ABC-1", product=cls.p,
                                        alt_unit="KAR", alt_qty=5, lot="L1")
        # Different location — must not appear.
        hu2 = HandlingUnit.objects.create(shipment=cls.stock, seq=2, code="HU2", location="B0-02-200B")
        HandlingUnitItem.objects.create(hu=hu2, ref_code="ABC-1", product=cls.p, alt_qty=1)
        # Same location code but non-stock shipment — excluded.
        hu3 = HandlingUnit.objects.create(shipment=cls.other, seq=1, code="HU3", location="A0-01-100A")
        HandlingUnitItem.objects.create(hu=hu3, ref_code="ABC-1", product=cls.p, alt_qty=99)

    def setUp(self):
        self.client.force_login(self.user)

    def _get(self, code=None):
        params = {"code": code} if code is not None else {}
        r = self.client.get(reverse("ui:warehouse_location_contents"), params)
        self.assertEqual(r.status_code, 200)
        return json.loads(r.content)

    def test_returns_stock_items_at_location(self):
        d = self._get("A0-01-100A")
        self.assertEqual(d["location"], "A0-01-100A")
        self.assertEqual(d["count"], 1)               # only the stock HU, not the non-stock one
        item = d["items"][0]
        self.assertEqual(item["code"], "ABC-1")
        self.assertEqual(item["hu_ref"], "HU1")
        self.assertEqual(item["lot"], "L1")
        self.assertEqual(item["qty"], 5)

    def test_other_location_isolated(self):
        self.assertEqual(self._get("B0-02-200B")["count"], 1)

    def test_unknown_location_empty(self):
        self.assertEqual(self._get("NOPE")["count"], 0)

    def test_blank_code_empty(self):
        d = self._get("")
        self.assertEqual(d["count"], 0)
        self.assertEqual(d["items"], [])

    def test_requires_login(self):
        self.client.logout()
        r = self.client.get(reverse("ui:warehouse_location_contents"), {"code": "A0-01-100A"})
        self.assertEqual(r.status_code, 302)
