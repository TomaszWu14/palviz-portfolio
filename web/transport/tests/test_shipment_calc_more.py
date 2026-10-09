"""Behaviour tests for two previously-untested areas:
  - shipment creation with manual line rows (incl. the regression for the zip()
    truncation fix — mismatched parallel lists must not drop rows),
  - the manual palletization calculator endpoint returning a rendered panel."""
from django.test import TestCase
from django.urls import reverse

from ui.models import (
    Product, Shipment, ShipmentLine, PalletizationInstruction, WarehouseLocationType,
)
from ui.views.core.helpers import _calc_shipment_data

from .test_shipment_calc import _user_all_roles

class PalletCountCalibrationTests(TestCase):
    """Regression for delivery 81772168: the loose 3D draw must NOT drive the billed
    pallet count, and the efficiency slider must actually move the headline."""

    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        # A mixed load whose VOLUME fits in 2 pallets at high efficiency, but whose loose
        # shelf-packing (no true interlock) spreads into 3 — the exact inflation reported.
        cls.sh = Shipment.objects.create(name="Dostawa 81772168", stowage_efficiency_pct=85)
        specs = [("A", 40, 30, 25, 20), ("B", 60, 40, 20, 14), ("C", 35, 35, 30, 18),
                 ("D", 50, 30, 15, 16), ("E", 30, 20, 40, 22)]
        for code, l, w, h, qty in specs:
            p = Product.objects.create(code=code, name=code)
            PalletizationInstruction.objects.create(
                product=p, version=1, carton_l=l, carton_w=w, carton_h=h,
                unit_weight=0.4, pcs_per_carton=1, demand_pcs=100, is_active=True)
            ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=qty, unit="kar")

    def _headline(self, eff, h_cm=180):
        calc = _calc_shipment_data(self.sh, stow_eff=eff, heights=[h_cm])
        return calc["scenarios"][0]

    def test_loose_draw_does_not_inflate_count(self):
        # The headline equals the efficiency-calibrated estimate, not the looser 3D draw.
        sc = self._headline(95)
        self.assertEqual(sc["n_pallets"], sc["est_theoretical"])
        # The illustration may draw more pallets, but that no longer raises the quote.
        self.assertGreaterEqual(sc["model_pallets"], sc["n_pallets"])

    def test_efficiency_slider_moves_the_count(self):
        # Lower efficiency must mean at least as many pallets — and strictly more somewhere
        # across the range (the slider is no longer inert).
        counts = [self._headline(e)["n_pallets"] for e in (60, 75, 85, 95)]
        self.assertTrue(all(a >= b for a, b in zip(counts, counts[1:])))  # non-increasing ↓
        self.assertGreater(counts[0], counts[-1])                         # 60% > 95%

    def test_sap_hu_anchors_to_reality(self):
        # When SAP carried the real HU count, the headline is that number at ANY efficiency.
        self.sh.actual_hu_count = 2
        self.sh.save(update_fields=["actual_hu_count"])
        for eff in (60, 85, 95):
            sc = self._headline(eff)
            self.assertEqual(sc["n_pallets"], 2)
            self.assertEqual(sc["actual_hu"], 2)
        self.sh.actual_hu_count = None
        self.sh.save(update_fields=["actual_hu_count"])


class QuoteRecipientTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        p = Product.objects.create(code="Q1", name="Q1")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=1, demand_pcs=100, is_active=True)
        cls.sh = Shipment.objects.create(name="Dostawa 1", stowage_efficiency_pct=80)
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=10, unit="kar")

    def setUp(self):
        self.client.force_login(self.user)

    def test_two_default_heights_and_selection(self):
        resp = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]), {"h": 220})
        self.assertEqual(resp.status_code, 200)
        heights = [s["max_h_cm"] for s in resp.context["calc"]["scenarios"]]
        self.assertEqual(heights, [180, 220])                  # two editable defaults
        self.assertEqual(resp.context["selected_height"], 220)
        # invalid height → first scenario
        resp2 = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]), {"h": 999})
        self.assertEqual(resp2.context["selected_height"], 180)

    def test_heights_are_editable(self):
        resp = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]),
                               {"h1": "1.6", "h2": "2.4"})
        heights = [s["max_h_cm"] for s in resp.context["calc"]["scenarios"]]
        self.assertEqual(heights, [160, 240])

    def test_list_search_filter(self):
        Shipment.objects.create(name="PHARMO PL", recipient_name="PHARMO")
        Shipment.objects.create(name="INNY", recipient_name="Acme")
        resp = self.client.get(reverse("ui:planner_shipments"), {"q": "pharmo"})
        self.assertEqual(resp.status_code, 200)
        names = [s.name for s in resp.context["page_obj"]]
        self.assertIn("PHARMO PL", names)
        self.assertNotIn("INNY", names)

    def test_detail_links_to_quote_screen(self):
        resp = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("ui:planner_shipment_quote", args=[self.sh.pk]))


class CalculatorEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()

    def setUp(self):
        self.client.force_login(self.user)

    def _params(self, **extra):
        p = {
            "pallet": "EU", "sku": "TST", "variant": "STD",
            "carton_l": 40, "carton_w": 30, "carton_h": 25,
            "unit_weight": "0.5", "pcs_per_carton": 1, "demand_pcs": 1000,
            "carton_tare": "0", "max_height_total": 215, "max_weight": 1000,
            "render_layers": 3,
        }
        p.update(extra)
        return p

    def test_calculate_returns_panel_with_layouts(self):
        resp = self.client.post(reverse("ui:calculate"), self._params())
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertNotIn("Błędne dane formularza", body)
        self.assertIn("Wariant", body)               # variant selector present
        self.assertIn("three-pallet", body)          # realistic 3D pallet canvas

    def test_whatif_returns_panel(self):
        resp = self.client.get(reverse("ui:whatif"), self._params())
        self.assertEqual(resp.status_code, 200)
        self.assertIn("three-pallet", resp.content.decode())

    def test_location_name_xss_is_escaped_in_embedded_figure(self):
        """A location named with </script> must not break out of the <script> block
        that embeds its Plotly figure — `<` is escaped to \\u003c by _fig_json."""
        loc = WarehouseLocationType.objects.create(
            name="</script><x>", location_class="pallet_full", is_pallet_location=True,
            width_cm=90, depth_cm=130, total_height_cm=235, max_load_kg=1000)
        resp = self.client.post(reverse("ui:calculate"), self._params(fit_loc=[str(loc.pk)]))
        body = resp.content.decode()
        self.assertIn("\\u003c/script\\u003e\\u003cx\\u003e", body)   # escaped in the figure JSON
        self.assertNotIn("</script><x>", body)                       # raw breakout absent

    def test_ticked_locations_fit_inline(self):
        """Selected locations (checkboxes) produce an inline fit section — no navigation."""
        big = WarehouseLocationType.objects.create(
            name="DUZA", location_class="pallet_full", is_pallet_location=True,
            width_cm=90, depth_cm=130, total_height_cm=235, max_load_kg=1000)
        small = WarehouseLocationType.objects.create(
            name="MALA", location_class="shelf", is_pallet_location=False,
            width_cm=30, depth_cm=40, total_height_cm=50)
        resp = self.client.post(reverse("ui:calculate"),
                                self._params(fit_loc=[str(big.pk), str(small.pk)]))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Przymierzenie do lokalizacji", body)
        self.assertIn("DUZA", body)
        self.assertIn("Mieści się", body)      # pallet fits the big location
        self.assertIn("MALA", body)
        self.assertIn("locfig3d-src-0", body)  # inline 3D fit visualisation embedded
        self.assertIn("locfig2d-src-0", body)  # inline front-view embedded

    def test_variant_switch_keeps_location_fits(self):
        """Switching the pallet variant (pallet_panel) must keep the ticked location's
        fit visualisation — it is hx-included, not reset."""
        from ui.models import Palletization
        loc = WarehouseLocationType.objects.create(
            name="DUZA", location_class="pallet_full", is_pallet_location=True,
            width_cm=90, depth_cm=130, total_height_cm=235, max_load_kg=1000)
        self.client.post(reverse("ui:calculate"), self._params(fit_loc=[str(loc.pk)]))
        rec = Palletization.objects.latest("id")
        alt = rec.layouts[1]["name"] if len(rec.layouts) > 1 else rec.selected_layout
        resp = self.client.get(reverse("ui:pallet_panel", args=[rec.id]),
                               {"layout": alt, "fit_loc": str(loc.pk)})
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Przymierzenie do lokalizacji", body)
        self.assertIn("locfig3d-src-0", body)


