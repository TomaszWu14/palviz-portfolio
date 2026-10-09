"""resolve_ref: jedno źródło prawdy scan→Product — pełna kaskada testowana bez HTTP.

Regresja, którą to zamyka: ten sam kod (np. EAN kartonu) rozwiązywał się na PHV,
a 404 na innych ekranach, bo każdy widok reimplementował 1–2 gałęzie kaskady.
"""
from django.test import TestCase

from ui.models import Product, PalletizationInstruction, Carton, InnerPack
from ui.product_lookup import resolve_ref


def _product_with_packaging(code="R100", ean="5900000000017"):
    inner = InnerPack.objects.create(name="OPZ", length_cm=21, width_cm=12, height_cm=11,
                                     ean="5901111111114")
    carton = Carton.objects.create(name="K", length_cm=29, width_cm=25, height_cm=22,
                                   unit_weight_kg=0.5, ean="5902222222225", inner_pack=inner)
    p = Product.objects.create(code=code, name="Rękawice", ean=ean,
                               unit_length_cm=21, unit_width_cm=12, unit_height_cm=5.5)
    PalletizationInstruction.objects.create(
        product=p, name="v1", is_active=True, version=1,
        unit_weight=0.458, pcs_per_carton=10, carton_l=29, carton_w=25, carton_h=22,
        carton=carton, inner_pack=inner,
        pallet_length_cm=120, pallet_width_cm=80,
        pallet_base_height_cm=15, max_height_total_cm=213, layouts=[])
    return p


class ResolveRefCascade(TestCase):
    def setUp(self):
        self.p = _product_with_packaging()

    def test_every_code_resolves_same_product(self):
        # REF, EAN sztuki, EAN kartonu, EAN opak. zbiorczego — jeden produkt, każde wejście.
        for q in ("R100", "r100", "5900000000017", "5902222222225", "5901111111114"):
            self.assertEqual(resolve_ref(q), self.p, f"nie rozwiązało: {q!r}")

    def test_ean_first_for_barcode_shape(self):
        # Numeryczny 13-cyfrowy kod idzie najpierw ścieżką EAN, nie REF.
        clash = Product.objects.create(code="5900000000017", name="Kolizja",
                                       unit_length_cm=1, unit_width_cm=1, unit_height_cm=1)
        # q = EAN sztuki produktu p (13 cyfr) → ma trafić w p (przez EAN), nie w clash (przez code)
        self.assertEqual(resolve_ref("5900000000017"), self.p)
        self.assertNotEqual(resolve_ref("5900000000017"), clash)

    def test_unknown_and_empty(self):
        self.assertIsNone(resolve_ref("NIEMA"))
        self.assertIsNone(resolve_ref(""))
        self.assertIsNone(resolve_ref(None))
        self.assertIsNone(resolve_ref("   "))

    def test_active_only_skips_retired(self):
        self.p.is_active = False
        self.p.save(update_fields=["is_active"])
        # domyślnie (skaner) — wciąż znajduje wycofany indeks
        self.assertEqual(resolve_ref("R100"), self.p)
        # active_only (wyszukiwarka magazynu) — nie routuje do wycofanego
        self.assertIsNone(resolve_ref("R100", active_only=True))
