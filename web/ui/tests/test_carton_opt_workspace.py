"""Moduł „Optymalizacja kartonów" (Faza 1): skrzynka zgłoszeń + routing powiadomień.

Sprawdza, że:
  • zgłoszenie optymalizacyjne ze skanera (PHV) trafia do operatora Optymalizacji,
    a NIE do samego master daty (routing wg typu),
  • skrzynka renderuje się dla operatora i pokazuje tylko typy optymalizacyjne,
  • zmiana statusu działa i wymaga roli operatora.
"""
from django.test import TestCase
from django.urls import reverse


from ui.models import (PackagingIssue, Product,
                       PalletizationInstruction, CartonAlternative)
from ui.roles import GROUP_OPTIMIZER, GROUP_VIEWER

from .test_carton_opt import _user

class CartonOptIssueWorkspace(TestCase):
    def setUp(self):
        self.op = _user("op", GROUP_OPTIMIZER)
        self.p = Product.objects.create(code="WS1", name="Materiał warsztatu")
        from ui.views.core.packing import _recalculate_instruction
        instr = PalletizationInstruction.objects.create(
            product=self.p, version=1, is_active=True, unit_weight=1.0, pcs_per_carton=10,
            carton_l=20, carton_w=20, carton_h=120, pallet_length_cm=120, pallet_width_cm=80,
            max_height_total_cm=215, pallet_base_height_cm=15, max_weight_kg=1000)
        _recalculate_instruction(instr)              # policz layouty → 3D palety dostępne
        self.issue = PackagingIssue.objects.create(
            ref_code="WS1", issue_type="carton_underfilled", product=self.p)
        self.alt = CartonAlternative.objects.create(product=self.p, label="Lepszy",
                                                    length_cm=40, width_cm=40, height_cm=30)

    def test_claim_assigns_and_sets_in_review(self):
        self.client.force_login(self.op)
        resp = self.client.post(reverse("ui:carton_opt_claim_issue", args=[self.issue.pk]))
        self.assertIn(resp.status_code, (301, 302))
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.assigned_to, self.op)
        self.assertIsNotNone(self.issue.assigned_at)
        self.assertEqual(self.issue.status, "in_review")

    def test_variants_with_issue_shows_context(self):
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_variants")
                               + f"?product=WS1&issue={self.issue.pk}")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.context["issue"])
        self.assertIsNotNone(resp.context["pallet_three"])   # 3D palety zbudowane

    def test_promote_with_issue_resolves_and_links(self):
        from ui.models import CartonPromotion
        self.client.force_login(self.op)
        self.client.post(reverse("ui:carton_opt_variant_promote", args=[self.alt.pk]),
                         {"issue": self.issue.pk})
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, "resolved")
        self.assertIsNotNone(self.issue.resolved_at)
        pr = CartonPromotion.objects.get(product=self.p)
        self.assertEqual(pr.issue, self.issue)

    def test_claim_non_optimizer_blocked(self):
        self.client.force_login(_user("v5", GROUP_VIEWER))
        resp = self.client.post(reverse("ui:carton_opt_claim_issue", args=[self.issue.pk]))
        self.assertEqual(resp.status_code, 403)


