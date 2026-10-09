# Testy RAG-lite ZARII: rozpoznawanie kodów w pytaniu i budowa bloku kontekstu.
from django.test import TestCase

from ui.models import (Product, Customer, Shipment, HandlingUnit, HandlingUnitItem,
                       WarehouseSnapshot, WarehouseSnapshotRow,
                       Carton, PalletizationInstruction, HUQualityIssue)
from ui.zaria_rag import context_for, _codes


class ZariaRagCodesTests(TestCase):
    def test_extracts_sku_hu_and_location_codes(self):
        codes = _codes("Gdzie leży ZR-1001? Paleta HU123456 w B0-01-100A")
        self.assertIn("ZR-1001", codes)
        self.assertIn("HU123456", codes)
        self.assertIn("B0-01-100A", codes)

    def test_plain_text_yields_no_codes(self):
        self.assertEqual(_codes("napisz maila do przewoźnika po niemiecku"), [])


class ZariaRagContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User
        cls.admin = User.objects.create_superuser("rag_admin", "a@x.pl", "x")
    def test_no_match_returns_empty(self):
        self.assertEqual(context_for("jaka jest stolica Francji?", self.admin), "")

    def test_product_lookup(self):
        Product.objects.create(code="ZR-1001", name="Kompresy gazowe 10×10")
        ctx = context_for("co to jest ZR-1001?", self.admin)
        self.assertIn("ZR-1001", ctx)
        self.assertIn("Kompresy gazowe", ctx)
        self.assertIn("DANE GROOVE", ctx)

    def test_product_location_via_hu_items(self):
        sh = Shipment.objects.create()
        hu = HandlingUnit.objects.create(shipment=sh, code="HU777", location="B0-01-100A")
        HandlingUnitItem.objects.create(hu=hu, ref_code="ZR-1001", expected_qty=5, unit="kar")
        ctx = context_for("gdzie leży ZR-1001?", self.admin)
        self.assertIn("B0-01-100A", ctx)
        self.assertIn("HU777", ctx)

    def test_location_from_latest_snapshot(self):
        snap = WarehouseSnapshot.objects.create(name="s1")
        WarehouseSnapshotRow.objects.create(snapshot=snap, location_code="B0-01-100A",
                                            is_empty=False, blocked_pick=True)
        ctx = context_for("co jest w B0-01-100A?", self.admin)
        self.assertIn("B0-01-100A", ctx)
        self.assertIn("zajęta", ctx)
        self.assertIn("blokada pobrania", ctx)

    def test_product_history_surfaced(self):
        p = Product.objects.create(code="ZR-1001", name="Kompresy")
        p.name = "Kompresy gazowe"            # druga rewizja → historia zmian
        p.save()
        ctx = context_for("historia ZR-1001?", self.admin)
        self.assertIn("Historia ZR-1001", ctx)

    def test_active_instruction_surfaced(self):
        p = Product.objects.create(code="ZR-1001", name="Kompresy")
        c = Carton.objects.create(name="K", length_cm=40, width_cm=30, height_cm=25,
                                  unit_weight_kg=0.5, pieces_per_carton=10)
        PalletizationInstruction.objects.create(
            product=p, version=2, is_active=True, unit_weight=0.5, pcs_per_carton=10,
            carton=c, carton_l=40, carton_w=30, carton_h=25,
            pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15,
            max_height_total_cm=180)
        ctx = context_for("jak paletyzować ZR-1001?", self.admin)
        self.assertIn("Instrukcja ZR-1001", ctx)
        self.assertIn("180", ctx)

    def test_recurring_quality_issues_aggregated(self):
        p = Product.objects.create(code="ZR-1001", name="Kompresy")
        sh = Shipment.objects.create()
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HU9")
        HandlingUnitItem.objects.create(hu=hu, product=p, ref_code="ZR-1001",
                                        expected_qty=1, unit="kar")
        for i in range(2):
            hu2 = HandlingUnit.objects.create(shipment=sh, seq=2 + i, code=f"HUx{i}")
            it2 = HandlingUnitItem.objects.create(hu=hu2, product=p, ref_code="ZR-1001",
                                                  expected_qty=1, unit="kar")
            HUQualityIssue.objects.create(hu=hu2, item=it2, issue_type="damaged")
        ctx = context_for("jakie błędy ma ZR-1001?", self.admin)
        self.assertIn("Powtarzające się błędy REF ZR-1001", ctx)
        self.assertIn("2× Uszkodzony towar", ctx)

    def test_customer_by_name_with_requirements(self):
        Customer.objects.create(name="Demoprime GmbH", country="DE",
                                requires_fumigated_pallet=True, max_pallet_height_cm=180)
        ctx = context_for("jakie wymagania ma Demoprime?", self.admin)
        self.assertIn("Demoprime", ctx)
        self.assertIn("fumigowana", ctx)
        self.assertIn("180", ctx)


