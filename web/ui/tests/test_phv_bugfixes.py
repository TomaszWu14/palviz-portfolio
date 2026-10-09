"""PHV bugfixy (code-review): foto weryfikowane realnie (nie nagłówek content_type),
dopasowanie lokalizacji case-insensitive (fix „poniżej minimum"), mnożnik OP→JU = units_per_piece."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import (Product, PalletizationInstruction, PackagingIssue, Shipment,
                       HandlingUnit, HandlingUnitItem, FixLocation)
from ui.roles import GROUP_WAREHOUSE
from ui.hierarchy import enrich_pallet_metrics


def _wh():
    u = get_user_model().objects.create_user(username="mag", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_WAREHOUSE)[0])
    return u


class PhvPhotoValidationTests(TestCase):
    def setUp(self):
        self.client.force_login(_wh())

    def test_svg_disguised_as_png_rejected(self):
        # SVG z inline JS, podszyty pod image/png — nagłówkowy check by przepuścił.
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        f = SimpleUploadedFile("x.png", svg, content_type="image/png")
        self.client.post(reverse("ui:phv_report"), {
            "ref_code": "R1", "issue_type": "other", "description": "x", "photo": f})
        # Zgłoszenie odrzucone przez walidację obrazu (nie zapisane z trefnym plikiem).
        iss = PackagingIssue.objects.first()
        self.assertTrue(iss is None or not iss.photo)


class PhvLocationCaseTests(TestCase):
    def test_fix_below_min_case_insensitive(self):
        from ui.views.phv import _storage_strategy
        p = Product.objects.create(code="RG-9", name="X")
        sh = Shipment.objects.create(name="Stock", is_stock=True)
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="H1",
                                         warehouse_type="0050", location="b0-38-471a")  # lower
        HandlingUnitItem.objects.create(hu=hu, product=p, ref_code=p.code, expected_qty=100)
        FixLocation.objects.create(ref_code="RG-9", location_code="B0-38-471A",  # UPPER
                                   warehouse_type="0050", min_qty=60)
        strat = _storage_strategy(p)
        fx = strat["fixes"][0]
        self.assertEqual(fx["current"], 100)       # dopasowane mimo różnicy case
        self.assertFalse(fx["below_min"])          # 100 >= 60 → NIE poniżej minimum


class PhvJuMultiplierTests(TestCase):
    def test_op_to_ju_multiplier_is_upp(self):
        p = Product.objects.create(code="RJ", name="X")
        instr = PalletizationInstruction.objects.create(
            product=p, version=1, is_active=True, unit_weight=0.5, pcs_per_carton=10,
            units_per_piece=24, carton_l=29, carton_w=25, carton_h=22,
            pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15,
            max_height_total_cm=200)
        levels = [{"key": "unit"}, {"key": "ju"}]
        summary = {"pcs_per_pallet": 990, "cartons_per_pallet": 99}
        enrich_pallet_metrics(levels, summary, instr)
        self.assertEqual(levels[0]["mult"], 24)    # 1 OP = 24 JU (units_per_piece), nie 1