class CartonOptKpi(TestCase):
    def setUp(self):
        self.op = _user("op", GROUP_OPTIMIZER)
        self.p = Product.objects.create(code="KPI1", name="Materiał KPI")
        # Słaby baseline (1 warstwa) — wariant o tej samej objętości mieści więcej/paletę.
        PalletizationInstruction.objects.create(
            product=self.p, version=1, is_active=True, unit_weight=1.0, pcs_per_carton=10,
            carton_l=20, carton_w=20, carton_h=120, pallet_length_cm=120, pallet_width_cm=80,
            max_height_total_cm=215, pallet_base_height_cm=15, max_weight_kg=1000,
            demand_pcs=100000)

    def test_variant_shows_pallet_and_truck_savings(self):
        # Wariant płaski 40×40×30: więcej kartonów/paletę → mniej palet/rok niż baseline.
        alt = CartonAlternative.objects.create(product=self.p, label="Płaski",
                                               length_cm=40, width_cm=40, height_cm=30)
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_variants") + "?product=KPI1&annual=100000")
        row = next(r for r in resp.context["rows"] if r["alt"].pk == alt.pk)
        self.assertIsNotNone(row["save_pallets"])
        self.assertGreater(row["save_pallets"], 0)          # realna oszczędność palet
        self.assertGreaterEqual(row["save_trucks"], 0)

    def test_annual_defaults_to_demand_pcs(self):
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_variants") + "?product=KPI1")
        self.assertEqual(resp.context["annual"], 100000)    # z instr.demand_pcs

    def test_no_annual_no_savings(self):
        self.p2 = Product.objects.create(code="KPI0", name="Bez wolumenu")
        PalletizationInstruction.objects.create(
            product=self.p2, version=1, is_active=True, unit_weight=1.0, pcs_per_carton=10,
            carton_l=20, carton_w=20, carton_h=120, pallet_length_cm=120, pallet_width_cm=80,
            max_height_total_cm=215, pallet_base_height_cm=15, max_weight_kg=1000, demand_pcs=0)
        CartonAlternative.objects.create(product=self.p2, label="X",
                                         length_cm=40, width_cm=40, height_cm=30)
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_variants") + "?product=KPI0&annual=0")
        self.assertEqual(resp.context["annual"], 0)
        for r in resp.context["rows"]:
            self.assertIsNone(r["save_pallets"])


class CartonOptWeight(TestCase):
    def test_zero_unit_weight_still_computes(self):
        # Materiał bez wagi sztuki NIE może wywalać fill/3D (validate rzuca na 0 — guard 0.001).
        from ui.views.carton_opt import _variant_fill, _engine_pallet
        p = Product.objects.create(code="W0", name="Bez wagi")
        instr = PalletizationInstruction.objects.create(
            product=p, version=1, is_active=True, unit_weight=0, pcs_per_carton=10,
            carton_l=40, carton_w=30, carton_h=25, pallet_length_cm=120, pallet_width_cm=80,
            max_height_total_cm=215, pallet_base_height_cm=15, max_weight_kg=1000)
        vf = _variant_fill(40, 30, 25, instr)
        self.assertTrue(vf["fits"]); self.assertIsNotNone(vf["fill"])
        ep = _engine_pallet(instr, 40, 30, 25)
        self.assertEqual(ep["error"], ""); self.assertIsNotNone(ep["spec"]["per_pallet"])

    def test_layers_by_weight_limit_and_unlimited(self):
        from ui.views.core.packing import _layers_by_weight
        self.assertEqual(_layers_by_weight(1, 500, 1000, 99), 2)   # 500kg/warstwę, max 1000 → 2
        self.assertEqual(_layers_by_weight(5, 0, 1000, 42), 42)    # brak wagi → unlimited
        self.assertEqual(_layers_by_weight(0, 10, 1000, 42), 42)   # brak warstwy → unlimited


