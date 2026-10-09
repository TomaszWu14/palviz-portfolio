"""Behaviour tests for two previously-untested areas:
  - shipment creation with manual line rows (incl. the regression for the zip()
    truncation fix — mismatched parallel lists must not drop rows),
  - the manual palletization calculator endpoint returning a rendered panel."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import (
    Product, Shipment, ShipmentLine, PalletizationInstruction, Customer,
)
from ui.roles import ALL_GROUPS
from ui.views.core.helpers import _calc_shipment_data


class CustomerRequirementsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        p = Product.objects.create(code="HVY", name="Heavy")
        # 50 kg per carton → a load whose pallet count is driven by weight, not volume.
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=50, pcs_per_carton=1, demand_pcs=100, is_active=True)
        cls.cust = Customer.objects.create(name="Nordmed", code="C1",
                                           max_pallet_weight_kg=200, requires_adr=True,
                                           temp_control="+2…+8°C", pallet_type="euro")
        cls.sh = Shipment.objects.create(name="Ciężka", customer=cls.cust)
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=20, unit="kar")  # ~1000 kg

    def test_customer_weight_cap_raises_pallet_count(self):
        calc = _calc_shipment_data(self.sh, stow_eff=95)
        self.assertTrue(calc["weight_capped"])
        self.assertEqual(calc["pallet_weight_cap_kg"], 200)
        # 1000 kg / 200 kg-per-pallet = 5 by weight, which dominates the light-volume estimate.
        self.assertGreaterEqual(calc["scenarios"][0]["n_pallets"], 5)

    def test_requirement_summary_lists_all_rules(self):
        summ = self.cust.requirement_summary()
        joined = " | ".join(summ)
        self.assertIn("ADR", joined)
        self.assertIn("+2…+8°C", joined)
        self.assertIn("maks. waga palety 200 kg", joined)
        self.assertIn("Euro", joined)


def _user_all_roles():
    u = get_user_model().objects.create_user(username="tester", password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class ShipmentCreateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.p1 = Product.objects.create(code="P1", name="Produkt 1")
        cls.p2 = Product.objects.create(code="P2", name="Produkt 2")

    def setUp(self):
        self.client.force_login(self.user)

    def _post(self, name, **extra):
        # stowage_efficiency_pct mirrors the real form (pre-filled with the model default)
        data = {"name": name, "status": "draft", "stowage_efficiency_pct": 80}
        data.update(extra)
        return self.client.post(reverse("ui:planner_shipment_new"), data)

    def test_create_with_two_lines(self):
        resp = self._post(
            "WYS-1",
            line_product=[str(self.p1.pk), str(self.p2.pk)],
            line_quantity=["5", "3"],
            line_unit=["kar", "pal"],
            line_notes=["a", "b"],
        )
        self.assertEqual(resp.status_code, 302)
        sh = Shipment.objects.get(name="WYS-1")
        lines = list(sh.lines.order_by("order"))
        self.assertEqual(len(lines), 2)
        self.assertEqual((lines[0].product_id, lines[0].quantity, lines[0].unit), (self.p1.pk, 5.0, "kar"))
        self.assertEqual((lines[1].product_id, lines[1].quantity, lines[1].unit), (self.p2.pk, 3.0, "pal"))

    def test_fewer_units_than_products_keeps_all_rows(self):
        """Regression: zip(product_ids, quantities, units) used to truncate to the
        shortest list, silently dropping trailing rows. Defensive indexing keeps them."""
        resp = self._post(
            "WYS-2",
            line_product=[str(self.p1.pk), str(self.p2.pk)],
            line_quantity=["5", "3"],
            line_unit=["kar"],          # one unit short
        )
        self.assertEqual(resp.status_code, 302)
        sh = Shipment.objects.get(name="WYS-2")
        self.assertEqual(sh.lines.count(), 2)                 # both rows kept
        self.assertEqual(sh.lines.get(product=self.p2).unit, "kar")   # missing unit → default

    def test_missing_instruction_flagged_as_underquote(self):
        """A line whose product has no palletization instruction must be flagged
        (has_missing + listed) so the quote isn't silently under-counted."""
        sh = Shipment.objects.create(name="WYS-M", status="draft")
        ShipmentLine.objects.create(shipment=sh, product=self.p1, quantity=5, unit="kar", order=0)
        calc = _calc_shipment_data(sh, with_packing=False)
        self.assertTrue(calc["has_missing"])
        self.assertEqual([m["code"] for m in calc["missing_products"]], ["P1"])
        self.assertEqual(calc["total_cartons"], 0)   # nothing counted (the risk we now surface)
        # The hard warning must render on the shipment detail screen.
        resp = self.client.get(reverse("ui:planner_shipment_detail", args=[sh.pk]))
        self.assertContains(resp, "Wycena zaniżona")

    def test_blank_and_zero_rows_skipped(self):
        resp = self._post(
            "WYS-3",
            line_product=["", str(self.p1.pk), str(self.p2.pk)],
            line_quantity=["9", "0", "4"],   # first has no product, second qty 0
            line_unit=["kar", "kar", "kar"],
        )
        self.assertEqual(resp.status_code, 302)
        sh = Shipment.objects.get(name="WYS-3")
        self.assertEqual(sh.lines.count(), 1)
        self.assertEqual(sh.lines.get().product_id, self.p2.pk)

    def test_failed_excel_import_does_not_wipe_existing_lines(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self._post("KEEP", line_product=[str(self.p1.pk)], line_quantity=["5"], line_unit=["kar"])
        sh = Shipment.objects.get(name="KEEP")
        self.assertEqual(sh.lines.count(), 1)
        # Re-save with a broken "Excel" → import raises, atomic rolls back, line stays.
        bad = SimpleUploadedFile("x.xlsx", b"not really xlsx", content_type="application/vnd.ms-excel")
        resp = self.client.post(reverse("ui:planner_shipment_edit", args=[sh.pk]),
                                {"name": "KEEP", "status": "draft",
                                 "stowage_efficiency_pct": 80, "excel_file": bad})
        self.assertEqual(resp.status_code, 200)          # re-rendered form with error
        self.assertEqual(sh.lines.count(), 1)            # NOT wiped


class ShipmentFileImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        Product.objects.create(code="DMOM10001", name="Rękawice M")
        Product.objects.create(code="DMOL10001", name="Rękawice L")

    def setUp(self):
        self.client.force_login(self.user)

    def _import(self, body):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile("dostawy.csv", body.encode("utf-8"), content_type="text/csv")
        return self.client.post(reverse("ui:planner_shipments_import"), {"file": f})

    def test_groups_rows_by_document_into_shipments(self):
        body = (
            "Dokument;Produkt;Ilość;JS\n"
            "81789764;DMOM10001;200;OP\n"
            "81789764;DMOL10001;100;OP\n"
            "81789765;DMOM10001;50;KAR\n"
        )
        resp = self._import(body)
        self.assertEqual(resp.status_code, 302)
        s1 = Shipment.objects.get(name="Dostawa 81789764")
        self.assertEqual(s1.lines.count(), 2)
        line = s1.lines.get(product__code="DMOM10001")
        self.assertEqual((line.quantity, line.unit), (200.0, "szt"))   # OP → szt
        s2 = Shipment.objects.get(name="Dostawa 81789765")
        self.assertEqual(s2.lines.get().unit, "kar")                   # KAR → kar

    _HDR = ("Dokument;Produkt;Ilość;JS;Odbiorca materiałów;Opis odbiorcy materiałów;"
            "Miejscowość;Kod pocztowy;Wymagania klienta\n")

    def test_dost_prefixed_layout_with_country(self):
        # New SAP layout: "Dost."-prefixed headers + "Klucz kraju/regionu" (ISO country).
        body = (
            "Dokument;Produkt;Ilość;Jednostka miary;Dost.Odbiorca materiałów;"
            "Dost.Opis odbiorcy materiałów;Dost.Utworz. dnia;Dost.Dział sprzedaży;Dost.Autor;"
            "Dost.Kod pocztowy;Dost.Klucz kraju/regionu;Dost.Nazwa odbiorcy 1;Dost.Liczba jednostek obsługi\n"
            "81772168;DMOM10001;600;SZT;11132232;Nordmed ehf.;17.06.2026;DS03;ADEMOWY;203;IS;Nordmed ehf.;2\n"
        )
        self._import(body)
        sh = Shipment.objects.get(name="Dostawa 81772168")
        self.assertEqual(sh.destination_country, "IS")          # country captured for zones
        self.assertEqual(sh.recipient_name, "Nordmed ehf.")
        self.assertEqual(sh.destination_postal, "203")
        self.assertEqual(sh.author, "ADEMOWY")
        self.assertEqual(sh.lines.get().unit, "szt")            # SZT → szt

    def test_captures_client_requirements(self):
        body = self._HDR + "81785384;DMOM10001;2500;SZT;21131719;MSF SUPPLY ASBL;Neder;1120;Paleta EURO\n"
        self._import(body)
        sh = Shipment.objects.get(name="Dostawa 81785384")
        self.assertEqual(sh.client_requirements, "Paleta EURO")
        self.assertEqual(sh.recipient_name, "MSF SUPPLY ASBL")

    def test_consolidate_merges_same_recipient(self):
        # Two documents → same recipient (21131719). With consolidation = ONE shipment.
        body = (self._HDR +
                "81785384;DMOM10001;2500;SZT;21131719;MSF SUPPLY ASBL;Neder;1120;Paleta EURO\n"
                "81785385;DMOL10001;5525;SZT;21131719;MSF SUPPLY ASBL;Neder;1120;Paleta EURO\n")
        f = SimpleUploadedFile("d.csv", body.encode("utf-8"), content_type="text/csv")
        self.client.post(reverse("ui:planner_shipments_import"), {"file": f, "consolidate": "1"})
        cons = Shipment.objects.filter(name__startswith="Konsolidacja")
        self.assertEqual(cons.count(), 1)
        sh = cons.get()
        self.assertEqual(sh.lines.count(), 2)                       # both products, one shipment
        self.assertEqual(sh.client_requirements, "Paleta EURO")
        self.assertIn("81785384", sh.notes)
        self.assertIn("81785385", sh.notes)

    def test_without_consolidate_keeps_separate(self):
        body = (self._HDR +
                "81785384;DMOM10001;2500;SZT;21131719;MSF SUPPLY ASBL;Neder;1120;Paleta EURO\n"
                "81785385;DMOL10001;5525;SZT;21131719;MSF SUPPLY ASBL;Neder;1120;Paleta EURO\n")
        self._import(body)                                          # no consolidate
        self.assertEqual(Shipment.objects.filter(name__startswith="Dostawa").count(), 2)

    def test_multi_file_upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f1 = SimpleUploadedFile("a.csv", (self._HDR + "111;DMOM10001;10;KAR;C1;Klient A;X;00;\n").encode(), content_type="text/csv")
        f2 = SimpleUploadedFile("b.csv", (self._HDR + "222;DMOL10001;20;KAR;C2;Klient B;Y;00;\n").encode(), content_type="text/csv")
        self.client.post(reverse("ui:planner_shipments_import"), {"file": [f1, f2]})
        self.assertTrue(Shipment.objects.filter(name="Dostawa 111").exists())
        self.assertTrue(Shipment.objects.filter(name="Dostawa 222").exists())

    def test_unknown_code_skipped_no_empty_shipment(self):
        body = "Dokument;Produkt;Ilość;JS\n90000001;NIEMA;5;OP\n"
        resp = self._import(body)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Shipment.objects.filter(name="Dostawa 90000001").exists())

    def test_author_and_email_captured_from_trailing_columns(self):
        body = (
            "Dokument;Produkt;Ilość;Jednostka miary;Autor;Mail\n"
            "81789764;DMOM10001;200;OP;Jan Kowalski;jan@firma.pl\n"
            "81789764;DMOL10001;100;OP;Jan Kowalski;jan@firma.pl\n"
        )
        resp = self._import(body)
        self.assertEqual(resp.status_code, 302)
        s = Shipment.objects.get(name="Dostawa 81789764")
        self.assertEqual(s.author, "Jan Kowalski")
        self.assertEqual(s.author_email, "jan@firma.pl")
        self.assertEqual(s.lines.get(product__code="DMOM10001").source_unit, "OP")

    def test_destination_and_maps_url_from_file(self):
        body = (
            "Dokument;Produkt;Ilość;Jednostka miary;Opis odbiorcy materiałów;Miejscowość;Kod pocztowy\n"
            "81782091;DMOM10001;50;SZT;PHARMO DEMO SRL;VALDEMO;25020\n"
        )
        resp = self._import(body)
        self.assertEqual(resp.status_code, 302)
        s = Shipment.objects.get(name="Dostawa 81782091")
        self.assertEqual(s.recipient_name, "PHARMO DEMO SRL")
        self.assertEqual(s.destination_city, "VALDEMO")
        self.assertEqual(s.destination_postal, "25020")
        self.assertIn("google.com/maps", s.google_maps_url())
        self.assertIn("VALDEMO", s.google_maps_url())

    def test_recipient_number_seeds_and_links_customer(self):
        from ui.models import Customer
        body = (
            "Dokument;Produkt;Ilość;JS;Odbiorca materiałów;Nazwa odbiorcy\n"
            "81782091;DMOM10001;50;OP;100123;PHARMO DEMO SRL\n"
        )
        resp = self._import(body)
        self.assertEqual(resp.status_code, 302)
        s = Shipment.objects.get(name="Dostawa 81782091")
        self.assertIsNotNone(s.customer)
        self.assertEqual(s.customer.code, "100123")
        self.assertEqual(s.customer.name, "PHARMO DEMO SRL")
        self.assertEqual(s.customer.kind, "consignee")
        self.assertEqual(s.recipient_name, "PHARMO DEMO SRL")
        # re-importing the same recipient number links to the same customer (no duplicate)
        Shipment.objects.filter(name="Dostawa 81782091").delete()
        self._import(body.replace("81782091", "81782092"))
        self.assertEqual(Customer.objects.filter(code="100123").count(), 1)
        s2 = Shipment.objects.get(name="Dostawa 81782092")
        self.assertEqual(s2.customer.code, "100123")

    def test_recipient_number_only_does_not_become_name(self):
        from ui.models import Customer
        # Only the number column present → recipient_name must not be the bare number,
        # and the seeded consignee falls back to the code as a placeholder name.
        body = ("Dokument;Produkt;Ilość;JS;Odbiorca materiałów\n"
                "81789764;DMOM10001;10;OP;100777\n")
        self._import(body)
        s = Shipment.objects.get(name="Dostawa 81789764")
        self.assertEqual(s.recipient_name, "")          # not "100777"
        self.assertEqual(s.customer.code, "100777")
        self.assertTrue(Customer.objects.filter(code="100777").exists())

    def test_recipient_name_with_spaces_not_treated_as_code(self):
        from ui.models import Customer
        # A name accidentally landing in the number column (has spaces) must not seed a
        # junk customer keyed by the name.
        body = ("Dokument;Produkt;Ilość;JS;Odbiorca materiałów\n"
                "81789764;DMOM10001;10;OP;PHARMO DEMO SRL\n")
        self._import(body)
        s = Shipment.objects.get(name="Dostawa 81789764")
        self.assertIsNone(s.customer)
        self.assertEqual(s.recipient_name, "PHARMO DEMO SRL")
        self.assertFalse(Customer.objects.filter(code="PHARMO DEMO SRL").exists())

    def test_recipient_number_links_existing_customer(self):
        from ui.models import Customer
        existing = Customer.objects.create(code="555", name="ACME", kind="customer")
        body = ("Dokument;Produkt;Ilość;JS;Odbiorca materiałów\n"
                "81789764;DMOM10001;10;OP;555\n")
        self._import(body)
        s = Shipment.objects.get(name="Dostawa 81789764")
        self.assertEqual(s.customer_id, existing.id)
        self.assertEqual(Customer.objects.filter(code="555").count(), 1)

    def test_repeated_product_in_document_sums_into_one_line(self):
        # Same product twice in one document must not hit the unique constraint.
        body = (
            "Dokument;Produkt;Ilość;JS\n"
            "81789764;DMOM10001;200;OP\n"
            "81789764;DMOM10001;50;OP\n"
        )
        resp = self._import(body)
        self.assertEqual(resp.status_code, 302)
        s = Shipment.objects.get(name="Dostawa 81789764")
        self.assertEqual(s.lines.count(), 1)
        self.assertEqual(s.lines.get().quantity, 250.0)        # 200 + 50


class ShipmentMixedPackingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.sh = Shipment.objects.create(name="MIX", stowage_efficiency_pct=80)
        # Two SKUs, same carton size, each with an instruction so they pack.
        for code, qty in (("A1", 30), ("B1", 30)):
            p = Product.objects.create(code=code, name=code)
            PalletizationInstruction.objects.create(
                product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
                unit_weight=0.5, pcs_per_carton=1, demand_pcs=100, is_active=True)
            ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=qty, unit="kar")

    def test_pack_keeps_every_carton_and_rests_on_support(self):
        from ui.views.core.helpers import _calc_shipment_data, _build_shipment_three_data
        calc = _calc_shipment_data(self.sh, stow_eff=80)
        data = _build_shipment_three_data(calc, max_h=180)
        self.assertIsNotNone(data)
        boxes = [b for p in data["pallets"] for b in p["boxes"]]
        self.assertEqual(len(boxes), calc["total_cartons"])     # nothing dropped
        self.assertEqual({b["label"] for b in boxes}, {"A1", "B1"})
        # No floating: every box bottom sits at the pallet base or on another box top.
        PB = data["pallet_base"]
        for p in data["pallets"]:
            tops = set()
            for b in p["boxes"]:
                bottom = round(b["pos"][1] - b["dims"][2] / 2, 1)
                self.assertTrue(bottom == PB or any(abs(bottom - t) < 0.5 for t in tops),
                                f"box floating at bottom={bottom}")
                tops.add(round(b["pos"][1] + b["dims"][2] / 2, 1))

    def test_multi_pallet_when_volume_exceeds_one(self):
        from ui.views.core.helpers import _calc_shipment_data, _build_shipment_three_data
        # Enough cartons to need several pallets → multiple filled pallets.
        big = Product.objects.create(code="BIG", name="BIG")
        PalletizationInstruction.objects.create(
            product=big, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=1, demand_pcs=100, is_active=True)
        ShipmentLine.objects.create(
            shipment=self.sh, product=big, quantity=300, unit="kar", order=9)
        calc = _calc_shipment_data(self.sh, stow_eff=80)
        data = _build_shipment_three_data(calc, max_h=180)
        self.assertGreater(data["n_pallets"], 1)
        self.assertEqual(sum(len(p["boxes"]) for p in data["pallets"]), calc["total_cartons"])

    def test_full_pallet_sku_kept_on_its_own_pallet(self):
        from ui.views.core.helpers import _calc_shipment_data, _build_shipment_three_data
        # BIG fills >1 pallet on its own; a tiny SKU shares space elsewhere.
        big = Product.objects.create(code="BIG", name="BIG")
        PalletizationInstruction.objects.create(
            product=big, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=1, demand_pcs=100, is_active=True)
        ShipmentLine.objects.create(shipment=self.sh, product=big, quantity=200, unit="kar", order=9)
        calc = _calc_shipment_data(self.sh, stow_eff=80)
        data = _build_shipment_three_data(calc, max_h=180)
        # At least one pallet is pure BIG (a full original pallet, not scattered).
        pure = [p for p in data["pallets"]
                if p["boxes"] and all(b["label"] == "BIG" for b in p["boxes"])]
        self.assertTrue(pure)

    def test_same_sku_kept_together_not_scattered(self):
        from ui.views.core.helpers import _calc_shipment_data, _build_shipment_three_data
        # Two SKUs, each spanning multiple pallets. With SKU-grouped packing only the
        # boundary pallet may mix them — the rest are single-SKU (not scattered).
        for code in ("AAA", "BBB"):
            p = Product.objects.create(code=code, name=code)
            PalletizationInstruction.objects.create(
                product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
                unit_weight=0.5, pcs_per_carton=1, demand_pcs=100, is_active=True)
            ShipmentLine.objects.create(shipment=self.sh, product=p, quantity=120, unit="kar", order=9)
        calc = _calc_shipment_data(self.sh, stow_eff=80)
        data = _build_shipment_three_data(calc, max_h=180)
        mixed = [p for p in data["pallets"]
                 if len({b["label"] for b in p["boxes"] if b["label"] in ("AAA", "BBB")}) > 1]
        self.assertLessEqual(len(mixed), 1)

    def test_py3dbp_optimizer_packs_without_dropping(self):
        from ui.views.core.helpers import _calc_shipment_data, _build_shipment_three_data
        calc = _calc_shipment_data(self.sh, stow_eff=80)
        data = _build_shipment_three_data(calc, max_h=180)
        self.assertIsNotNone(data)
        self.assertEqual(sum(len(p["boxes"]) for p in data["pallets"]), calc["total_cartons"])
