"""PHV + wspólny serwis hierarchii (ui/hierarchy.py): identyczne liczby na desktopie
i skanerze, brak poziomu „Warstwa", poziomy wg kategorii, spójność przeliczników,
zgłoszenia z mailem i prefill „Brak przelicznika"."""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse


from ui.hierarchy import build_hierarchy
from ui.models import (Product, ProductCategory, PalletizationInstruction, InnerPack,
                       MaterialReference,
                       PackagingIssue, LocationIssue, Shipment, HandlingUnit, HandlingUnitItem)
from ui.roles import GROUP_WAREHOUSE, GROUP_TRANSPORT, GROUP_MASTER_DATA, GROUP_OPTIMIZER


def _wh(name="mag", group=GROUP_WAREHOUSE):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=group)[0])
    return u


class RefSuggestSharedTests(TestCase):
    """UX #9: typeahead REF (phv_suggest) jest współdzielony przez carton_opt,
    warehouse/search, ukraine i panel lidera — guard `_any_role`, więc rola bez
    modułu phv (np. Optymalizacja kartonów) też dostaje podpowiedzi, a nie 403."""

    def test_optimizer_without_phv_module_gets_suggestions(self):
        _product(code="DMOM10001")
        self.client.force_login(_wh("opt", GROUP_OPTIMIZER))
        r = self.client.get(reverse("ui:phv_suggest"), {"q": "DMOM"})
        self.assertEqual(r.status_code, 200)
        codes = [row["code"] for row in r.json()["results"]]
        self.assertIn("DMOM10001", codes)

    def test_anonymous_still_blocked(self):
        r = self.client.get(reverse("ui:phv_suggest"), {"q": "DMOM"})
        self.assertNotEqual(r.status_code, 200)


def _product(code="DMOM10001", ppc=10, weight=0.458, cpp=99, layers=9, category=None):
    """Produkt à la DMOM10001: 10 szt/KAR, 99 KAR/PAL (9 warstw) → 990 szt/PAL, 453,4 kg."""
    p = Product.objects.create(code=code, name="Rękawice nitrylowe", ean="5900000001500",
                               unit_length_cm=21, unit_width_cm=12, unit_height_cm=5.5,
                               category=category)
    PalletizationInstruction.objects.create(
        product=p, name="v1", is_active=True, version=1,
        unit_weight=weight, pcs_per_carton=ppc,
        carton_l=29, carton_w=25, carton_h=22,
        pallet_length_cm=120, pallet_width_cm=80,
        pallet_base_height_cm=15, max_height_total_cm=213,
        layouts=[{"name": "L1", "cartons_per_pallet": cpp, "cartons_per_layer": 11,
                  "layers_used": layers, "placements": []}])
    return p


class SharedServiceNumbers(TestCase):
    """Kryterium akceptacji: 10 szt/KAR · 99 KAR/PAL · 990 szt/PAL · 453,4 kg/PAL."""

    def test_dmom_numbers(self):
        h = build_hierarchy(_product())
        s = h["summary"]
        self.assertEqual(s["cartons_per_pallet"], 99)
        self.assertEqual(s["pcs_per_pallet"], 990)
        self.assertEqual(s["pallet_weight_kg"], 453.4)      # 99 × (0.458×10)
        self.assertEqual(s["layers"], 9)
        self.assertEqual(h["alerts"], [])                    # spójne → zero alertów

    def test_no_layer_level(self):
        h = build_hierarchy(_product("R2"))
        keys = [l["key"] for l in h["levels"]]
        self.assertNotIn("layer", keys)                      # Warstwa nie jest poziomem
        pallet = next(l for l in h["levels"] if l["key"] == "pallet")
        self.assertIn("9 warstw", pallet["qty"])             # …ale zostaje metadaną

    def test_inconsistent_converters_alert(self):
        p = _product("R3")
        instr = p.latest_instruction()
        instr.packs_per_carton = 3
        instr.pcs_per_inner_pack = 4                         # 3×4=12 ≠ 10 szt/karton
        instr.save()
        h = build_hierarchy(p)
        self.assertTrue(any("Niespójność" in a for a in h["alerts"]))

    def test_desktop_and_phv_serve_same_numbers(self):
        p = _product("R4")
        adm = get_user_model().objects.create_user("adm", password="x", is_superuser=True)
        self.client.force_login(adm)
        r_desktop = self.client.get(reverse("ui:planner_product_hierarchy", args=[p.pk]))
        r_phv = self.client.get(reverse("ui:phv_home"), {"q": p.code})
        d = r_desktop.context["summary"]
        s = r_phv.context["hierarchy"]["summary"]
        self.assertEqual((d["pcs_per_pallet"], d["cartons_per_pallet"], d["pallet_weight_kg"]),
                         (s["pcs_per_pallet"], s["cartons_per_pallet"], s["pallet_weight_kg"]))
        self.assertEqual([l["key"] for l in r_desktop.context["levels"]],
                         [l["key"] for l in r_phv.context["hierarchy"]["levels"]])


