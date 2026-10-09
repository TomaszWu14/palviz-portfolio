"""promote_variant: transakcja promocji wariantu — testowana bez HTTP.

Zysk deepeningu #4: bump wersji, odrzucenie niepasującego, domknięcie zgłoszenia i
kolejność audytu fill były osiągalne tylko przez POST. Teraz to wywołanie funkcji.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from ui.models import (Product, PalletizationInstruction, CartonAlternative,
                       CartonPromotion, PackagingIssue)
from ui.views.carton_opt_variants import promote_variant


def _base(p):
    return PalletizationInstruction.objects.create(
        product=p, version=1, is_active=True, unit_weight=1.0, pcs_per_carton=10,
        carton_l=20, carton_w=20, carton_h=120, pallet_length_cm=120, pallet_width_cm=80,
        max_height_total_cm=215, pallet_base_height_cm=15, max_weight_kg=1000)


class PromoteVariantUnit(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("op", password="x")
        self.p = Product.objects.create(code="PROMO1", name="Materiał")
        _base(self.p)
        self.alt = CartonAlternative.objects.create(product=self.p, label="Lepszy",
                                                    length_cm=40, width_cm=40, height_cm=30)

    def test_bumps_version_old_untouched(self):
        res = promote_variant(self.alt, self.u)
        self.assertTrue(res.ok)
        self.assertEqual(res.instruction.version, 2)
        v1 = PalletizationInstruction.objects.get(product=self.p, version=1)
        self.assertEqual((v1.carton_l, v1.carton_w, v1.carton_h), (20, 20, 120))   # stara nietknięta
        self.assertEqual((res.instruction.carton_l, res.instruction.carton_w,
                          res.instruction.carton_h), (40, 40, 30))                  # nowa z wariantu

    def test_non_fitting_rejected_no_write(self):
        big = CartonAlternative.objects.create(product=self.p, label="ZaDuży",
                                               length_cm=200, width_cm=200, height_cm=30)
        before = PalletizationInstruction.objects.filter(product=self.p).count()
        res = promote_variant(big, self.u)
        self.assertFalse(res.ok)
        self.assertIn("nie mieści", res.error)
        self.assertEqual(PalletizationInstruction.objects.filter(product=self.p).count(), before)
        self.assertFalse(CartonPromotion.objects.exists())      # zero śladu przy odrzuceniu

    def test_resolves_linked_issue(self):
        iss = PackagingIssue.objects.create(ref_code="PROMO1", issue_type="carton_fit_pallet",
                                            product=self.p)
        res = promote_variant(self.alt, self.u, issue=iss)
        self.assertTrue(res.issue_resolved)
        iss.refresh_from_db()
        self.assertEqual(iss.status, "resolved")
        self.assertIsNotNone(iss.resolved_at)

    def test_audit_fill_before_after_order(self):
        promote_variant(self.alt, self.u)
        pr = CartonPromotion.objects.get(product=self.p)
        self.assertEqual(pr.version, 2)
        self.assertEqual(pr.dims, "40×40×30")
        self.assertEqual(pr.user, self.u)
        # płaski karton (wiele warstw) wypełnia lepiej niż baseline 20×20×120 (1 warstwa)
        self.assertGreater(pr.fill_after, pr.fill_before)
