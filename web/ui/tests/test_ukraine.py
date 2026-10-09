"""Moduł „Wysyłka UKRAINA": agregacja stocku po magazynie (ACME/DLT), import zleceń
(upsert), monitoring z brakiem i gate roli."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ui.models import Shipment, HandlingUnit, HandlingUnitItem, UkraineOrderLine
from ui.roles import GROUP_TRANSPORT, GROUP_CONTROLLER
from ui.views.ukraine import stock_by_batch


def _user(name, group):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=group)[0])
    return u


def _stock(wh, ref, lot, qty):
    sh, _ = Shipment.objects.get_or_create(name="Stock", is_stock=True)
    hu = HandlingUnit.objects.create(shipment=sh, seq=HandlingUnit.objects.count() + 1,
                                     code=f"HU{HandlingUnit.objects.count()+1}", warehouse_type=wh)
    HandlingUnitItem.objects.create(hu=hu, ref_code=ref, lot=lot, base_qty=qty, base_unit="szt")


@override_settings(UKRAINE_WAREHOUSE_ACME=["MAG"], UKRAINE_WAREHOUSE_DLT=["DLT"])
class StockAggregation(TestCase):
    def test_buckets_by_warehouse(self):
        _stock("MAG", "A1", "L1", 100)
        _stock("MAG", "A1", "L1", 50)     # druga HU tego samego LOT-u w ACME → sumuje
        _stock("DLT", "A1", "L1", 30)
        _stock("XXX", "A1", "L1", 7)      # nieznany kod → other
        _stock("MAG", "A1", "L2", 999)    # inny lot → nie liczony do (A1,L1)
        res = stock_by_batch({("A1", "L1")})
        self.assertEqual(res[("A1", "L1")], {"acme": 150.0, "dlt": 30.0, "other": 7.0})

    def test_empty_when_no_match(self):
        _stock("MAG", "B2", "L9", 5)
        self.assertEqual(stock_by_batch({("A1", "L1")}), {})


@override_settings(UKRAINE_WAREHOUSE_ACME=["MAG"], UKRAINE_WAREHOUSE_DLT=["DLT"])
class HomeView(TestCase):
    def setUp(self):
        self.tr = _user("tr", GROUP_TRANSPORT)

    def test_shortfall_and_split(self):
        UkraineOrderLine.objects.create(customer="Kyiv", index_code="A1", lot="L1", requested_qty=200)
        _stock("MAG", "A1", "L1", 120)
        _stock("DLT", "A1", "L1", 30)
        self.client.force_login(self.tr)
        r = self.client.get(reverse("ui:ukraine_home"))
        self.assertEqual(r.status_code, 200)
        row = r.context["rows"][0]
        self.assertEqual(row["acme"], 120.0)
        self.assertEqual(row["dlt"], 30.0)
        self.assertEqual(row["ready"], 150.0)
        self.assertEqual(row["shortfall"], 50.0)     # 200 żądane − 150 gotowe
        self.assertEqual(r.context["n_short"], 1)

    def test_only_short_filter_hides_complete(self):
        UkraineOrderLine.objects.create(customer="K", index_code="A1", lot="L1", requested_qty=10)
        _stock("MAG", "A1", "L1", 50)                # nadmiar → brak = 0
        self.client.force_login(self.tr)
        r = self.client.get(reverse("ui:ukraine_home"), {"short": "1"})
        self.assertEqual(len(r.context["rows"]), 0)

    def test_role_gate_blocks_controller(self):
        ctrl = _user("c", GROUP_CONTROLLER)
        self.client.force_login(ctrl)
        r = self.client.get(reverse("ui:ukraine_home"))
        self.assertNotEqual(r.status_code, 200)      # brak dostępu → redirect/403


class ImportUpsert(TestCase):
    def test_import_creates_then_updates(self):
        tr = _user("tr2", GROUP_TRANSPORT)
        self.client.force_login(tr)
        csv1 = ("klient,zlecenie,indeks,partia,ilość,jm\n"
                "Kyiv,Z1,A1,L1,100,szt\n").encode("utf-8")
        self.client.post(reverse("ui:ukraine_import"),
                         {"file": SimpleUploadedFile("z.csv", csv1, content_type="text/csv")})
        ln = UkraineOrderLine.objects.get(index_code="A1", lot="L1")
        self.assertEqual(ln.requested_qty, 100.0)
        # Ponowny import tej samej linii z większą ilością → aktualizacja, nie duplikat.
        csv2 = ("klient,zlecenie,indeks,partia,ilość,jm\n"
                "Kyiv,Z1,A1,L1,250,szt\n").encode("utf-8")
        self.client.post(reverse("ui:ukraine_import"),
                         {"file": SimpleUploadedFile("z.csv", csv2, content_type="text/csv")})
        self.assertEqual(UkraineOrderLine.objects.filter(index_code="A1", lot="L1").count(), 1)
        ln.refresh_from_db()
        self.assertEqual(ln.requested_qty, 250.0)
