"""Podpowiedź „📦 Pobierano w:" zbija surowe kody JM SAP na 4 kanoniczne kafle
(base/opz/kar/pal) — brudne warianty (KAR/KARTON/Kar.) nie wyciekają jako osobne
jednostki, a etykieta ma max 4 pozycje."""
from django.test import TestCase
from django.utils import timezone

from ui.models import HandlingUnit, HandlingUnitItem, Shipment, PickerActivity, PickerActivityBatch
from huctl.views.hu_control import _annotate_picked_units


class PickedLabelCapTests(TestCase):
    def test_dirty_unit_variants_collapse_to_four_tiles(self):
        sh = Shipment.objects.create(name="D1")
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="H1", status="planned")
        it = HandlingUnitItem.objects.create(hu=hu, ref_code="RG-50", base_unit="OP", base_qty=10)
        batch = PickerActivityBatch.objects.create(name="t")
        for unit, qty in [("KAR", 2), ("KARTON", 1), ("Kar.", 1), ("OP", 5), ("OPZ", 3)]:
            PickerActivity.objects.create(batch=batch, location_code="L",
                                          confirmed_at=timezone.now(), material_code="RG-50",
                                          qty=qty, unit=unit)
        _annotate_picked_units([it], hu)
        # 5 surowych kodów → 3 kafle (kar, base, opz), bez duplikatów, ≤4.
        self.assertEqual(it.picked_units, {"kar", "base", "opz"})
        parts = it.picked_label.split(" + ")
        self.assertLessEqual(len(parts), 4)
        self.assertEqual(set(parts), {"KAR", "OP", "OPZ"})
