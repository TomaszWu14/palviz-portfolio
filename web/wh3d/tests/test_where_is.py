"""Magazyn 3D — wyszukiwarka „Gdzie jest produkt?" (fizyczne lokalizacje stocku)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Product, Shipment, HandlingUnit, HandlingUnitItem


class WhereIsSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("planner", password="x")
        cls.user.groups.add(Group.objects.get_or_create(name="Podgląd")[0])  # _planner wymaga roli
        cls.p1 = Product.objects.create(code="ABC-1", name="Rękawice nitrylowe", ean="5901234")
        cls.p2 = Product.objects.create(code="XYZ-9", name="Maseczki", ean="5907777")

        cls.stock = Shipment.objects.create(name="Stock", is_stock=True)
        cls.other = Shipment.objects.create(name="Wysyłka", is_stock=False)

        hu1 = HandlingUnit.objects.create(shipment=cls.stock, seq=1, code="HU1", location="A0-01-100A")
        HandlingUnitItem.objects.create(hu=hu1, ref_code="ABC-1", product=cls.p1,
                                        alt_unit="KAR", alt_qty=5)
        hu2 = HandlingUnit.objects.create(shipment=cls.stock, seq=2, code="HU2", location="B0-02-200B")
        HandlingUnitItem.objects.create(hu=hu2, ref_code="ABC-1", product=cls.p1,
                                        alt_unit="KAR", alt_qty=3)
        # Same product but on a NON-stock shipment — must be ignored.
        hu3 = HandlingUnit.objects.create(shipment=cls.other, seq=1, code="HU3", location="Z9")
        HandlingUnitItem.objects.create(hu=hu3, ref_code="ABC-1", product=cls.p1,
                                        alt_unit="KAR", alt_qty=99)

    def setUp(self):
        self.client.force_login(self.user)

    def test_finds_stock_locations_by_code(self):
        resp = self.client.get(reverse("ui:warehouse_where_is"), {"q": "ABC-1"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "A0-01-100A")
        self.assertContains(resp, "B0-02-200B")
        # 2 stock locations, 2 HU rows, total 8 — the non-stock HU (99) is excluded.
        self.assertEqual(resp.context["location_count"], 2)
        self.assertEqual(len(resp.context["rows"]), 2)
        self.assertEqual(resp.context["total_qty"], 8)
        # The non-stock HU (location "Z9") is excluded. Assert on the parsed rows, not
        # the raw HTML: a 2-char needle like "Z9" can appear by chance inside the random
        # per-request CSRF token in the page, which made this a ~1-2% flaky check.
        self.assertNotIn("Z9", {r["location"] for r in resp.context["rows"]})

    def test_search_by_ean_and_name(self):
        self.assertContains(self.client.get(reverse("ui:warehouse_where_is"), {"q": "5901234"}),
                            "A0-01-100A")
        self.assertContains(self.client.get(reverse("ui:warehouse_where_is"), {"q": "Rękawice"}),
                            "A0-01-100A")

    def test_no_match_shows_empty_state(self):
        resp = self.client.get(reverse("ui:warehouse_where_is"), {"q": "NOPE-404"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["rows"]), 0)

    def test_blank_query_renders(self):
        resp = self.client.get(reverse("ui:warehouse_where_is"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["rows"], [])

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("ui:warehouse_where_is"))
        self.assertEqual(resp.status_code, 302)
