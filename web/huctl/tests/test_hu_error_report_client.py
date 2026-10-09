"""Raport błędów HU: kolumna i filtr klienta (nr + nazwa) pod reklamacje."""
from django.test import TestCase
from django.urls import reverse

from ui.models import (Customer, Product, Shipment, ShipmentLine,
                       PalletizationInstruction)
from huctl.views.hu import _generate_handling_units
from .test_hu_control import _user_all_roles


class HUErrorReportClientTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles("ctrl-client")
        cls.cust = Customer.objects.create(name="Apteka Testowa", code="KL-77",
                                           kunnr="0000123")
        cls.sh = Shipment.objects.create(name="Dostawa 1", stowage_efficiency_pct=80,
                                         recipient_name="PHARMO DEMO SRL",
                                         customer=cls.cust)
        p = Product.objects.create(code="NL100-100", name="NONVI lux")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=10, demand_pcs=100, is_active=True)
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=8,
                                    unit="kar", source_unit="OP")
        _generate_handling_units(cls.sh)
        cls.hu = cls.sh.handling_units.first()

    def setUp(self):
        self.client.force_login(self.user)
        # Wygeneruj błąd ilościowy, żeby raport miał wiersz.
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.it = self.hu.items.first()
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.it.pk]),
                         {"action": "confirm", "sure": "1",
                          "qty_base": str(self.it.base_qty - 1)})

    def test_report_shows_client(self):
        resp = self.client.get(reverse("ui:hu_error_report"))
        self.assertContains(resp, "Apteka Testowa")
        self.assertContains(resp, "KL-77")

    def test_client_filter_hits_and_misses(self):
        # Trafia po nazwie i KUNNR; pudło daje pustą listę.
        hit = self.client.get(reverse("ui:hu_error_report"), {"client": "apteka"})
        self.assertContains(hit, self.it.ref_code)
        kunnr = self.client.get(reverse("ui:hu_error_report"), {"client": "0000123"})
        self.assertContains(kunnr, self.it.ref_code)
        miss = self.client.get(reverse("ui:hu_error_report"), {"client": "nie-ma"})
        self.assertNotContains(miss, self.it.ref_code)

    def test_csv_contains_client(self):
        csv = self.client.get(reverse("ui:hu_error_report"), {"export": "csv"})
        body = csv.content.decode("utf-8-sig")
        self.assertIn("Apteka Testowa", body)
        self.assertIn("KL-77", body)

    def test_report_shows_ref_and_daily_trend(self):
        # #17: cięcie per indeks (REF) + trend dzienny liczą ten sam błąd.
        resp = self.client.get(reverse("ui:hu_error_report"))
        self.assertContains(resp, "Wg indeksu")
        self.assertContains(resp, "Trend dzienny")
        by_ref = {r[0]: r[1] for r in resp.context["by_ref"]}
        self.assertEqual(by_ref.get(self.it.ref_code), 1)
        self.assertEqual(sum(c for _d, c in resp.context["by_day"]), 1)