class CategoryLevels(TestCase):
    def test_category_limits_levels(self):
        cat = ProductCategory.objects.create(name="Rękawice", code="REK",
                                             hierarchy_levels="pallet,carton,unit")
        h = build_hierarchy(_product("R5", category=cat))
        self.assertEqual([l["key"] for l in h["levels"]], ["pallet", "carton", "unit"])
        self.assertEqual(h["alerts"], [])

    def test_required_level_missing_alerts(self):
        cat = ProductCategory.objects.create(name="Zbiorcze", code="ZB",
                                             hierarchy_levels="pallet,carton,inner_pack")
        h = build_hierarchy(_product("R6", category=cat))   # brak inner_pack w master dacie
        self.assertTrue(any("Brak przelicznika: sztuka → opakowanie zbiorcze" in a
                            for a in h["alerts"]))


class PHVView(TestCase):
    def setUp(self):
        self.client.force_login(_wh())

    def test_levels_have_three_data(self):
        p = _product("R7")
        r = self.client.get(reverse("ui:phv_home"), {"q": p.code})
        self.assertEqual(r.status_code, 200)
        for l in r.context["hierarchy"]["levels"]:
            self.assertIn("three_data", l)                   # render 3D na każdym poziomie
        self.assertContains(r, "data-three")
        self.assertContains(r, "palviz-three.js")

    def test_level_without_ean_shows_dash(self):
        # Karton bez EAN w master dacie → wiersz „EAN: —" (nie pusto), żeby było jasne
        # że to brak danych, a nie brak funkcji. Sztuka ma EAN produktu → wartość.
        p = _product("R9")                                   # instr bez Carton FK → carton.ean pusty
        r = self.client.get(reverse("ui:phv_home"), {"q": p.code})
        self.assertContains(r, "EAN: <span")                 # wiersz EAN jest renderowany
        self.assertContains(r, "—")                          # karton bez EAN → myślnik
        self.assertContains(r, "5900000001500")              # sztuka → realny EAN

    def test_levels_have_dblclick_zoom(self):
        # Dwuklik na KAŻDEJ karcie poziomu otwiera pełnoekranowe zbliżenie 3D.
        p = _product("R8")
        r = self.client.get(reverse("ui:phv_home"), {"q": p.code})
        self.assertContains(r, "phvZoom")
        self.assertContains(r, 'addEventListener("dblclick"')
        self.assertContains(r, "viewer.destroy")   # sprzątanie WebGL przy zamknięciu zoomu
        self.assertContains(r, "phv-facts")        # panel danych pod modelem w zoomie
        n_levels = len(r.context["hierarchy"]["levels"])
        # Liczymy KARTY, nie wszystkie wystąpienia klasy — selektorów `.phv-level` w JS
        # bywa więcej niż jeden (zoom, leniwe budowanie modeli) i nie o nie tu chodzi.
        self.assertEqual(r.content.decode().count('class="card phv-level"'), n_levels)

    def test_header_shows_strategy_not_duplicate_kpi(self):
        """Liczby palety tylko przy wierszu Palety; nagłówek = strategia magazynowa."""
        p = _product("R20")
        r = self.client.get(reverse("ui:phv_home"), {"q": p.code})
        body = r.content.decode()
        self.assertContains(r, "Dane magazynowe")
        self.assertEqual(body.count("/pal."), 1)            # liczby palety tylko raz
        self.assertNotIn("wypełnienie palety", body)        # stary kafel KPI usunięty

    def test_md_pill_ok_when_no_alerts(self):
        p = _product("R40")
        r = self.client.get(reverse("ui:phv_home"), {"q": p.code})
        self.assertContains(r, "Master data OK")

    def test_md_pill_incomplete_when_alerts(self):
        cat = ProductCategory.objects.create(name="Zb", code="ZB2",
                                             hierarchy_levels="pallet,carton,inner_pack")
        p = _product("R41", category=cat)                    # brak inner_pack → alert
        r = self.client.get(reverse("ui:phv_home"), {"q": p.code})
        self.assertContains(r, "Master data niekompletna")

    def test_product_card_shows_description_not_ref_ean_line(self):
        p = _product("R30")                                  # name="Rękawice nitrylowe"
        r = self.client.get(reverse("ui:phv_home"), {"q": p.code})
        self.assertContains(r, "Rękawice nitrylowe")          # opis materiału widoczny
        self.assertNotContains(r, "· EAN:")                  # zdublowana linia REF·EAN usunięta

    def test_description_falls_back_to_mara(self):
        p = _product("R31")
        p.name = "R31"                                       # brak opisu (== kod)
        p.save()
        MaterialReference.objects.create(code="R31", name="Kubek 200ml")
        r = self.client.get(reverse("ui:phv_home"), {"q": "R31"})
        self.assertEqual(r.context["material_desc"], "Kubek 200ml")

    def test_no_process_offers_report_with_three_options(self):
        p = _product("R21")                                  # brak stocku → brak procesu
        r = self.client.get(reverse("ui:phv_home"), {"q": p.code})
        self.assertFalse(r.context["strategy"]["has_process"])
        self.assertContains(r, "Brak procesu magazynowego")
        self.assertContains(r, "missing_process")
        for code in ("0050", "0052", "0070"):
            self.assertContains(r, code)

    def test_strategy_reads_current_stock(self):
        p = _product("R22")
        sh = Shipment.objects.create(name="Stock", is_stock=True)
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HU-S1",
                                         warehouse_type="0050", location="B0-01-100A")
        HandlingUnitItem.objects.create(hu=hu, product=p, ref_code=p.code)
        r = self.client.get(reverse("ui:phv_home"), {"q": p.code})
        strat = r.context["strategy"]
        self.assertTrue(strat["has_process"])
        self.assertEqual(strat["processes"][0]["code"], "0050")
        self.assertIn("B0-01-100A", [l["code"] for l in strat["processes"][0]["locations"]])
        self.assertContains(r, "Fix")

    def test_stock_groups_drilldown_with_volume_hu_status(self):
        p = _product("R23")
        sh = Shipment.objects.create(name="Stock", is_stock=True)
        # Typ 0010 (nie-proces) → trafia do stock_groups z drill-downem.
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HU-0010",
                                         warehouse_type="0010", location="C1-02-030B",
                                         length_cm=120, width_cm=80, height_cm=100,
                                         stock_status="B6")
        HandlingUnitItem.objects.create(hu=hu, product=p, ref_code=p.code)
        r = self.client.get(reverse("ui:phv_home"), {"q": p.code})
        groups = r.context["strategy"]["stock_groups"]
        g = next(g for g in groups if g["code"] == "0010")
        self.assertEqual(g["count"], 1)
        row = g["rows"][0]
        self.assertEqual(row["location"], "C1-02-030B")
        self.assertEqual(row["hu_code"], "HU-0010")
        self.assertEqual(row["volume_m3"], 0.96)            # 120×80×100 cm = 0,96 m³
        self.assertEqual(row["status"], "B6")
        self.assertEqual(g["total_volume_m3"], 0.96)        # suma objętości typu
        HandlingUnitItem.objects.filter(hu=hu).update(expected_qty=990)
        r2 = self.client.get(reverse("ui:phv_home"), {"q": p.code})
        g2 = next(x for x in r2.context["strategy"]["stock_groups"] if x["code"] == "0010")
        self.assertEqual(g2["base_qty"], 990)               # suma szt (jedn. podstawowa)
        self.assertContains(r2, "990")                      # „… · 990 szt" w panelu
        self.assertContains(r, "Dane magazynowe")
        self.assertContains(r, "C1-02-030B")
        self.assertContains(r, "B6")
        self.assertContains(r, "paleta")                    # odmiana singularna (nie „palet")

    def test_fuzzy_suggestion_on_typo(self):
        # Literowka w kodzie (transpozycja 471→741) — icontains nie trafi, RapidFuzz tak.
        try:
            import rapidfuzz  # noqa: F401
        except Exception:
            self.skipTest("rapidfuzz not installed")
        # REF-owy kod (bez myslnikow — nie pasuje do wzorca lokalizacji _LOC_RE).
        _product("DMOM10001")
        r = self.client.get(reverse("ui:phv_home"), {"q": "DMOM10011"})
        self.assertIsNone(r.context["product"])                # nie ma dokladnego
        codes = [s["code"] for s in r.context["suggestions"]]
        self.assertIn("DMOM10001", codes)                      # rozmyte trafienie

    def test_carton_ean_resolves_product(self):
        # Skan EAN kartonu (numeryczny — nie pasuje do _LOC_RE) trafia na produkt.
        from ui.models import Carton
        p = _product("REF-CE")
        instr = p.latest_instruction()
        instr.carton = Carton.objects.create(name="K", length_cm=29, width_cm=25,
                                              height_cm=22, unit_weight_kg=4.5, ean="5901111000012")
        instr.save()
        r = self.client.get(reverse("ui:phv_home"), {"q": "5901111000012"})
        self.assertIsNotNone(r.context["product"])
        self.assertEqual(r.context["product"].code, "REF-CE")

    def test_carton_glb_model_passed_to_hierarchy(self):
        # Karton z wgranym modelem 3D (.glb) → three_data kartonu ma glb_url; bez
        # pliku brak klucza (generator bryły zostaje bez zmian).
        from django.core.files.uploadedfile import SimpleUploadedFile
        from ui.models import Carton
        from ui.hierarchy import build_hierarchy
        p = _product("REF-GLB")
        instr = p.latest_instruction()
        instr.carton = Carton.objects.create(
            name="K", length_cm=29, width_cm=25, height_cm=22, unit_weight_kg=4.5,
            glb_model=SimpleUploadedFile("carton.glb", b"glTF-fake-bytes"))
        instr.save()
        h = build_hierarchy(p, instr)
        carton_level = next(l for l in h["levels"] if l["key"] == "carton")
        self.assertIn("glb_url", json.loads(carton_level["three_data"]))

    def test_hierarchy_shows_glb_upload_shortcut_when_carton_linked(self):
        # Skrót "Model 3D" na karcie kartonu w hierarchii produktu, prowadzi wprost
        # do formularza kartonu (żeby nie trzeba było go szukać w liście Kartony).
        from django.contrib.auth import get_user_model as _gum
        from ui.models import Carton
        p = _product("REF-SHORTCUT")
        instr = p.latest_instruction()
        carton = Carton.objects.create(name="K", length_cm=29, width_cm=25,
                                       height_cm=22, unit_weight_kg=4.5)
        instr.carton = carton
        instr.save()
        planner = _gum().objects.create_user(username="pl1", password="x")
        planner.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(planner)
        r = self.client.get(reverse("ui:planner_product_hierarchy", args=[p.pk]))
        self.assertContains(r, reverse("ui:planner_carton_edit", args=[carton.pk]))
        self.assertContains(r, "Model 3D")

    def test_opz_ean_resolves_product(self):
        # Skan EAN opakowania zbiorczego (OPZ) trafia na produkt.
        p = _product("REF-OE")
        instr = p.latest_instruction()
        instr.inner_pack = InnerPack.objects.create(name="OPZ", length_cm=21, width_cm=12,
                                                    height_cm=6, ean="5902222000019")
        instr.save()
        r = self.client.get(reverse("ui:phv_home"), {"q": "5902222000019"})
        self.assertEqual(r.context["product"].code, "REF-OE")

    def test_pill_incomplete_when_no_description(self):
        p = _product("REF-ND")
        p.name = "REF-ND"                                     # nazwa == kod → brak opisu
        p.save()
        r = self.client.get(reverse("ui:phv_home"), {"q": "REF-ND"})
        self.assertEqual(r.context["material_desc"], "")
        self.assertContains(r, "Master data niekompletna")   # pill spójny z brakiem opisu

    def test_missing_description_has_no_report_banner(self):
        # Zgłaszanie braku opisu wyłączone — brak czerwonego banera „zgłoś".
        p = _product("R25")
        p.name = "R25"                                       # brak opisu (== kod), brak MARA
        p.save()
        r = self.client.get(reverse("ui:phv_home"), {"q": "R25"})
        self.assertEqual(r.context["material_desc"], "")
        self.assertNotContains(r, "Brak opisu w master dacie")

    def test_pl_palety_filter(self):
        from ui.templatetags.palviz_extras import pl_palety
        self.assertEqual(pl_palety(1), "paleta")
        self.assertEqual(pl_palety(2), "palety")
        self.assertEqual(pl_palety(4), "palety")
        self.assertEqual(pl_palety(5), "palet")
        self.assertEqual(pl_palety(12), "palet")            # nastka → palet
        self.assertEqual(pl_palety(22), "palety")
        self.assertEqual(pl_palety(25), "palet")

    def test_material_desc_prefers_master_data(self):
        p = _product("R24")
        p.name = "Krótka nazwa"
        p.save()
        MaterialReference.objects.create(code="R24", name="Przyrząd do infuzji z filtrem 15µm")
        r = self.client.get(reverse("ui:phv_home"), {"q": "R24"})
        # Autorytatywny pełny opis z master daty (MARA/MAKTX), nie krótka nazwa produktu.
        self.assertEqual(r.context["material_desc"], "Przyrząd do infuzji z filtrem 15µm")

    def test_missing_converter_prefills_report(self):
        cat = ProductCategory.objects.create(name="Z", code="Z2",
                                             hierarchy_levels="pallet,carton,inner_pack")
        p = _product("R8", category=cat)
        r = self.client.get(reverse("ui:phv_home"), {"q": p.code})
        self.assertTrue(r.context["prefill_missing"])
        self.assertContains(r, 'value="missing_conversion" selected')

    def test_unknown_ref_suggests(self):
        _product("ABC-100")
        r = self.client.get(reverse("ui:phv_home"), {"q": "ABC"})
        self.assertIsNone(r.context["product"])
        self.assertEqual(r.context["suggestions"][0]["code"], "ABC-100")

    def test_ean_lookup(self):
        _product("R9")
        r = self.client.get(reverse("ui:phv_home"), {"q": "5900000001500"})
        self.assertEqual(r.context["product"].code, "R9")

    def test_role_gate(self):
        tr = _wh("tr", GROUP_TRANSPORT)
        self.client.force_login(tr)
        r = self.client.get(reverse("ui:phv_home"))
        self.assertNotEqual(r.status_code, 200)