class StowageEfficiencyTests(TestCase):
    """The stowage-efficiency factor must shrink usable pallet cube → more pallets
    for a volume-driven shipment (matters for honest transport quoting)."""
    @classmethod
    def setUpTestData(cls):
        cls.p = Product.objects.create(code="VOL", name="Duży lekki")
        PalletizationInstruction.objects.create(
            product=cls.p, version=1, carton_l=60, carton_w=40, carton_h=40,
            unit_weight=0.1, pcs_per_carton=1, carton_tare=0.0, is_active=True, layouts=[])
        cls.sh = Shipment.objects.create(name="EFF", stowage_efficiency_pct=80)
        ShipmentLine.objects.create(shipment=cls.sh, product=cls.p, quantity=100, unit="kar", order=0)

    def test_lower_efficiency_needs_more_pallets(self):
        # Lower stowage efficiency shrinks the usable pallet cube → more pallets (the
        # quotable count is the efficiency-calibrated estimate).
        hi = _calc_shipment_data(self.sh, stow_eff=100)["scenarios"][0]["n_pallets"]
        lo = _calc_shipment_data(self.sh, stow_eff=50)["scenarios"][0]["n_pallets"]
        self.assertGreater(lo, hi)

    def test_count_matches_drawn_layout(self):
        # Invariant: the headline pallet count equals the pallets actually drawn in 3D.
        for sc in _calc_shipment_data(self.sh)["scenarios"]:
            self.assertEqual(sc["n_pallets"], len(sc["three"]["pallets"]))

    def test_borderline_load_exposes_pallet_range(self):
        # A load that needs one fewer pallet at +5pp efficiency is borderline → range.
        sh = Shipment.objects.create(name="EDGE", stowage_efficiency_pct=80)
        ShipmentLine.objects.create(shipment=sh, product=self.p, quantity=88, unit="kar", order=0)
        sc = _calc_shipment_data(sh)["scenarios"][0]   # 1.8 m
        self.assertTrue(sc["n_range"])
        self.assertEqual(sc["n_pallets_low"], sc["n_pallets"] - 1)

    def test_filled_plus_slack_equals_total(self):
        # Precise breakdown: filled + slack pallets must equal the quoted total.
        sc = _calc_shipment_data(self.sh)["scenarios"][0]
        self.assertEqual(sc["n_filled"] + sc["n_slack"], sc["n_pallets"])
        self.assertGreaterEqual(sc["n_filled"], 1)

    def test_arbitrary_cm_height_scenarios(self):
        # Heights are now 1-cm precise (not snapped to 10 cm) — labels reflect it.
        scs = _calc_shipment_data(self.sh, heights=[196, 211])["scenarios"]
        self.assertEqual({s["max_h_cm"] for s in scs}, {196, 211})
        self.assertIn("1,96 m", {s["label"] for s in scs})

    def test_default_uses_shipment_value(self):
        self.assertEqual(_calc_shipment_data(self.sh)["stow_eff_pct"], 80)

    def test_efficiency_clamped_to_range(self):
        self.assertEqual(_calc_shipment_data(self.sh, stow_eff=5)["stow_eff_pct"], 30)
        self.assertEqual(_calc_shipment_data(self.sh, stow_eff=999)["stow_eff_pct"], 100)