class ZariaRagRoleFilterTests(TestCase):
    """AI-003: RAG dokleja tylko dane z modułów, które użytkownik może otworzyć w UI."""
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import Group, User
        from core.roles import GROUP_ADMIN, GROUP_WAREHOUSE
        Customer.objects.create(name="Demoprime GmbH", requires_fumigated_pallet=True)
        Product.objects.create(code="ZR-1001", name="Kompresy gazowe")
        cls.magazyn = User.objects.create_user("rag_mag", password="x")
        cls.magazyn.groups.add(Group.objects.get_or_create(name=GROUP_WAREHOUSE)[0])
        cls.admin = User.objects.create_user("rag_adm", password="x")
        cls.admin.groups.add(Group.objects.get_or_create(name=GROUP_ADMIN)[0])

    def test_magazyn_gets_no_customer_cards(self):
        self.assertNotIn("Demoprime", context_for("jakie wymagania ma Demoprime?", self.magazyn))

    def test_magazyn_still_gets_products_via_matinfo(self):
        self.assertIn("Kompresy gazowe", context_for("co to jest ZR-1001?", self.magazyn))

    def test_admin_gets_customer_cards(self):
        self.assertIn("Demoprime", context_for("jakie wymagania ma Demoprime?", self.admin))

    def test_no_user_is_fail_closed(self):
        self.assertEqual(context_for("co to jest ZR-1001?"), "")


class ZariaPiiMaskTests(TestCase):
    """AI-003: maskowanie PII tylko dla dostawcy chmurowego; kody HU/EAN/SSCC nietknięte."""
    MSG = "Kierowca Jan: +48 600 123 456, jan.kowal@firma.pl, PESEL 90010112345"

    def test_masks_phone_email_pesel_for_cloud(self):
        from ui.zaria_llm import _mask_payload
        msgs, system = _mask_payload("anthropic", [{"role": "user", "content": self.MSG}],
                                     "RAG: tel. 600123456")
        out = msgs[0]["content"]
        for label in ("[telefon]", "[e-mail]", "[PESEL]"):
            self.assertIn(label, out)
        self.assertNotIn("600 123 456", out)
        self.assertNotIn("firma.pl", out)
        self.assertNotIn("90010112345", out)
        self.assertEqual(system, "RAG: tel. [telefon]")

    def test_ollama_is_not_masked(self):
        from ui.zaria_llm import _mask_payload
        msgs, _ = _mask_payload("ollama", [{"role": "user", "content": self.MSG}], "")
        self.assertEqual(msgs[0]["content"], self.MSG)

    def test_codes_are_not_masked(self):
        from ui.zaria_llm import mask_pii
        for code in ("SSCC 359012345678901234", "EAN 5901234123457", "paleta HU123456789",
                     "kod ZR-12345678901", "(00)359012345678901234"):
            self.assertEqual(mask_pii(code), code)

    def test_sap_numbers_are_not_masked(self):
        from ui.zaria_llm import mask_pii
        for text in ("dostawa 800123456", "materiał 123456789", "klient 400100200"):
            self.assertEqual(mask_pii(text), text)

    def test_phone_forms_are_masked(self):
        from ui.zaria_llm import mask_pii
        self.assertEqual(mask_pii("tel. 600100200"), "tel. [telefon]")
        self.assertEqual(mask_pii("Kom: 600100200"), "Kom: [telefon]")
        for text in ("600 100 200", "600-100-200", "+48600100200", "0048 600 100 200",
                     "22 123 45 67"):
            self.assertEqual(mask_pii(text), "[telefon]", text)

    def test_llm_complete_sends_masked_but_db_keeps_original(self):
        from unittest.mock import patch
        from ui import zaria_llm
        model = type("M", (), {"provider": "anthropic"})()
        with patch.object(zaria_llm, "is_configured", return_value=True),              patch.object(zaria_llm, "_complete_anthropic", return_value=("ok", 1, 1)) as call:
            history = [{"role": "user", "content": self.MSG}]
            zaria_llm.complete(model, history, "sys")
        sent = call.call_args[0][1][0]["content"]
        self.assertIn("[telefon]", sent)
        self.assertEqual(history[0]["content"], self.MSG)   # oryginał nietknięty