@override_settings(EMAIL_HOST="smtp.test", PHV_ISSUE_EMAIL="md@example.com",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class IssueReporting(TestCase):
    def setUp(self):
        self.u = _wh("rep")
        self.client.force_login(self.u)

    def test_report_creates_issue_and_sends_mail(self):
        _product("R10")
        self.client.post(reverse("ui:phv_report"), {
            "ref_code": "R10", "issue_type": "wrong_conversion",
            "current_value": "30 szt/karton", "correct_value": "50 szt/karton"})
        issue = PackagingIssue.objects.get()
        self.assertEqual(issue.reporter, self.u)
        self.assertEqual(issue.status, "open")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("md@example.com", mail.outbox[0].to)
        self.assertIn("50 szt/karton", mail.outbox[0].body)

    def test_invalid_type_rejected(self):
        self.client.post(reverse("ui:phv_report"), {"ref_code": "X", "issue_type": "zzz"})
        self.assertEqual(PackagingIssue.objects.count(), 0)

    def test_report_emits_matinfo_issue_event(self):
        from unittest.mock import patch
        _product("R12")
        with patch("ui.notifications.emit_event") as mock_emit:
            self.client.post(reverse("ui:phv_report"), {
                "ref_code": "R12", "issue_type": "wrong_conversion",
                "description": "tajny opis z danymi klienta"})
        mock_emit.assert_called_once()
        kind, payload = mock_emit.call_args.args
        self.assertEqual(kind, "matinfo_issue")
        self.assertEqual(set(payload), {"issue_id", "issue_type", "issue_type_display", "ref_code"})
        self.assertEqual(payload["ref_code"], "R12")
        self.assertNotIn("tajny opis", str(payload))       # opis nie wychodzi

    def test_location_report_emits_location_issue_event(self):
        from unittest.mock import patch
        itype = LocationIssue.TYPES[0][0]
        with patch("ui.notifications.emit_event") as mock_emit:
            self.client.post(reverse("ui:phv_location_report"), {
                "location_code": "B0-01-100A", "issue_type": itype})
        mock_emit.assert_called_once()
        kind, payload = mock_emit.call_args.args
        self.assertEqual(kind, "location_issue")
        self.assertEqual(set(payload), {"issue_id", "issue_type", "issue_type_display", "location_code"})
        self.assertEqual(payload["location_code"], "B0-01-100A")

    def test_my_issues_lists_and_single_status(self):
        i = PackagingIssue.objects.create(ref_code="R9", issue_type="other",
                                          reporter=self.u, status="in_review")
        r = self.client.get(reverse("ui:phv_my_issues"), {"issue": i.pk})
        self.assertContains(r, f"#{i.pk}")
        self.assertContains(r, "W przeglądzie")