class WarehouseEmailRequirementsTests(TestCase):
    def test_wh_mailto_includes_client_requirements(self):
        import urllib.parse
        from transport.views.shipments import _wh_mailto
        sh = Shipment.objects.create(name="WH", status="draft",
                                     client_requirements="Paleta EURO, fumigacja")
        readiness = type("R", (), {"kind": "date", "pickup_date": None})()
        body = urllib.parse.unquote(_wh_mailto(sh, readiness, "http://x/confirm"))
        self.assertIn("WYMAGANIA KLIENTA", body)
        self.assertIn("Paleta EURO", body)

    def test_wh_mailto_omits_when_no_requirements(self):
        import urllib.parse
        from transport.views.shipments import _wh_mailto
        sh = Shipment.objects.create(name="WH2", status="draft")
        readiness = type("R", (), {"kind": "date", "pickup_date": None})()
        self.assertNotIn("WYMAGANIA", urllib.parse.unquote(_wh_mailto(sh, readiness, "http://x")))


class ContainerViewTests(TestCase):
    def setUp(self):
        self.client.force_login(_user_all_roles())
        p = Product.objects.create(code="C1", name="C")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=30,
            unit_weight=1, pcs_per_carton=1, carton_tare=0, is_active=True, layouts=[])
        self.sh = Shipment.objects.create(name="CT", status="draft")
        ShipmentLine.objects.create(shipment=self.sh, product=p, quantity=100, unit="kar", order=0)

    def test_loose_and_pallets_render(self):
        for mode in ("loose", "pallets"):
            r = self.client.get(reverse("ui:planner_shipment_container", args=[self.sh.pk]),
                                 {"mode": mode, "vehicle": "naczepa"})
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "Naczepa")          # vehicle-type label rendered

    def test_loose_view_shows_fill_efficiency_hud(self):
        r = self.client.get(reverse("ui:planner_shipment_container", args=[self.sh.pk]),
                             {"mode": "loose", "vehicle": "naczepa"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Efektywność wypełnienia")     # always-on HUD on the canvas
        self.assertContains(r, "kubatury")                    # volumetric fill caption
        self.assertIsNotNone(r.context["data"]["weight_pct"])  # weight utilisation computed


class VolumeNoRoundupTests(TestCase):
    """Volume is the exact carton-equivalent, not the rounded-up box count: goods ordered
    in sub-carton units (OP) whose volume is carton_vol/ppc must not inflate the total."""
    def test_szt_volume_uses_fractional_cartons(self):
        from ui.views.core.helpers import _calc_shipment_data
        p = Product.objects.create(code="V1", name="V")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=100, carton_w=100, carton_h=100,   # 1 m³ carton
            unit_weight=1, pcs_per_carton=10, carton_tare=0, is_active=True, layouts=[])
        sh = Shipment.objects.create(name="VOL", status="draft")
        ShipmentLine.objects.create(shipment=sh, product=p, quantity=45, unit="szt", order=0)
        calc = _calc_shipment_data(sh, with_packing=False)
        # 45 pcs ÷ 10/carton = 4.5 cartons → 4.5 m³ (carton = 1 m³), NOT 5 m³ (rounded up).
        self.assertAlmostEqual(calc["total_vol_m3"], 4.5, places=3)
        lc = calc["lines"][0]
        self.assertAlmostEqual(lc["volume_m3"], 4.5, places=3)
        self.assertEqual(lc["n_cartons"], 5)          # physical boxes still whole (5)

    def test_marm_unit_volume_overrides_carton_dims(self):
        from ui.views.core.helpers import _calc_shipment_data
        p = Product.objects.create(code="V2", name="V2")
        # Carton L×W×H = 0.08647 m³/carton, but the SAP MARM per-OP volume is 0.00283
        # (×25 OP = 0.0707) — the box carries void, so the dims overstate the goods.
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=57, carton_w=37, carton_h=41,
            unit_weight=0.1, pcs_per_carton=25, carton_tare=0, is_active=True, layouts=[],
            unit_volume_m3=0.00283)
        sh = Shipment.objects.create(name="MARMVOL", status="draft")
        ShipmentLine.objects.create(shipment=sh, product=p, quantity=2200, unit="szt", order=0)
        calc = _calc_shipment_data(sh, with_packing=False)
        # 2200 OP × 0.00283 = 6.226 m³ (MARM), NOT 88 cartons × 0.08647 = 7.61 m³ (dims).
        self.assertAlmostEqual(calc["total_vol_m3"], 6.226, places=2)
