# Fala 0: import HU z NOWYMI kolumnami feedu (WZ/KUNNR/kraj/daty/kompletacja/wagi)
# + regresja starego formatu (nowe kolumny są opcjonalne).
from datetime import date

from django.test import TestCase

from ui.models import Customer, HandlingUnit, Shipment
from huctl.views.hu import _import_hu_rows, _map_hu_columns

OLD_HEADER = ["dostawa", "jednostka obsługi", "produkt", "ilość", "jedn. miary",
              "miejsce składowania", "typ magazynu", "odbiorca", "picker"]

NEW_HEADER = OLD_HEADER + ["numer wz", "kod klienta", "kraj", "data utworzenia dostawy",
                           "data dostawy", "skompletowana", "waga brutto",
                           "długość", "szerokość", "wysokość"]


class OldFeedRegressionTests(TestCase):
    def test_old_format_still_imports(self):
        ok, info = _import_hu_rows(OLD_HEADER, [
            ["D-100", "HU1", "ZR-1", "10", "OP", "B0-01-100A", "P1", "Szpital X", "jan"],
            ["D-100", "HU1", "ZR-2", "5", "OP", "", "", "", ""],
        ])
        self.assertTrue(ok, info)
        hu = HandlingUnit.objects.get(code="HU1")
        self.assertEqual(hu.items.count(), 2)
        self.assertFalse(hu.is_completed)
        self.assertEqual(hu.shipment.wz_number, "")

    def test_kod_klienta_not_stolen_by_ref_alias(self):
        # "kod klienta" musi trafić do kunnr, a "produkt" do ref — kolejność aliasów.
        idx = _map_hu_columns(["kod klienta", "produkt", "jednostka obsługi"])
        self.assertEqual(idx["kunnr"], 0)
        self.assertEqual(idx["ref"], 1)

    def test_skompletowana_not_stolen_by_picker_alias(self):
        idx = _map_hu_columns(["skompletowana", "kompletujący", "jednostka obsługi", "produkt"])
        self.assertEqual(idx["completed"], 0)
        self.assertEqual(idx["picker"], 1)


class NewFeedImportTests(TestCase):
    def _row(self, **over):
        base = {"dostawa": "D-200", "hu": "HU9", "ref": "ZR-9", "qty": "10", "unit": "OP",
                "loc": "B0-01-100A", "wh": "P1", "odb": "Szpital X", "picker": "jan",
                "wz": "WZ/2026/123", "kunnr": "0000123", "kraj": "de",
                "created": "2026-08-01", "ddate": "05.08.2026", "compl": "X",
                "waga": "12,5", "dl": "120", "sz": "80", "wys": "145"}
        base.update(over)
        return [base["dostawa"], base["hu"], base["ref"], base["qty"], base["unit"],
                base["loc"], base["wh"], base["odb"], base["picker"], base["wz"],
                base["kunnr"], base["kraj"], base["created"], base["ddate"],
                base["compl"], base["waga"], base["dl"], base["sz"], base["wys"]]

    def test_new_columns_land_on_models(self):
        Customer.objects.create(name="Szpital X", kunnr="123")
        ok, info = _import_hu_rows(NEW_HEADER, [self._row()])
        self.assertTrue(ok, info)
        sh = Shipment.objects.get(name="D-200")
        self.assertEqual(sh.wz_number, "WZ/2026/123")
        self.assertEqual(sh.kunnr, "0000123")
        self.assertEqual(sh.destination_country, "DE")
        self.assertEqual(sh.outbound_created_date, date(2026, 8, 1))
        self.assertEqual(sh.outbound_delivery_date, date(2026, 8, 5))
        self.assertEqual(sh.customer.kunnr, "123")   # KUNNR match mimo zer wiodących
        hu = HandlingUnit.objects.get(code="HU9")
        self.assertTrue(hu.is_completed)
        self.assertEqual((hu.length_cm, hu.width_cm, hu.height_cm), (120.0, 80.0, 145.0))
        self.assertEqual(hu.items.get().weight_kg, 12.5)

    def test_unmatched_kunnr_saved_without_customer(self):
        ok, _ = _import_hu_rows(NEW_HEADER, [self._row(kunnr="999")])
        self.assertTrue(ok)
        sh = Shipment.objects.get(name="D-200")
        self.assertEqual(sh.kunnr, "999")
        self.assertIsNone(sh.customer)

    def test_completed_toggles_both_ways(self):
        _import_hu_rows(NEW_HEADER, [self._row(compl="X")])
        self.assertTrue(HandlingUnit.objects.get(code="HU9").is_completed)
        _import_hu_rows(NEW_HEADER, [self._row(compl="nie")])
        self.assertFalse(HandlingUnit.objects.get(code="HU9").is_completed)