class CartonOptPalletFit(TestCase):
    def setUp(self):
        self.op = _user("op", GROUP_OPTIMIZER)
        self.p = Product.objects.create(code="FIT1", name="Materiał fit")
        PalletizationInstruction.objects.create(
            product=self.p, version=1, is_active=True, unit_weight=1.0, pcs_per_carton=10,
            carton_l=40, carton_w=30, carton_h=25, pallet_length_cm=120, pallet_width_cm=80,
            max_height_total_cm=215, pallet_base_height_cm=15, max_weight_kg=1000)

    def _rd(self, scope, b=None):
        from ui.models import PackagingRedesign
        rd = PackagingRedesign.create_for(self.p, scope=scope, user=self.op)
        if b:
            rd.b_carton_l, rd.b_carton_w, rd.b_carton_h = b
            rd.save()
        return rd

    def test_detail_has_ab_pallets_with_engineering_spec(self):
        rd = self._rd("karton", b=(40, 40, 15))
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_redesign_detail", args=[rd.pk]))
        self.assertEqual(resp.status_code, 200)
        pa, pb = resp.context["pallet_a"], resp.context["pallet_b"]
        self.assertIsNotNone(pa["three"]); self.assertIsNotNone(pb["three"])
        for s in (pa["spec"], pb["spec"]):
            for key in ("per_layer", "layers", "per_pallet", "stack_h", "fill", "floor",
                        "cog_z", "overhang", "slender", "stability"):
                self.assertIn(key, s)
            self.assertIn(s["stability"], ("stabilny", "uwaga", "ryzyko"))
        # B (płaski 40×40×15) mieści więcej kartonów/paletę niż A (40×30×25).
        self.assertGreater(pb["spec"]["per_pallet"], pa["spec"]["per_pallet"])

    def test_scope_op_makes_b_equal_a(self):
        rd = self._rd("op")
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_redesign_detail", args=[rd.pk]))
        pa, pb = resp.context["pallet_a"], resp.context["pallet_b"]
        self.assertEqual(pa["spec"]["per_pallet"], pb["spec"]["per_pallet"])

    def test_engine_pallet_returns_cog_and_stability(self):
        from ui.views.carton_opt import _engine_pallet
        r = _engine_pallet(self.p.latest_instruction(), 40, 30, 25)
        self.assertEqual(r["error"], "")
        self.assertIsNotNone(r["spec"]["cog_z"])
        self.assertIn(r["spec"]["stability"], ("stabilny", "uwaga", "ryzyko"))

    def test_engine_pallet_no_base(self):
        from ui.views.carton_opt import _engine_pallet
        self.assertIsNotNone(_engine_pallet(None, 40, 30, 25)["error"])

    def test_orientation_options_three_variants_best_marked(self):
        from ui.views.carton_opt import _orientation_options
        opts = _orientation_options(self.p.latest_instruction(), 40, 30, 25)
        self.assertEqual(len(opts), 3)                       # płasko / na bok / na sztorc
        heights = {o["h"] for o in opts}
        self.assertEqual(heights, {25, 30, 40})              # każda inna wysokość pionowa
        self.assertEqual(sum(1 for o in opts if o["best"]), 1)  # dokładnie jedna najlepsza

    def test_detail_context_has_orientations(self):
        rd = self._rd("karton", b=(40, 30, 25))
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_redesign_detail", args=[rd.pk]))
        self.assertTrue(resp.context["orientations"])

    def test_metrics_endpoint_returns_live_pallet_b(self):
        rd = self._rd("karton", b=(40, 30, 25))
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_redesign_metrics"),
                               {"pk": rd.pk, "b_carton_l": 40, "b_carton_w": 40, "b_carton_h": 15})
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertIsNotNone(d["three_b"])                # 3D palety B do przerysowania
        self.assertIn("Stabilność", d["spec_html_b"])     # spec wyrenderowany z partiala
        self.assertIn("b", d)                              # metryki nadal obecne


