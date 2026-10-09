"""The 0050 data migration backfills is_stock on legacy 'Stock magazynowy' containers."""
import importlib
from django.test import TestCase
from ui import models as m


class FlagLegacyStockTests(TestCase):
    def test_backfill_only_touches_named_unflagged_containers(self):
        legacy = m.Shipment.objects.create(name="Stock magazynowy", is_stock=False)
        already = m.Shipment.objects.create(name="Stock magazynowy", is_stock=True)
        transport = m.Shipment.objects.create(name="Dostawa 123", is_stock=False)

        mod = importlib.import_module("ui.migrations.0050_flag_legacy_stock_containers")

        # Rejestr historyczny migracji zna Shipment pod "ui"; po W2 żywy model jest w
        # transport — shim podaje właściwą klasę bez ruszania zaaplikowanej migracji.
        class _Apps:
            @staticmethod
            def get_model(app_label, name):
                return m.Shipment

        mod.flag_legacy_stock(_Apps, None)

        legacy.refresh_from_db(); already.refresh_from_db(); transport.refresh_from_db()
        self.assertTrue(legacy.is_stock)        # backfilled
        self.assertTrue(already.is_stock)       # untouched (already true)
        self.assertFalse(transport.is_stock)    # real shipment left alone

