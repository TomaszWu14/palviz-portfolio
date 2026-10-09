"""PHV + wspólny serwis hierarchii (ui/hierarchy.py): identyczne liczby na desktopie
i skanerze, brak poziomu „Warstwa", poziomy wg kategorii, spójność przeliczników,
zgłoszenia z mailem i prefill „Brak przelicznika"."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from django.utils import timezone

from ui.hierarchy import build_hierarchy, enrich_pallet_metrics
from ui.models import (Product, PalletizationInstruction, InnerPack,
                       PackagingIssue, PickerActivity, PickerActivityBatch,
                       Notification, LocationIssue, WarehouseLocationMaster,
                       WarehouseLocationMasterBatch,
                       Shipment, HandlingUnit, HandlingUnitItem)
from ui.roles import GROUP_WAREHOUSE, GROUP_MASTER_DATA

from .test_phv import _product, _wh

class MaterialCardEnrichment(TestCase):
    """Karta materiału: metryki prezentacji + numeryczne pola + statystyka JM."""

    def test_enrich_matches_between_planner_and_phv(self):
        # Regresja: vol_pct/mult liczone jednym helperem = te same liczby, co dawniej.
        h = build_hierarchy(_product("E1"))
        vol_pct = enrich_pallet_metrics(h["levels"], h["summary"], h["instr"])
        self.assertIsNotNone(vol_pct)
        self.assertTrue(0 < vol_pct <= 100)
        carton = next(l for l in h["levels"] if l["key"] == "carton")
        self.assertIsNotNone(carton["mult"])                 # karton → ile niżej

    def test_levels_have_numeric_dims_and_uom(self):
        h = build_hierarchy(_product("E2"))
        carton = next(l for l in h["levels"] if l["key"] == "carton")
        self.assertEqual((carton["l_cm"], carton["w_cm"], carton["h_cm"]), (29, 25, 22))
        self.assertAlmostEqual(carton["vol_m3"], 29 * 25 * 22 / 1_000_000, places=5)
        self.assertEqual(carton["uom_kind"], "collective")
        unit = next(l for l in h["levels"] if l["key"] == "unit")
        self.assertEqual(unit["uom_kind"], "base")

    def test_unit_level_exposes_ean(self):
        # MATinfo pokazuje EAN każdej jednostki obok wagi/wymiarów — poziom sztuki
        # dziedziczy EAN produktu z master daty.
        h = build_hierarchy(_product("E9"))
        unit = next(l for l in h["levels"] if l["key"] == "unit")
        self.assertEqual(unit["ean"], "5900000001500")

    def test_opz_exposes_ean_and_sales_unit_level_dropped(self):
        # OPZ ma własny EAN w master dacie (InnerPack) — MATinfo pokazuje go przy poziomie.
        # Poziom „Opakowanie handlowe" (sales_unit) jest pominięty (MATinfo = max 4 poziomy),
        # nawet gdy InnerPack ma wypełnione wymiary/EAN sprzedażowe.
        ip = InnerPack.objects.create(
            name="OPZ 12", length_cm=30, width_cm=20, height_cm=15, units_per_pack=12,
            ean="5901111111118",
            sales_unit_l_cm=10, sales_unit_w_cm=8, sales_unit_h_cm=6,
            sales_units_per_pack=2, sales_unit_ean="5902222222229")
        p = _product("E10")
        instr = p.latest_instruction()
        instr.inner_pack = ip
        instr.packs_per_carton = 1
        instr.pcs_per_inner_pack = 12
        instr.save()
        levels = {l["key"]: l for l in build_hierarchy(p)["levels"]}
        self.assertEqual(levels["inner_pack"]["ean"], "5901111111118")
        self.assertNotIn("sales_unit", levels)

    def test_issue_stat_dominant_unit(self):
        from ui.views.phv import _issue_stat
        p = _product("E3")
        b = PickerActivityBatch.objects.create(name="t")
        now = timezone.now()
        for _ in range(7):
            PickerActivity.objects.create(batch=b, location_code="A", confirmed_at=now,
                                          material_code="E3", unit="KAR")
        for _ in range(3):
            PickerActivity.objects.create(batch=b, location_code="A", confirmed_at=now,
                                          material_code="E3", unit="ST")
        stat = _issue_stat(p)
        self.assertEqual(stat["unit"], "KAR")
        self.assertEqual(stat["pct"], 70)                    # 7/10
        self.assertEqual(stat["total"], 10)

    def test_issue_stat_none_without_history(self):
        from ui.views.phv import _issue_stat
        self.assertIsNone(_issue_stat(_product("E4")))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ReportNotifiesMasterData(TestCase):
    """Zgłoszenie natychmiast powiadamia zespół master daty (in-app bell)."""

    def test_report_notifies_owner_users(self):
        md = get_user_model().objects.create_user("md", password="x")
        md.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        reporter = _wh("rep2")
        self.client.force_login(reporter)
        _product("R11")
        self.client.post(reverse("ui:phv_report"), {
            "ref_code": "R11", "issue_type": "carton_underfilled",
            "current_value": "wypełnienie 40%"})
        self.assertEqual(PackagingIssue.objects.count(), 1)
        note = Notification.objects.filter(recipient=md).first()
        self.assertIsNotNone(note)                           # Master Data dostaje bell
        self.assertIn("R11", note.title)


class LocationScan(TestCase):
    """Skan kodu lokalizacji (zamiast REF): karta lokalizacji + zgłoszenie problemu."""

    def setUp(self):
        self.client.force_login(_wh())

    def _loc(self, code="B0-01-100A", **kw):
        b = WarehouseLocationMasterBatch.objects.create(name="t", is_active=True)
        return WarehouseLocationMaster.objects.create(
            batch=b, location_code=code, warehouse_type="0052",
            max_volume_m3=2.1, max_weight_kg=300, height_mm=1800, **kw)

    def test_location_code_routes_to_location_card(self):
        self._loc()
        r = self.client.get(reverse("ui:phv_home"), {"q": "B0-01-100A"})
        self.assertIsNone(r.context["product"])
        loc = r.context["location"]
        self.assertEqual(loc["code"], "B0-01-100A")
        self.assertEqual(loc["zone"], "B0")                  # strefa z prefiksu
        self.assertTrue(loc["in_master"])
        self.assertEqual(loc["master"].max_volume_m3, 2.1)

    def test_blocked_flag_shown(self):
        self._loc(blocked_pick=True)
        r = self.client.get(reverse("ui:phv_home"), {"q": "B0-01-100A"})
        self.assertContains(r, "Lokalizacja zablokowana")

    def test_location_outside_master(self):
        r = self.client.get(reverse("ui:phv_home"), {"q": "Z9-99-999Z"})
        loc = r.context["location"]
        self.assertFalse(loc["in_master"])
        self.assertIsNone(loc["master"])

    def test_product_ref_not_treated_as_location(self):
        _product("DMOM10001")
        r = self.client.get(reverse("ui:phv_home"), {"q": "DMOM10001"})
        self.assertIsNone(r.context["location"])             # REF ≠ kształt lokalizacji
        self.assertIsNotNone(r.context["product"])


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class LocationReporting(TestCase):
    def setUp(self):
        self.u = _wh("locrep")
        self.client.force_login(self.u)

    def test_report_creates_location_issue_and_notifies(self):
        md = get_user_model().objects.create_user("md2", password="x")
        md.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.post(reverse("ui:phv_location_report"), {
            "location_code": "B0-01-100A", "issue_type": "damaged_beam",
            "description": "belka pęknięta"})
        issue = LocationIssue.objects.get()
        self.assertEqual(issue.location_code, "B0-01-100A")
        self.assertEqual(issue.reporter, self.u)
        self.assertIsNotNone(Notification.objects.filter(recipient=md).first())

    def test_invalid_location_type_rejected(self):
        self.client.post(reverse("ui:phv_location_report"),
                         {"location_code": "B0-01-100A", "issue_type": "zzz"})
        self.assertEqual(LocationIssue.objects.count(), 0)

    def test_my_issues_lists_material_and_location(self):
        PackagingIssue.objects.create(ref_code="R20", issue_type="other", reporter=self.u)
        LocationIssue.objects.create(location_code="B0-01-100A", issue_type="damaged_label",
                                     reporter=self.u)
        r = self.client.get(reverse("ui:phv_my_issues"))
        self.assertContains(r, "R20")                        # zgłoszenie materiału
        self.assertContains(r, "B0-01-100A")                 # zgłoszenie lokalizacji
        self.assertContains(r, "LOK")


class RecentHistoryTests(TestCase):
    """B1: historia podglądów per user, server-side, 10 ostatnich bez duplikatów."""

    def setUp(self):
        from django.contrib.auth.models import Group
        self.u = get_user_model().objects.create_user(username="hist", password="x")
        self.u.groups.add(Group.objects.get_or_create(name=GROUP_WAREHOUSE)[0])
        self.client.force_login(self.u)

    def test_viewing_records_and_lists_without_duplicates(self):
        from ui.models import ProductViewHistory
        p1 = Product.objects.create(code="H-1", name="Hist 1")
        p2 = Product.objects.create(code="H-2", name="Hist 2")
        self.client.get(reverse("ui:phv_home"), {"q": "H-1"})
        self.client.get(reverse("ui:phv_home"), {"q": "H-2"})
        self.client.get(reverse("ui:phv_home"), {"q": "H-1"})   # ponowne → bez duplikatu
        self.assertEqual(ProductViewHistory.objects.filter(user=self.u).count(), 2)
        r = self.client.get(reverse("ui:phv_home"))
        codes = [h.product.code for h in r.context["recent"]]
        self.assertEqual(codes, ["H-1", "H-2"])                 # najnowszy pierwszy

    def test_history_is_per_user(self):
        from django.contrib.auth.models import Group
        Product.objects.create(code="H-3", name="Hist 3")
        self.client.get(reverse("ui:phv_home"), {"q": "H-3"})
        other = get_user_model().objects.create_user(username="hist2", password="x")
        other.groups.add(Group.objects.get_or_create(name=GROUP_WAREHOUSE)[0])
        self.client.force_login(other)
        r = self.client.get(reverse("ui:phv_home"))
        self.assertEqual(list(r.context["recent"]), [])


class EanSearchTests(TestCase):
    """B3: EAN w typeahead + detekcja 8/13 cyfr jako EAN-first."""

    def setUp(self):
        from django.contrib.auth.models import Group
        u = get_user_model().objects.create_user(username="ean-u", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_WAREHOUSE)[0])
        self.client.force_login(u)

    def test_ean13_hits_ean_before_numeric_ref(self):
        """REF o treści identycznej z cudzym EAN-13 nie przesłania skanu kodu."""
        decoy = Product.objects.create(code="5901234123457", name="Numeryczny REF")
        target = Product.objects.create(code="T-EAN", name="Właściwy", ean="5901234123457")
        r = self.client.get(reverse("ui:phv_home"), {"q": "5901234123457"})
        self.assertEqual(r.context["product"], target)
        # Krótszy ciąg cyfr (nie 8/13) → normalna kolejność: najpierw REF.
        decoy2 = Product.objects.create(code="12345", name="REF 5 cyfr")
        Product.objects.create(code="X-1", name="X", ean="12345")
        r = self.client.get(reverse("ui:phv_home"), {"q": "12345"})
        self.assertEqual(r.context["product"], decoy2)

    def test_suggest_matches_ean_prefix(self):
        Product.objects.create(code="SG-1", name="Sugestia", ean="5901234000011")
        d = self.client.get(reverse("ui:phv_suggest"), {"q": "590123400"}).json()
        self.assertIn("SG-1", [row["code"] for row in d["results"]])


class ReportButtonsRemovedTests(TestCase):
    """B4: zgłoszenia tylko przez okno na dole — bez przycisków przy kartach 3D."""

    def test_no_inline_report_buttons_on_levels(self):
        from django.contrib.auth.models import Group
        u = get_user_model().objects.create_user(username="b4u", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_WAREHOUSE)[0])
        self.client.force_login(u)
        p = Product.objects.create(code="B4-1", name="B4")
        PalletizationInstruction.objects.create(
            product=p, version=1, is_active=True, unit_weight=0.5, pcs_per_carton=10,
            carton_l=40, carton_w=30, carton_h=25, pallet_length_cm=120,
            pallet_width_cm=80, max_height_total_cm=215, pallet_base_height_cm=15)
        r = self.client.get(reverse("ui:phv_home"), {"q": "B4-1"})
        self.assertNotContains(r, "Zgłoś optymalizację")
        self.assertNotContains(r, "Zgłoś zdjęcie")
        self.assertContains(r, 'id="phv-report"')      # okno na dole zostaje


class NewIssueTypesTests(TestCase):
    """B6: „Zmień sposób paletyzacji" (powód wymagany) + „Zweryfikuj objętość"."""

    def setUp(self):
        from django.contrib.auth.models import Group
        self.u = get_user_model().objects.create_user(username="b6u", password="x")
        self.u.groups.add(Group.objects.get_or_create(name=GROUP_WAREHOUSE)[0])
        self.client.force_login(self.u)
        Product.objects.create(code="B6-1", name="B6")

    def test_change_palletization_requires_reason(self):
        from ui.models import PackagingIssue
        r = self.client.post(reverse("ui:phv_report"), {
            "ref_code": "B6-1", "issue_type": "change_palletization"})
        self.assertFalse(PackagingIssue.objects.exists())          # bez powodu → odbite
        self.client.post(reverse("ui:phv_report"), {
            "ref_code": "B6-1", "issue_type": "change_palletization",
            "correct_value": "carton_size", "description": "nowy karton"})
        issue = PackagingIssue.objects.get()
        self.assertEqual(issue.issue_type, "change_palletization")
        self.assertEqual(issue.correct_value, "Zmiana rozmiaru kartonu")  # etykieta, nie kod

    def test_verify_volume_goes_through_pipeline(self):
        from ui.models import PackagingIssue
        self.client.post(reverse("ui:phv_report"), {
            "ref_code": "B6-1", "issue_type": "verify_volume",
            "description": "objętość podejrzana"})
        self.assertTrue(PackagingIssue.objects.filter(issue_type="verify_volume").exists())

    def test_new_types_visible_in_picker_and_admin_list(self):
        r = self.client.get(reverse("ui:phv_home"), {"q": "B6-1"})
        self.assertContains(r, "Zmień sposób paletyzacji")
        self.assertContains(r, "Zweryfikuj objętość produktu")


class LocationMatinfoFormatTests(TestCase):
    """BLOK C: karta lokalizacji pokazuje produkty w formacie MATINFO — przeliczniki
    AJM z tego samego serwisu co karta materiału (unit_factors) + link do karty."""

    def setUp(self):
        self.client.force_login(_wh())
        b = WarehouseLocationMasterBatch.objects.create(name="c", is_active=True)
        WarehouseLocationMaster.objects.create(batch=b, location_code="B0-05-100A",
                                               warehouse_type="0052")
        self.p = _product("C-LOC-1")
        PalletizationInstruction.objects.create(       # v2 — _product tworzy już v1
            product=self.p, version=2, is_active=True, unit_weight=0.5, pcs_per_carton=24,
            carton_l=40, carton_w=30, carton_h=25, pallet_length_cm=120,
            pallet_width_cm=80, max_height_total_cm=215, pallet_base_height_cm=15,
            layouts=[{"name": "L1", "cartons_per_pallet": 32}], selected_layout="L1")
        sh = Shipment.objects.create(name="Stok C", is_stock=True)
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, location="B0-05-100A")
        HandlingUnitItem.objects.create(hu=hu, product=self.p, ref_code="C-LOC-1",
                                        expected_qty=48, unit="KAR")

    def test_products_section_shows_shared_factors(self):
        from ui.hierarchy import unit_factors
        r = self.client.get(reverse("ui:phv_home"), {"q": "B0-05-100A"})
        prods = r.context["location"]["products"]
        self.assertEqual(len(prods), 1)
        f = prods[0]["factors"]
        # Te same wartości co wspólny serwis (zero duplikacji logiki).
        self.assertEqual(f, unit_factors(self.p.latest_instruction()))
        self.assertEqual(f["kar"], 24.0)
        self.assertEqual(f["pal"], 32 * 24.0)
        self.assertContains(r, "PRODUKTY W LOKALIZACJI")
        self.assertContains(r, "?q=C-LOC-1")                 # link do pełnej karty MATINFO
