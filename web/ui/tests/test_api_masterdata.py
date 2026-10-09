"""Master-data REST API (Faza 3): read-only reference data other services will consume,
behind the X-API-Key auth."""
from django.test import TestCase, override_settings

from ui.models import Product, Customer, Shipment, HandlingUnit

KEY = "test-master-data-key"
HDR = {"HTTP_X_API_KEY": KEY}


@override_settings(PALVIZ_API_TOKEN=KEY)
class MasterDataApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.p = Product.objects.create(code="IDX-1", name="Produkt testowy", ean="5900000000017")
        cls.c = Customer.objects.create(name="PHARMO Sp. z o.o.", code="PHARMO", city="Radom")
        cls.sh = Shipment.objects.create(name="Kontener", is_stock=True)
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HU-100749998",
                                             status="planned", warehouse_type="WMS",
                                             recipient_type="PHARMO")

    def test_requires_api_key(self):
        self.assertEqual(self.client.get("/api/v2/products").status_code, 401)

    def test_products_list_and_detail(self):
        r = self.client.get("/api/v2/products", **HDR)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([p["code"] for p in r.json()], ["IDX-1"])
        r = self.client.get("/api/v2/products/IDX-1", **HDR)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["ean"], "5900000000017")
        self.assertEqual(self.client.get("/api/v2/products/NOPE", **HDR).status_code, 404)

    def test_products_filter(self):
        r = self.client.get("/api/v2/products?q=testowy", **HDR)
        self.assertEqual(len(r.json()), 1)
        r = self.client.get("/api/v2/products?q=nieistnieje", **HDR)
        self.assertEqual(r.json(), [])

    def test_customers(self):
        r = self.client.get("/api/v2/customers?q=PHARMO", **HDR)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()[0]["name"], "PHARMO Sp. z o.o.")
        r = self.client.get(f"/api/v2/customers/{self.c.pk}", **HDR)
        self.assertEqual(r.json()["city"], "Radom")

    def test_handling_units_registry(self):
        r = self.client.get("/api/v2/handling-units?warehouse_type=WMS", **HDR)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()[0]["code"], "HU-100749998")
        r = self.client.get("/api/v2/handling-units/HU-100749998", **HDR)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "planned")
        self.assertEqual(self.client.get("/api/v2/handling-units/NOPE", **HDR).status_code, 404)
