"""Moduł „Optymalizacja kartonów" (Faza 1): skrzynka zgłoszeń + routing powiadomień.

Sprawdza, że:
  • zgłoszenie optymalizacyjne ze skanera (PHV) trafia do operatora Optymalizacji,
    a NIE do samego master daty (routing wg typu),
  • skrzynka renderuje się dla operatora i pokazuje tylko typy optymalizacyjne,
  • zmiana statusu działa i wymaga roli operatora.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ui.views.carton_opt import _vol_fill

from ui.models import (PackagingIssue, Notification, Product,
                       PalletizationInstruction, OptimizationConfig, CartonAlternative)
from ui.roles import GROUP_OPTIMIZER, GROUP_WAREHOUSE, GROUP_VIEWER, GROUP_ADMIN
from ui.notifications import optimizer_users


def _user(name, group):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=group)[0])
    return u


class CartonOptRouting(TestCase):
    def test_optimizer_user_in_recipient_set(self):
        op = _user("op", GROUP_OPTIMIZER)
        self.assertIn(op, optimizer_users())

    def test_scanner_optimization_report_notifies_optimizer(self):
        op = _user("op", GROUP_OPTIMIZER)
        reporter = _user("mag", GROUP_WAREHOUSE)
        self.client.force_login(reporter)
        resp = self.client.post(reverse("ui:phv_report"), {
            "ref_code": "TEST123", "issue_type": "carton_underfilled",
            "description": "Za dużo powietrza w kartonie",
        })
        self.assertin_redirect(resp)
        issue = PackagingIssue.objects.get(ref_code="TEST123")
        self.assertEqual(issue.issue_type, "carton_underfilled")
        # Powiadomienie poszło do operatora optymalizacji, z linkiem do skrzynki.
        notif = Notification.objects.filter(recipient=op).first()
        self.assertIsNotNone(notif)
        self.assertIn("/optymalizacja/", notif.url)

    def assertin_redirect(self, resp):
        self.assertIn(resp.status_code, (301, 302))


class CartonOptInbox(TestCase):
    def setUp(self):
        self.op = _user("op", GROUP_OPTIMIZER)
        # Jedno zgłoszenie optymalizacyjne + jedno „zwykłe" (nie powinno się pokazać).
        self.opt = PackagingIssue.objects.create(ref_code="A1", issue_type="carton_fit_pallet")
        PackagingIssue.objects.create(ref_code="B2", issue_type="missing_conversion")

    def test_inbox_shows_only_optimization_issues(self):
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_inbox") + "?status=all")
        self.assertEqual(resp.status_code, 200)
        # Sprawdzamy LISTĘ zgłoszeń, nie surowy HTML: „B2" to dwa znaki, a strona zawiera
        # 64-znakowy losowy token CSRF — assertNotContains trafiał w niego mniej więcej
        # raz na sto przebiegów i wywracał CI bez związku ze zmianą (zdarzyło się na #370:
        # value="…UmTJWgPKlB2rQEXQ1…").
        refs = {i.ref_code for i in resp.context["issues"]}
        self.assertIn("A1", refs)
        self.assertNotIn("B2", refs)

    def test_status_counts_single_query(self):
        """Liczniki statusów: poprawne wartości (w tym zera) i jedna agregacja zamiast N zapytań."""
        PackagingIssue.objects.create(ref_code="A2", issue_type="carton_fit_pallet",
                                      status="resolved")
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_inbox"))
        counts = resp.context["counts"]
        self.assertEqual(counts["open"], 1)
        self.assertEqual(counts["resolved"], 1)
        self.assertEqual(counts["in_review"], 0)   # status bez wystąpień nadal ma 0
        # Wszystkie statusy jednym zapytaniem (agregacja), nie .count() per status.
        from ui.views.carton_opt import _optimization_issues
        from django.db.models import Count
        with self.assertNumQueries(1):
            dict(_optimization_issues().values("status").annotate(n=Count("id"))
                 .values_list("status", "n"))

    def test_status_change(self):
        self.client.force_login(self.op)
        resp = self.client.post(
            reverse("ui:carton_opt_set_status", args=[self.opt.pk]),
            {"status": "resolved", "resolver_notes": "Zmieniono karton na mniejszy"})
        self.assertIn(resp.status_code, (301, 302))
        self.opt.refresh_from_db()
        self.assertEqual(self.opt.status, "resolved")
        self.assertIsNotNone(self.opt.resolved_at)

    def test_non_optimizer_blocked_from_status_change(self):
        viewer = _user("v", GROUP_VIEWER)
        self.client.force_login(viewer)
        resp = self.client.post(
            reverse("ui:carton_opt_set_status", args=[self.opt.pk]), {"status": "resolved"})
        self.assertEqual(resp.status_code, 403)
        self.opt.refresh_from_db()
        self.assertEqual(self.opt.status, "open")


class VolFillTests(SimpleTestCase):
    def test_underfilled_pallet_low_fill(self):
        # Paleta 120×80, dostępne 200 cm. Karton 40×40×50=80000. 2 warstwy = 12 szt (per_layer 6).
        # Objętość: 12*80000=960000; dostępna: 9600*200=1920000 → 50%.
        self.assertEqual(_vol_fill(12, 120, 80, 200, 40, 40, 50), 50)

    def test_ignores_used_height_only_available(self):
        # Ta sama paleta ułożona tylko 1 warstwą (6 szt) → 25%, mimo że „cube_used_pct"
        # (gęstość w zajętej wysokości) byłby ~100%. Dashboard MUSI to wykryć.
        self.assertEqual(_vol_fill(6, 120, 80, 200, 40, 40, 50), 25)

    def test_missing_data_none(self):
        self.assertIsNone(_vol_fill(0, 120, 80, 200, 40, 40, 50))
        self.assertIsNone(_vol_fill(10, 0, 80, 200, 40, 40, 50))


def _instr(code, fill_pct):
    """Produkt + aktywna instrukcja o zadanym wypełnieniu OBJĘTOŚCI. cube_used_pct celowo
    ustawione na 99 — nowy _fill_pct MUSI je ignorować i liczyć z geometrii (regresja)."""
    p = Product.objects.create(code=code, name=f"Materiał {code}")
    # _vol_fill: fill = 100*cpp*carton_vol/(pallet_area*usable_h).
    # karton 30×20×20=12000, paleta 120×80=9600, usable_h=200 → fill = 0.625*cpp.
    cpp = round(fill_pct / 0.625)
    PalletizationInstruction.objects.create(
        product=p, version=1, is_active=True, unit_weight=1.0, pcs_per_carton=10,
        carton_l=30, carton_w=20, carton_h=20,
        pallet_length_cm=120, pallet_width_cm=80, max_height_total_cm=215, pallet_base_height_cm=15,
        layouts=[{"name": "L1", "cube_used_pct": 99, "cartons_per_pallet": cpp}],
        selected_layout="L1")
    return p


class PalletsPerTruckConfigTests(TestCase):
    """Pojemność auta (palet/auto) z OptimizationConfig zamiast hardkodu 33."""

    def test_year_kpi_uses_configured_capacity(self):
        from ui.views.carton_opt import _year_kpi
        # Domyślnie 33: 100 palet → ceil(100/33) = 4 auta.
        self.assertEqual(_year_kpi(10, 10, 10_000)["trucks"], 4)
        cfg = OptimizationConfig.load()
        cfg.pallets_per_truck = 20
        cfg.save()
        # Po zmianie: ceil(100/20) = 5 aut.
        self.assertEqual(_year_kpi(10, 10, 10_000)["trucks"], 5)

    def test_admin_can_change_capacity_clamped(self):
        admin = _user("adm-ppt", GROUP_ADMIN)
        self.client.force_login(admin)
        self.client.post(reverse("ui:carton_opt_set_config"), {"pallets_per_truck": "999"})
        self.assertEqual(OptimizationConfig.load().pallets_per_truck, 66)   # clamp 1–66


class CartonOptDashboard(TestCase):
    def setUp(self):
        self.op = _user("op", GROUP_OPTIMIZER)
        _instr("LOW", 55)      # poniżej progu 70 → widoczny
        _instr("MID", 68)      # poniżej progu → widoczny
        _instr("GOOD", 92)     # powyżej progu → ukryty

    def test_below_threshold_shown_above_hidden(self):
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_dashboard"))
        self.assertEqual(resp.status_code, 200)
        codes = [r["product"].code for r in resp.context["rows"]]
        self.assertIn("LOW", codes)
        self.assertIn("MID", codes)
        self.assertNotIn("GOOD", codes)
        # Sort: gorsze wypełnienie wyżej (krytyczny LOW przed MID).
        self.assertEqual(codes[0], "LOW")

    def test_threshold_change_moves_boundary(self):
        admin = _user("adm", GROUP_ADMIN)
        self.client.force_login(admin)
        # Obniż próg do 60 → MID (68%) znika, LOW (55%) zostaje.
        self.client.post(reverse("ui:carton_opt_set_config"), {"min_fill_pct": "60"})
        self.assertEqual(OptimizationConfig.load().min_fill_pct, 60)
        resp = self.client.get(reverse("ui:carton_opt_dashboard"))
        codes = [r["product"].code for r in resp.context["rows"]]
        self.assertIn("LOW", codes)
        self.assertNotIn("MID", codes)

    def test_non_admin_cannot_change_threshold(self):
        self.client.force_login(self.op)   # operator, nie admin
        resp = self.client.post(reverse("ui:carton_opt_set_config"), {"min_fill_pct": "30"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(OptimizationConfig.load().min_fill_pct, 70)


class CartonOptVariants(TestCase):
    def setUp(self):
        self.op = _user("op", GROUP_OPTIMIZER)
        self.p = Product.objects.create(code="VAR1", name="Materiał wariantowy")
        # Instrukcja bazowa: karton 40×30×25, paleta EU 120×80, wys. 215 → silnik policzy fill.
        PalletizationInstruction.objects.create(
            product=self.p, version=1, is_active=True, unit_weight=1.0, pcs_per_carton=10,
            carton_l=40, carton_w=30, carton_h=25, pallet_length_cm=120, pallet_width_cm=80,
            max_height_total_cm=215, pallet_base_height_cm=15, max_weight_kg=1000)

    def test_add_variant_and_compute_fill(self):
        self.client.force_login(self.op)
        self.client.post(reverse("ui:carton_opt_variant_save"), {
            "product_id": self.p.pk, "label": "Wariant A",
            "length_cm": "40", "width_cm": "40", "height_cm": "20"})
        alt = CartonAlternative.objects.get(product=self.p, label="Wariant A")
        self.assertTrue(alt.is_active)
        resp = self.client.get(reverse("ui:carton_opt_variants") + "?product=VAR1")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.context["baseline"])       # instrukcja bazowa policzona
        row = next(r for r in resp.context["rows"] if r["alt"].pk == alt.pk)
        self.assertIsNotNone(row["fill"])                    # silnik policzył wypełnienie wariantu
        self.assertTrue(row["fits"])

    def test_edit_records_history(self):
        alt = CartonAlternative.objects.create(product=self.p, label="B",
                                               length_cm=40, width_cm=30, height_cm=25)
        self.client.force_login(self.op)
        self.client.post(reverse("ui:carton_opt_variant_save"), {
            "product_id": self.p.pk, "alt_id": alt.pk, "label": "B",
            "length_cm": "50", "width_cm": "30", "height_cm": "25"})
        alt.refresh_from_db()
        self.assertEqual(alt.length_cm, 50)
        self.assertGreaterEqual(alt.history.count(), 2)      # historia zapamiętana

    def test_delete_soft(self):
        alt = CartonAlternative.objects.create(product=self.p, label="C",
                                               length_cm=40, width_cm=30, height_cm=25)
        self.client.force_login(self.op)
        self.client.post(reverse("ui:carton_opt_variant_delete", args=[alt.pk]))
        alt.refresh_from_db()
        self.assertFalse(alt.is_active)

    def test_non_optimizer_blocked(self):
        viewer = _user("v2", GROUP_VIEWER)
        self.client.force_login(viewer)
        resp = self.client.post(reverse("ui:carton_opt_variant_save"), {
            "product_id": self.p.pk, "label": "X", "length_cm": "40",
            "width_cm": "30", "height_cm": "25"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(CartonAlternative.objects.filter(label="X").exists())

    def test_variants_view_computes_delta_vs_baseline(self):
        # Delta = wypełnienie wariantu − wypełnienie obecnego (steruje badge „lepszy").
        alt = CartonAlternative.objects.create(product=self.p, label="Inny",
                                               length_cm=40, width_cm=40, height_cm=20)
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_variants") + "?product=VAR1")
        row = next(r for r in resp.context["rows"] if r["alt"].pk == alt.pk)
        self.assertIsNotNone(row["delta_pp"])
        self.assertEqual(row["delta_pp"], row["fill"] - resp.context["baseline"]["fill"])


class CartonOptPromotionHistory(TestCase):
    def setUp(self):
        self.op = _user("op", GROUP_OPTIMIZER)
        self.p = Product.objects.create(code="HIST1", name="Materiał historii")
        PalletizationInstruction.objects.create(
            product=self.p, version=1, is_active=True, unit_weight=1.0, pcs_per_carton=10,
            carton_l=20, carton_w=20, carton_h=120, pallet_length_cm=120, pallet_width_cm=80,
            max_height_total_cm=215, pallet_base_height_cm=15, max_weight_kg=1000)
        self.alt = CartonAlternative.objects.create(product=self.p, label="Lepszy",
                                                    length_cm=40, width_cm=40, height_cm=30)

    def test_promote_records_promotion(self):
        from ui.models import CartonPromotion
        self.client.force_login(self.op)
        self.client.post(reverse("ui:carton_opt_variant_promote", args=[self.alt.pk]))
        pr = CartonPromotion.objects.get(product=self.p)
        self.assertEqual(pr.dims, "40×40×30")
        self.assertEqual(pr.source_label, "Lepszy")
        self.assertEqual(pr.version, 2)
        self.assertEqual(pr.user, self.op)
        self.assertIsNotNone(pr.fill_after)
        # fill_after (płaski karton, wiele warstw) > fill_before (baseline 1 warstwa)
        self.assertGreater(pr.fill_after, pr.fill_before)

    def test_history_section_in_context(self):
        from ui.models import CartonPromotion
        CartonPromotion.objects.create(product=self.p, version=2, dims="40×40×30",
                                       source_label="Lepszy", fill_before=25, fill_after=88,
                                       user=self.op)
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_variants") + "?product=HIST1")
        self.assertEqual(len(resp.context["promotions"]), 1)


class CartonOptSuggest(TestCase):
    def setUp(self):
        self.op = _user("op", GROUP_OPTIMIZER)
        self.p = Product.objects.create(code="SUG1", name="Materiał do sugestii")
        # Baseline celowo słaby: karton 20×20×120 (V0=48000) wypełnia paletę fatalnie
        # (1 warstwa z 200 cm) — inne kształty o tej objętości muszą go pobić.
        PalletizationInstruction.objects.create(
            product=self.p, version=1, is_active=True, unit_weight=1.0, pcs_per_carton=10,
            carton_l=20, carton_w=20, carton_h=120, pallet_length_cm=120, pallet_width_cm=80,
            max_height_total_cm=215, pallet_base_height_cm=15, max_weight_kg=1000)

    def test_suggestions_within_band_and_better(self):
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_variants") + "?product=SUG1&suggest=1")
        self.assertEqual(resp.status_code, 200)
        sugg = resp.context["dim_suggestions"]
        self.assertTrue(sugg)                                 # słaby baseline → są lepsze
        self.assertLessEqual(len(sugg), 5)                    # top-K
        base = resp.context["baseline"]
        v0 = 20 * 20 * 120
        vmin, vmax = v0 * 0.8, v0 * 1.2                       # domyślne pasmo ±20%
        for s in sugg:
            self.assertTrue(vmin <= s["l"] * s["w"] * s["h"] <= vmax)   # objętość w paśmie
            self.assertGreater(s["fill"], base["fill"])       # lepsze wypełnienie
            self.assertEqual(s["delta_pp"], s["fill"] - base["fill"])
        # posortowane malejąco po wypełnieniu
        self.assertEqual([s["fill"] for s in sugg], sorted((s["fill"] for s in sugg), reverse=True))

    def test_no_suggest_param_computes_nothing(self):
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_variants") + "?product=SUG1")
        self.assertEqual(resp.context["dim_suggestions"], [])

    def test_band_from_config_respected(self):
        OptimizationConfig.objects.update_or_create(defaults={"suggest_vol_down_pct": 5,
                                                              "suggest_vol_up_pct": 5})
        self.client.force_login(self.op)
        resp = self.client.get(reverse("ui:carton_opt_variants") + "?product=SUG1&suggest=1")
        v0 = 20 * 20 * 120
        for s in resp.context["dim_suggestions"]:
            self.assertTrue(v0 * 0.95 <= s["l"] * s["w"] * s["h"] <= v0 * 1.05)
