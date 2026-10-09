"""Audyt PERF-005: generowanie HU wołało product.latest_instruction() per linia przesyłki
bez prefetch_related("instructions") → N+1 (metoda sama to dokumentuje). Pętla przelicznika
szt./karton używa teraz prefetcha — liczba zapytań tej pętli nie rośnie z liczbą linii."""
from unittest import mock

from django.test import TestCase

from huctl.views.hu import _generate_handling_units
from ui.models import PalletizationInstruction, Product, Shipment, ShipmentLine


def _shipment(n_lines):
    sh = Shipment.objects.create(name=f"Dostawa {n_lines}")
    for i in range(n_lines):
        p = Product.objects.create(code=f"PQ-{n_lines}-{i}", name="Produkt")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25, unit_weight=0.5,
            pcs_per_carton=6 + i, demand_pcs=100, is_active=True)
        ShipmentLine.objects.create(shipment=sh, product=p, quantity=2, unit="kar")
    return sh


class HuGenerateQueriesTests(TestCase):
    def test_latest_instruction_uses_prefetch(self):
        seen = []
        orig = Product.latest_instruction

        def spy(self):
            seen.append("instructions" in getattr(self, "_prefetched_objects_cache", {}))
            return orig(self)

        sh = _shipment(3)
        with mock.patch.object(Product, "latest_instruction", spy):
            _generate_handling_units(sh)
        self.assertTrue(seen)
        # wywołania z pętli przelicznika (ostatnie N) mają prefetch — brak N+1
        self.assertTrue(all(seen[-3:]), seen)

    def test_base_qty_still_uses_pcs_per_carton(self):
        sh = _shipment(2)
        _generate_handling_units(sh)
        items = {it.ref_code: it for hu in sh.handling_units.all() for it in hu.items.all()}
        self.assertEqual(items["PQ-2-1"].base_qty, items["PQ-2-1"].alt_qty * 7)
