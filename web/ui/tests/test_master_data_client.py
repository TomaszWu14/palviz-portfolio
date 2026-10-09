"""master_data_client: same normalised dicts whether reading the local DB (default) or the
remote master-data service (MASTER_DATA_URL) — so a service can be extracted by env alone."""
from unittest.mock import patch

from django.test import TestCase, override_settings

from ui import master_data_client as mdc
from ui.models import Product, Customer, Shipment, HandlingUnit


class _FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data if data is not None else {}
    def json(self):
        return self._data
    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 404:
            raise AssertionError(f"HTTP {self.status_code}")


class LocalModeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.p = Product.objects.create(code="IDX-9", name="Prod 9", ean="59000")
        cls.c = Customer.objects.create(name="Klient", code="K9", city="Radom")
        cls.sh = Shipment.objects.create(name="Kont", is_stock=True)
        from django.utils import timezone as _tz
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HU-9",
                                             status="ok", verified_at=_tz.now())

    def test_local_by_default(self):
        self.assertFalse(mdc.is_remote())

    def test_get_product_returns_normalised_dict(self):
        d = mdc.get_product("idx-9")
        self.assertEqual(d["code"], "IDX-9")
        self.assertEqual(d["ean"], "59000")
        self.assertIsNone(mdc.get_product("NOPE"))

    def test_list_products_and_customer_and_hu(self):
        self.assertEqual([p["code"] for p in mdc.list_products(q="Prod 9")], ["IDX-9"])
        self.assertEqual(mdc.get_customer(self.c.pk)["city"], "Radom")
        self.assertEqual(mdc.get_handling_unit("HU-9")["status"], "ok")

    def test_iter_products_pages_through_everything(self):
        # More rows than one page → iter must return them all, not just the first page.
        Product.objects.bulk_create(
            [Product(code=f"P{i:03d}", name=f"P {i}") for i in range(5)])
        codes = [p["code"] for p in mdc.iter_products(active=True, page_size=2)]
        # 1 from setUpTestData (IDX-9) + 5 here = 6, ordered by code, no duplicates.
        self.assertEqual(len(codes), 6)
        self.assertEqual(len(set(codes)), 6)
        self.assertIn("IDX-9", codes)


@override_settings(MASTER_DATA_URL="https://core.example/api/v2", MASTER_DATA_API_KEY="k")
class RemoteModeTests(TestCase):
    def test_remote_mode_fetches_over_http(self):
        self.assertTrue(mdc.is_remote())
        with patch("requests.get", return_value=_FakeResp(200, {"code": "R-1", "name": "Zdalny"})) as g:
            d = mdc.get_product("R-1")
        self.assertEqual(d, {"code": "R-1", "name": "Zdalny"})
        # called the right URL with the API key header
        args, kwargs = g.call_args
        self.assertEqual(args[0], "https://core.example/api/v2/products/R-1")
        self.assertEqual(kwargs["headers"]["X-API-Key"], "k")

    def test_remote_404_returns_none(self):
        with patch("requests.get", return_value=_FakeResp(404)):
            self.assertIsNone(mdc.get_product("MISSING"))

    def test_remote_does_not_touch_the_orm(self):
        # No Product rows exist here; a remote hit must not fall back to the (empty) ORM.
        with patch("requests.get", return_value=_FakeResp(200, {"code": "R-2"})):
            self.assertEqual(mdc.get_product("R-2")["code"], "R-2")

    def test_iter_products_stops_on_short_remote_page(self):
        # A full page then a short one → two HTTP calls, then stop (no infinite loop).
        pages = [_FakeResp(200, [{"code": "A"}, {"code": "B"}]),
                 _FakeResp(200, [{"code": "C"}])]
        with patch("requests.get", side_effect=pages) as g:
            codes = [p["code"] for p in mdc.iter_products(page_size=2)]
        self.assertEqual(codes, ["A", "B", "C"])
        self.assertEqual(g.call_count, 2)
