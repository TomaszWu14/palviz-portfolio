"""Panel „Dane magazynowe": agregacja typów magazynu do filtrów (Wydawcze/Zapasowe/
DLT/Wysyłka/Inne) + ilość (bieżący zapas) przy wierszach fix/procesów."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Product, Shipment, HandlingUnit, HandlingUnitItem
from ui.roles import GROUP_WAREHOUSE
from ui.views.phv import _storage_strategy, _wh_category


def _wh():
    u = get_user_model().objects.create_user(username="mag", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_WAREHOUSE)[0])
    return u


def _stock(product, wtype, location, qty, seq, expiry=None, lot="", status=""):
    sh = Shipment.objects.create(name=f"S{seq}", is_stock=True)
    hu = HandlingUnit.objects.create(shipment=sh, seq=1, code=f"HU{seq}",
                                     warehouse_type=wtype, location=location,
                                     stock_status=status)
    HandlingUnitItem.objects.create(hu=hu, product=product, ref_code=product.code,
                                    expected_qty=qty, expiry=expiry, lot=lot)


class WarehouseCategoryTest(TestCase):
    def test_category_mapping(self):
        self.assertEqual(_wh_category("0050"), "wydawcze")
        self.assertEqual(_wh_category("0010"), "zapasowe")
        self.assertEqual(_wh_category("92EX"), "wysylka")
        self.assertEqual(_wh_category("WCGL"), "wysylka")
        self.assertEqual(_wh_category("BROK"), "inne")      # nieznany → Inne
        self.assertEqual(_wh_category(""), "inne")

    def test_stock_groups_have_category_and_present_list(self):
        p = Product.objects.create(code="REF-C", name="X")
        _stock(p, "0010", "C1-01-010A", 100, 1)             # zapasowe
        _stock(p, "92EX", "W1-02-020B", 50, 2)              # wysylka
        s = _storage_strategy(p)
        cats = {g["code"]: g["category"] for g in s["stock_groups"]}
        self.assertEqual(cats["0010"], "zapasowe")
        self.assertEqual(cats["92EX"], "wysylka")
        keys = [c["key"] for c in s["stock_categories"]]
        self.assertEqual(keys, ["zapasowe", "wysylka"])     # kolejność wg WAREHOUSE_CATEGORIES

    def test_9010_splits_dlt_vs_grzone_by_location(self):
        p = Product.objects.create(code="REF-L", name="X")
        _stock(p, "9010", "DLT-01-001A", 30, 1)             # 9010 + lokalizacja DLT → DLT
        _stock(p, "9010", "GR-02-002B", 20, 2)              # 9010 inne → GR-Zone → Inne
        s = _storage_strategy(p)
        by = {g["code"]: g["category"] for g in s["stock_groups"]}
        self.assertEqual(by["9010 (DLT)"], "dlt")
        self.assertEqual(by["9010 (GR-Zone)"], "inne")
        self.assertIn("dlt", [c["key"] for c in s["stock_categories"]])

    def test_stock_total_sums_across_types(self):
        p = Product.objects.create(code="REF-T", name="X")
        _stock(p, "0010", "C1-01-010A", 100, 1)             # zapas zwykły
        _stock(p, "0011", "C2-02-020B", 50, 2)              # zapas zafoliowany
        s = _storage_strategy(p)
        self.assertEqual(s["stock_total"]["count"], 2)      # 1 paleta + 1 paleta
        self.assertEqual(s["stock_total"]["base_qty"], 150) # 100 + 50 szt

    def test_stock_total_present_for_single_type(self):
        # Headline pokazuje total on-hand ZAWSZE gdy jest zapas (też przy 1 typie) —
        # inaczej gubił sztuki z Fixa / znikał przy 1 kuble (decyzja produktowa).
        p = Product.objects.create(code="REF-S", name="X")
        _stock(p, "0010", "C1-01-010A", 100, 1)
        st = _storage_strategy(p)["stock_total"]
        self.assertEqual(st["count"], 1)
        self.assertEqual(st["base_qty"], 100)

    def test_process_row_carries_current_qty(self):
        p = Product.objects.create(code="REF-F", name="X")
        _stock(p, "0050", "B0-38-471A", 240, 1)             # fix/proces, bez FixLocation
        s = _storage_strategy(p)
        loc = s["processes"][0]["locations"][0]
        self.assertEqual(loc["code"], "B0-38-471A")
        self.assertEqual(loc["qty"], 240)                   # ilość przy wierszu procesu

    def test_view_renders_filter_chips_and_qty(self):
        self.client.force_login(_wh())
        p = Product.objects.create(code="REF-V", name="X")
        _stock(p, "0050", "B0-38-471A", 240, 1)
        _stock(p, "0010", "C1-01-010A", 100, 2)
        _stock(p, "92EX", "W1-02-020B", 50, 3)
        r = self.client.get(reverse("ui:phv_home"), {"q": "REF-V"})
        self.assertContains(r, "phvFilterCat")              # filtr obecny
        self.assertContains(r, "Zapasowe")
        self.assertContains(r, "Wysyłka")
        self.assertContains(r, "240 szt")                   # ilość przy wierszu procesu 0050


class DrillDownFefoTest(TestCase):
    """Drill-down: kolejność FEFO (najkrótsza ważność u góry), znacznik blokady,
    wydawcze przed innymi typami, Σ dynamiczna w widoku."""

    def test_rows_sorted_by_expiry_fefo(self):
        import datetime
        p = Product.objects.create(code="REF-FEFO", name="X")
        _stock(p, "0010", "C1-01-010A", 10, 1, expiry=datetime.date(2030, 5, 1))
        _stock(p, "0010", "C1-01-020A", 10, 2, expiry=datetime.date(2027, 1, 1))  # najkrótsza
        _stock(p, "0010", "C1-01-030A", 10, 3, expiry=None)                        # brak → koniec
        rows = _storage_strategy(p)["stock_groups"][0]["rows"]
        self.assertEqual([r["expiry"] for r in rows],
                         [datetime.date(2027, 1, 1), datetime.date(2030, 5, 1), None])

    def test_blocked_location_flag(self):
        from ui.models import WarehouseLocationMasterBatch, WarehouseLocationMaster
        p = Product.objects.create(code="REF-BLK", name="X")
        _stock(p, "0010", "C1-01-010A", 10, 1)
        b = WarehouseLocationMasterBatch.objects.create(is_active=True)
        WarehouseLocationMaster.objects.create(batch=b, location_code="C1-01-010A",
                                               blocked_pick=True)
        rows = _storage_strategy(p)["stock_groups"][0]["rows"]
        self.assertTrue(rows[0]["blocked"])

    def test_wydawcze_group_sorts_first(self):
        p = Product.objects.create(code="REF-WYD", name="X")
        _stock(p, "0010", "C1-01-010A", 10, 1)              # zapasowe (1 paleta)
        _stock(p, "0010", "C1-01-011A", 10, 2)              # zapasowe (2. paleta)
        _stock(p, "0051", "W0-01-010A", 10, 3)              # wydawcze (0051, nie-proces)
        cats = [g["category"] for g in _storage_strategy(p)["stock_groups"]]
        self.assertEqual(cats[0], "wydawcze")               # mimo mniejszej liczby palet

    def test_view_has_dynamic_sum_and_data_attrs(self):
        self.client.force_login(_wh())
        p = Product.objects.create(code="REF-SUM", name="X")
        _stock(p, "0010", "C1-01-010A", 100, 1)
        _stock(p, "0011", "C2-02-020B", 50, 2)
        r = self.client.get(reverse("ui:phv_home"), {"q": "REF-SUM"})
        self.assertContains(r, 'id="phv-sum"')              # cel Σ w nagłówku
        self.assertContains(r, "phvSumUpdate")              # przeliczanie w JS
        self.assertContains(r, 'data-count=')               # atrybuty do sumowania

    def test_stock_status_kind_mapping(self):
        from ui.views.phv import _stock_status_kind
        self.assertEqual(_stock_status_kind("B6"), "blocked")   # BB
        self.assertEqual(_stock_status_kind("Q4"), "quality")   # QQ
        self.assertEqual(_stock_status_kind("R8"), "returns")   # RR
        self.assertEqual(_stock_status_kind("F2"), "free")      # FF
        self.assertEqual(_stock_status_kind(""), "free")
        self.assertEqual(_stock_status_kind("ZZ"), "free")      # nieznany → wolne

    def test_drilldown_carries_status_kind(self):
        p = Product.objects.create(code="REF-ST", name="X")
        _stock(p, "0010", "C1-01-010A", 10, 1, status="Q4")
        rows = _storage_strategy(p)["stock_groups"][0]["rows"]
        self.assertEqual(rows[0]["status_kind"], "quality")

    def test_view_empty_stock_state(self):
        self.client.force_login(_wh())
        p = Product.objects.create(code="REF-EMPTY", name="X")   # brak HU
        r = self.client.get(reverse("ui:phv_home"), {"q": "REF-EMPTY"})
        self.assertContains(r, "Brak zapasu w magazynie")
