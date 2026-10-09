"""FK→kod seam on ShipmentLine (Faza 3): the durable business-key `product_code` is kept
in lockstep with the FK by save(), so the line survives the future master-data DB split."""
from django.test import TestCase

from ui.models import Product, Shipment, ShipmentLine


class ShipmentLineCodeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.p = Product.objects.create(code="SL-1", name="Linia 1")
        cls.sh = Shipment.objects.create(name="Wysyłka")

    def test_create_fills_code_from_fk(self):
        line = ShipmentLine.objects.create(shipment=self.sh, product=self.p, quantity=2)
        self.assertEqual(line.product_code, "SL-1")
        line.refresh_from_db()
        self.assertEqual(line.product_code, "SL-1")

    def test_save_with_update_fields_still_syncs_code(self):
        line = ShipmentLine.objects.create(shipment=self.sh, product=self.p, quantity=2)
        # Simulate a legacy row (blank code), then a partial save — the hook must widen
        # update_fields so the synced code actually persists.
        ShipmentLine.objects.filter(pk=line.pk).update(product_code="")
        line.refresh_from_db()
        line.quantity = 5
        line.save(update_fields=["quantity"])
        line.refresh_from_db()
        self.assertEqual(line.quantity, 5)
        self.assertEqual(line.product_code, "SL-1")

    def test_ref_prefers_code_and_reads_master_data(self):
        line = ShipmentLine.objects.create(shipment=self.sh, product=self.p, quantity=1)
        self.assertEqual(line.product_ref, "SL-1")
        self.assertEqual(line.product_data()["name"], "Linia 1")

    def test_ref_falls_back_to_fk_for_legacy_rows(self):
        line = ShipmentLine.objects.create(shipment=self.sh, product=self.p, quantity=1)
        ShipmentLine.objects.filter(pk=line.pk).update(product_code="")   # legacy shape
        line.refresh_from_db()
        self.assertEqual(line.product_ref, "SL-1")
