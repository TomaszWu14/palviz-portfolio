"""Liczba palet na JEDNEJ wysokości (audyt BIZ-007, decyzja „Wysokość wybrana na wysyłce”).

Dawniej: wycena spedycji liczyła palety na _DEFAULT_PALLET_HEIGHT (1,8 m), zapytanie do
magazynu na selected_pallet_height_cm (ale wysokość spoza domyślnych scenariuszy 1,8/2,25 m
cicho spadała na 1,8 m), KPI na scenarios[0]. Teraz wycena (ekran, e-mail, strona
odpowiedzi), zapytanie do magazynu, KPI, lista wysyłek i pakiet kierowcy liczą palety dla
wysokości wybranej na wysyłce; bez wyboru — domyślnej (wynik jak dotąd).
"""
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from testkit.personas import client_for
from transport.kpi import compute_transport_kpi
from transport.models import DriverAssignment, QuoteRecipient, ShipmentQuoteOffer, WarehouseReadiness
from transport.pallet_count import pallet_height_cm, shipment_pallet_calc
from transport.views.quotes import _quote_email_context
from ui.models import PalletizationInstruction, Product, Shipment, ShipmentLine
from ui.views.core import _DEFAULT_PALLET_HEIGHT, _calc_shipment_data


def _count_at(sh, height):
    return _calc_shipment_data(sh, heights=[height], with_packing=False)["scenarios"][0]["n_pallets"]


class PalletCountOneHeightTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # ~10 m³ kartonów, lekkie → liczba palet zależy od wysokości (objętość dominuje).
        p = Product.objects.create(code="VOL-1", name="Objętościowy")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.1, pcs_per_carton=10, demand_pcs=100, is_active=True)
        cls.sh = Shipment.objects.create(name="WYS-H", destination_country="DE",
                                         destination_city="Berlin")
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=334, unit="kar")
        rec = QuoteRecipient.objects.create(name="Speed", email="s@speed.pl")
        cls.offer = ShipmentQuoteOffer.objects.create(shipment=cls.sh, recipient=rec,
                                                      carrier_name="Speed")

    def setUp(self):
        self.client = client_for("Transport")

    def _set_height(self, cm):
        Shipment.objects.filter(pk=self.sh.pk).update(selected_pallet_height_cm=cm)
        self.sh.refresh_from_db()

    def _observed(self):
        """Liczba palet z każdego miejsca, które ją pokazuje/wysyła: {miejsce: (palety, wys.)}."""
        sh = self.sh
        quote = self.client.get(reverse("ui:planner_shipment_quote", args=[sh.pk])).context["scenario"]
        email = _quote_email_context(RequestFactory().get("/"), sh, self.offer)
        resp = Client().get(reverse("ui:quote_response", args=[self.offer.token])).context
        self.client.post(reverse("ui:planner_shipment_wh_request", args=[sh.pk, "pre"]))
        wh = WarehouseReadiness.objects.get(shipment=sh, kind="pre").asked_pallets
        kpi = compute_transport_kpi()["kpi"]["n_pallets"]
        page = self.client.get(reverse("ui:planner_shipments")).context["page_obj"]
        row = next(s for s in page.object_list if s.pk == sh.pk).n_pallets
        da, _ = DriverAssignment.objects.get_or_create(shipment=sh)
        drv = Client().get(reverse("ui:driver_confirm", args=[da.confirm_token])).context["n_pallets"]
        return {
            "wycena (ekran)": (quote["n_pallets"], quote["max_h_cm"]),
            "wycena (e-mail)": (email["n_pallets"], email["height_cm"]),
            "wycena (strona spedycji)": (resp["n_pallets"], resp["height_cm"]),
            "zapytanie do magazynu": (wh, None),
            "KPI transportu": (kpi, None),
            "lista wysyłek": (row, None),
            "pakiet kierowcy": (drv, None),
        }

    def _assert_all(self, observed, n, h):
        for where, (got_n, got_h) in observed.items():
            with self.subTest(miejsce=where):
                self.assertEqual(got_n, n)
                if got_h is not None:
                    self.assertEqual(got_h, h)

    def test_selected_height_drives_every_pallet_count(self):
        default_n = _count_at(self.sh, _DEFAULT_PALLET_HEIGHT)
        # 225 = drugi domyślny scenariusz; 220 = wysokość spoza domyślnych (dawniej fallback).
        for cm in (225, 220):
            with self.subTest(wysokosc=cm):
                self._set_height(cm)
                expected = _count_at(self.sh, cm)
                self.assertNotEqual(expected, default_n)      # dane rozróżniają wysokości
                self._assert_all(self._observed(), expected, cm)

    def test_no_selection_falls_back_to_default_height(self):
        self._set_height(None)
        self.assertEqual(pallet_height_cm(self.sh), _DEFAULT_PALLET_HEIGHT)
        self._assert_all(self._observed(), _count_at(self.sh, _DEFAULT_PALLET_HEIGHT),
                         _DEFAULT_PALLET_HEIGHT)

    def test_invalid_stored_height_falls_back_to_default(self):
        self._set_height(-5)
        self.assertEqual(pallet_height_cm(self.sh), _DEFAULT_PALLET_HEIGHT)

    def test_helper_scenario_matches_height(self):
        self._set_height(220)
        calc, sc = shipment_pallet_calc(self.sh, with_packing=False)
        self.assertEqual([s["max_h_cm"] for s in calc["scenarios"]], [220])
        self.assertEqual(sc["max_h_cm"], 220)
