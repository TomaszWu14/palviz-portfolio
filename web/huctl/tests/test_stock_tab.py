"""The imported warehouse stock lives on its own 'Stock magazynowy' tab and is kept
out of the transport shipments list."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from ui import models as m


class StockTabTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.stock = m.Shipment.objects.create(name="Stock magazynowy", is_stock=True)
        self.transport = m.Shipment.objects.create(name="Dostawa 1", is_stock=False)

    def test_stock_page_lists_containers(self):
        r = self.client.get(reverse("ui:planner_stock"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Stock magazynowy")
        self.assertContains(r, "Podgląd stocku")            # link to the contents view

    def test_stock_contents_lists_and_filters_items(self):
        # REF chosen so it is NOT part of the search-box placeholder text.
        hu = m.HandlingUnit.objects.create(shipment=self.stock, seq=1, code="HU001",
                                            location="RACK-77-A", warehouse_type="PA", status="to_recheck")
        m.HandlingUnitItem.objects.create(hu=hu, ref_code="MYREF777", description="Rękawice",
                                          lot="L1", base_qty=10, base_unit="szt")
        # An item on a transport shipment must NOT appear in the stock contents.
        other = m.HandlingUnit.objects.create(shipment=self.transport, seq=1, code="HU999")
        m.HandlingUnitItem.objects.create(hu=other, ref_code="TRANSPONLY", base_qty=1)

        url = reverse("ui:planner_stock_contents")
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "MYREF777")
        self.assertContains(r, "RACK-77-A")
        self.assertNotContains(r, "TRANSPONLY")             # transport item excluded

        # Text search narrows results.
        self.assertContains(self.client.get(url, {"q": "MYREF777"}), "MYREF777")
        self.assertNotContains(self.client.get(url, {"q": "nieistnieje"}), "MYREF777")
        # Container filter works.
        self.assertContains(self.client.get(url, {"container": self.stock.pk}), "MYREF777")
        self.assertNotContains(self.client.get(url, {"container": self.transport.pk}), "MYREF777")

    def test_transport_list_excludes_stock(self):
        r = self.client.get(reverse("ui:planner_shipments"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Dostawa 1")
        self.assertNotContains(r, "Stock magazynowy")