class CartonOptSuggestToRedesign(TestCase):
    def setUp(self):
        self.op = _user("op", GROUP_OPTIMIZER)
        self.p = Product.objects.create(code="AB1", name="Materiał A/B")
        PalletizationInstruction.objects.create(
            product=self.p, version=1, is_active=True, unit_weight=1.0, pcs_per_carton=10,
            carton_l=40, carton_w=30, carton_h=25, pallet_length_cm=120, pallet_width_cm=80,
            max_height_total_cm=215, pallet_base_height_cm=15, max_weight_kg=1000)

    def test_creates_redesign_with_b_carton_from_suggestion(self):
        from ui.models import PackagingRedesign
        self.client.force_login(self.op)
        resp = self.client.post(reverse("ui:carton_opt_suggest_to_redesign"), {
            "product_id": self.p.pk, "length_cm": "40", "width_cm": "40",
            "height_cm": "15", "fill": "88"})
        self.assertIn(resp.status_code, (301, 302))
        rd = PackagingRedesign.objects.get(product=self.p)
        self.assertEqual(rd.scope, "karton")
        self.assertEqual(rd.status, "draft")
        self.assertEqual((rd.b_carton_l, rd.b_carton_w, rd.b_carton_h), (40, 40, 15))
        self.assertIn("88", rd.notes)
        self.assertIsNotNone(rd.a_snapshot)                  # wersja A zamrożona

    def test_missing_dims_creates_nothing(self):
        from ui.models import PackagingRedesign
        self.client.force_login(self.op)
        self.client.post(reverse("ui:carton_opt_suggest_to_redesign"), {
            "product_id": self.p.pk, "length_cm": "0", "width_cm": "40", "height_cm": "15"})
        self.assertFalse(PackagingRedesign.objects.filter(product=self.p).exists())

    def test_non_optimizer_blocked(self):
        from ui.models import PackagingRedesign
        self.client.force_login(_user("v4", GROUP_VIEWER))
        resp = self.client.post(reverse("ui:carton_opt_suggest_to_redesign"), {
            "product_id": self.p.pk, "length_cm": "40", "width_cm": "40", "height_cm": "15"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(PackagingRedesign.objects.filter(product=self.p).exists())


class CartonOptPromote(TestCase):
    def setUp(self):
        self.op = _user("op", GROUP_OPTIMIZER)
        self.p = Product.objects.create(code="PROMO", name="Materiał do promocji")
        PalletizationInstruction.objects.create(
            product=self.p, version=1, is_active=True, unit_weight=1.0, pcs_per_carton=10,
            carton_l=40, carton_w=30, carton_h=25, pallet_length_cm=120, pallet_width_cm=80,
            max_height_total_cm=215, pallet_base_height_cm=15, max_weight_kg=1000)
        self.alt = CartonAlternative.objects.create(product=self.p, label="Płaski",
                                                    length_cm=40, width_cm=30, height_cm=10)

    def test_promote_creates_new_version(self):
        self.client.force_login(self.op)
        resp = self.client.post(reverse("ui:carton_opt_variant_promote", args=[self.alt.pk]))
        self.assertIn(resp.status_code, (301, 302))
        instrs = PalletizationInstruction.objects.filter(product=self.p).order_by("version")
        self.assertEqual(instrs.count(), 2)                     # stara nietknięta + nowa
        new = instrs.last()
        self.assertEqual(new.version, 2)
        self.assertEqual((new.carton_l, new.carton_w, new.carton_h), (40, 30, 10))  # wymiary wariantu
        self.assertTrue(new.layouts)                            # przeliczone przez silnik
        # Stara wersja bez zmian.
        self.assertEqual(instrs.first().carton_h, 25)
        # latest_instruction zwraca nową wersję (staje się live).
        self.assertEqual(self.p.latest_instruction().pk, new.pk)

    def test_promote_unfittable_variant_creates_nothing(self):
        bad = CartonAlternative.objects.create(product=self.p, label="Za duży",
                                               length_cm=999, width_cm=999, height_cm=999)
        self.client.force_login(self.op)
        self.client.post(reverse("ui:carton_opt_variant_promote", args=[bad.pk]))
        self.assertEqual(PalletizationInstruction.objects.filter(product=self.p).count(), 1)

    def test_non_optimizer_blocked(self):
        viewer = _user("v3", GROUP_VIEWER)
        self.client.force_login(viewer)
        resp = self.client.post(reverse("ui:carton_opt_variant_promote", args=[self.alt.pk]))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(PalletizationInstruction.objects.filter(product=self.p).count(), 1)
