# Fala 3: etykieta logistyczna z danymi eksportowymi (klient, kraj, numer WZ).
from django.test import TestCase

from ui.labels import zpl_logistics_label
from ui.models import Customer, HandlingUnit, Shipment


class LogisticsLabelExportTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Demoprime GmbH", country="DE",
                                                requires_logistics_label=True)
        self.sh = Shipment.objects.create(name="D-1", customer=self.customer,
                                          recipient_name="Demoprime Lager",
                                          destination_country="DE", wz_number="WZ/2026/77")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, code="HU1")

    def test_label_contains_customer_country_and_wz(self):
        zpl = zpl_logistics_label(self.hu, self.customer)
        self.assertIn("Demoprime GmbH", zpl)
        self.assertIn("DE", zpl)
        self.assertIn("WZ: WZ/2026/77", zpl)

    def test_missing_wz_line_skipped(self):
        self.sh.wz_number = ""
        self.sh.save(update_fields=["wz_number"])
        zpl = zpl_logistics_label(self.hu, self.customer)
        self.assertNotIn("WZ:", zpl)

    def test_no_customer_still_renders(self):
        zpl = zpl_logistics_label(self.hu, None)
        self.assertIn("ETYKIETA LOGISTYCZNA", zpl)
        self.assertNotIn("Demoprime GmbH", zpl)   # linia klienta pomijana bez klienta
