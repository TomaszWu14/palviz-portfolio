"""Importer migracji master daty (format SAP): produkty + instrukcje paletyzacji."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import Product, PalletizationInstruction
from ui.roles import GROUP_MASTER_DATA

# Header mirroring the real SAP migration export (duplicated "Ilość…" columns).
HEADER = (
    "ref_code;opis_pl;opis_en;Podstawowa jednostka miary;rodzina;Producent;TXT_SHORT_PL;"
    "sztuka_wymiar;sztuka_ean;Ilość podstawowej jednostki miary;sztuka_artwork_ref;"
    "op_wymiar;op_ean;Ilość podstawowej jednostki miary;op_artwork_ref;"
    "opz_wymiar;opz_ean;Ilość podstawowej jednostki miary;opz_artwork_ref;"
    "karton_wymiar;karton_ean;Ilość podstawowej jednostki miary;karton_artwork_ref"
)


def _row(code, opis, szt_dim, kar_dim, kar_qty, producent="O013",
         op_dim="", op_qty="", opz_dim="", opz_qty=""):
    cols = [""] * 23
    cols[0] = code; cols[1] = opis; cols[4] = "01: OPATRUNKI"; cols[5] = producent; cols[6] = opis
    cols[7] = szt_dim
    cols[11] = op_dim; cols[13] = op_qty
    cols[15] = opz_dim; cols[17] = opz_qty
    cols[19] = kar_dim; cols[21] = kar_qty
    return ";".join(cols)


class MasterDataImportTests(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_user(username="md", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)

    def _import(self, *rows):
        content = (HEADER + "\n" + "\n".join(rows)).encode("utf-8")
        f = SimpleUploadedFile("migracja.csv", content, content_type="text/csv")
        return self.client.post(reverse("ui:planner_master_data_import"), {"file": f})

    def test_creates_products_and_instructions(self):
        resp = self._import(
            _row("070313P-10-S", "Szczoteczka", "8 X 8 X 10 cm", "40,7 X 28,1 X 60,5 cm", "750"),
            _row("NO-DIMS", "Bez wymiarów", "0 X 0 X 0 cm", "", ""),
        )
        self.assertEqual(resp.status_code, 302)

        # product with unit dims + carton
        p = Product.objects.get(code="070313P-10-S")
        self.assertEqual((p.unit_length_cm, p.unit_width_cm, p.unit_height_cm), (8.0, 8.0, 10.0))
        self.assertEqual(p.supplier_short, "O013")

        instr = PalletizationInstruction.objects.get(product=p, name="Import migracji")
        self.assertEqual((instr.carton_l, instr.carton_w, instr.carton_h), (41, 28, 61))  # rounded up
        self.assertEqual(instr.pcs_per_carton, 750)
        self.assertEqual(instr.unit_weight, 0.001)        # negligible → height-limited

        # product without carton dims → no instruction
        p2 = Product.objects.get(code="NO-DIMS")
        self.assertFalse(p2.instructions.exists())

    def test_hierarchy_view_lazily_computes_layout(self):
        self._import(_row("L-04", "Produkt", "10 X 10 X 10 cm", "45 X 29 X 63 cm", "50"))
        p = Product.objects.get(code="L-04")
        instr = p.instructions.first()
        self.assertFalse(instr.layouts)                   # importer leaves layouts empty
        resp = self.client.get(reverse("ui:planner_product_hierarchy", args=[p.pk]))
        self.assertEqual(resp.status_code, 200)
        instr.refresh_from_db()
        self.assertTrue(instr.layouts)                    # computed on first view

    def test_inner_pack_from_opz_linked_to_instruction(self):
        from ui.models import InnerPack
        self._import(_row(
            "OPZ-1", "Produkt", "2 X 2 X 5 cm", "40 X 30 X 25 cm", "240",
            op_dim="6 X 4 X 5 cm", op_qty="12", opz_dim="20 X 15 X 11 cm", opz_qty="36"))
        instr = Product.objects.get(code="OPZ-1").instructions.get(name="Import migracji")
        ip = instr.inner_pack
        self.assertIsNotNone(ip)
        self.assertEqual((ip.length_cm, ip.width_cm, ip.height_cm), (20.0, 15.0, 11.0))
        self.assertEqual(ip.units_per_pack, 36)                    # sztuk w opz
        self.assertEqual((ip.sales_unit_l_cm, ip.sales_unit_w_cm, ip.sales_unit_h_cm), (6.0, 4.0, 5.0))
        self.assertEqual(ip.sales_units_per_pack, 3)               # op w opz = 36/12
        self.assertEqual(instr.pcs_per_inner_pack, 36)
        self.assertEqual(instr.packs_per_carton, 7)                # opz w kartonie = round(240/36)
        self.assertEqual(InnerPack.objects.filter(name="OPZ-1-opz").count(), 1)

    def test_reimport_refreshes_without_duplicating(self):
        self._import(_row("R-1", "v1", "5 X 5 X 5 cm", "30 X 20 X 20 cm", "10"))
        self._import(_row("R-1", "v2", "5 X 5 X 5 cm", "30 X 20 X 20 cm", "12"))
        p = Product.objects.get(code="R-1")
        self.assertEqual(p.name, "v2")
        instrs = p.instructions.filter(name="Import migracji")
        self.assertEqual(instrs.count(), 1)               # refreshed, not duplicated
        self.assertEqual(instrs.first().pcs_per_carton, 12)

    def test_long_codes_truncating_to_same_value_do_not_crash(self):
        base = "A" * 50
        resp = self._import(
            _row(base + "X1", "Pierwszy", "5 X 5 X 5 cm", "30 X 20 X 20 cm", "10"),
            _row(base + "Y2", "Drugi", "5 X 5 X 5 cm", "30 X 20 X 20 cm", "12"),
        )
        self.assertEqual(resp.status_code, 302)
        # Both collapse to the same 50-char code → exactly one product, no UNIQUE error.
        self.assertEqual(Product.objects.filter(code=base).count(), 1)

    def test_overwrite_clears_stale_imports(self):
        from ui.models import InnerPack
        # First base: two products, one with an opz inner pack.
        self._import(
            _row("OLD-1", "stary", "5 X 5 X 5 cm", "30 X 20 X 20 cm", "10"),
            _row("OLD-2", "stary opz", "2 X 2 X 5 cm", "40 X 30 X 25 cm", "240",
                 op_dim="6 X 4 X 5 cm", op_qty="12", opz_dim="20 X 15 X 11 cm", opz_qty="36"))
        self.assertEqual(InnerPack.objects.filter(name__endswith="-opz").count(), 1)
        # New base with "Nadpisz" — only NEW-1 should keep an import instruction.
        content = (HEADER + "\n" + _row("NEW-1", "nowy", "5 X 5 X 5 cm", "30 X 20 X 20 cm", "8")).encode("utf-8")
        f = SimpleUploadedFile("m.csv", content, content_type="text/csv")
        resp = self.client.post(reverse("ui:planner_master_data_import"),
                                {"file": f, "overwrite": "1"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(PalletizationInstruction.objects.filter(name="Import migracji").count(), 1)
        self.assertFalse(InnerPack.objects.filter(name__endswith="-opz").exists())  # opz wiped
        self.assertTrue(Product.objects.get(code="NEW-1").instructions.exists())

    def test_purge_inner_packs_and_instructions(self):
        from ui.models import InnerPack
        self._import(_row(
            "PG-1", "Produkt", "2 X 2 X 5 cm", "40 X 30 X 25 cm", "240",
            op_dim="6 X 4 X 5 cm", op_qty="12", opz_dim="20 X 15 X 11 cm", opz_qty="36"))
        self.assertTrue(InnerPack.objects.filter(name__endswith="-opz").exists())

        # Delete just the opz inner packs.
        self.client.post(reverse("ui:planner_master_data_purge"), {"scope": "inner_packs"})
        self.assertFalse(InnerPack.objects.filter(name__endswith="-opz").exists())
        self.assertTrue(PalletizationInstruction.objects.filter(name="Import migracji").exists())

        # Delete the import instructions; product stays.
        self.client.post(reverse("ui:planner_master_data_purge"), {"scope": "instructions"})
        self.assertFalse(PalletizationInstruction.objects.filter(name="Import migracji").exists())
        self.assertTrue(Product.objects.filter(code="PG-1").exists())

        # Delete all products.
        self.client.post(reverse("ui:planner_master_data_purge"), {"scope": "products"})
        self.assertFalse(Product.objects.exists())


# Header mirroring the SAP MARM export: one row per unit of measure, grouped by material.
MARM_HEADER = (
    "Materiał;Alternatywna jednostka miary;Mianownik;Licznik;"
    "Długość;Szerokość;Wysokość;Waga brutto;Kod EAN/UPC"
)


def _marm(mat, ajm, mian, licz, l="", w="", h="", brutto="", ean=""):
    return ";".join(str(x) for x in [mat, ajm, mian, licz, l, w, h, brutto, ean])


class MarmImportTests(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_user(username="marm", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)

    def _import(self, *rows):
        content = (MARM_HEADER + "\n" + "\n".join(rows)).encode("utf-8")
        f = SimpleUploadedFile("marm.csv", content, content_type="text/csv")
        return self.client.post(reverse("ui:planner_master_data_import"), {"file": f})

    def test_marm_groups_uoms_into_product_and_instruction(self):
        resp = self._import(
            _marm("MAT-100", "SZT", "1", "1", "8", "8", "10", "0,05", "5901234123457"),
            _marm("MAT-100", "KAR", "1", "750", "40,7", "28,1", "60,5", "37,5"),
            _marm("MAT-100", "PAZ", "1", "15000", "120", "80", "180", "900"),
        )
        self.assertEqual(resp.status_code, 302)

        p = Product.objects.get(code="MAT-100")
        self.assertEqual((p.unit_length_cm, p.unit_width_cm, p.unit_height_cm), (8.0, 8.0, 10.0))
        self.assertEqual(p.ean, "5901234123457")

        instr = PalletizationInstruction.objects.get(product=p, name="Import migracji")
        self.assertEqual((instr.carton_l, instr.carton_w, instr.carton_h), (41, 28, 61))
        self.assertEqual(instr.pcs_per_carton, 750)
        self.assertEqual(instr.unit_weight, 0.05)           # per-piece waga brutto
        self.assertEqual(instr.max_height_total_cm, 180)    # z PAZ
        self.assertEqual(instr.max_weight_kg, 900)          # z PAZ
        self.assertEqual(instr.demand_pcs, 15000)           # sztuk na palecie

    def test_marm_ju_factor_splits_piece_into_usable_units(self):
        # DMOM10001-style: JU row encodes 100 JU per base unit (Mian=100, Licznik=1).
        resp = self._import(
            _marm("DMOM10001", "JU", "100", "1"),                       # 100 JU = 1 jedn. bazowa
            _marm("DMOM10001", "OP", "1", "1.991", "21", "12", "5,5", "0,458", "5900000001609"),
            _marm("DMOM10001", "KAR", "1", "10", "29", "25", "22,2", "4,583", "5900000001500"),
            _marm("DMOM10001", "PAZ", "1", "1080", "120", "80", "215", "509,964"),
        )
        self.assertEqual(resp.status_code, 302)
        instr = Product.objects.get(code="DMOM10001").instructions.get(name="Import migracji")
        self.assertEqual(instr.units_per_piece, 100)
        self.assertEqual(instr.pcs_per_carton, 10)                      # nadal w jedn. bazowych

        # Hierarchy view exposes JU counts (×100) and a dedicated JU level.
        resp = self.client.get(reverse("ui:planner_product_hierarchy", args=[instr.product_id]))
        self.assertEqual(resp.status_code, 200)
        titles = [lv["title"] for lv in resp.context["levels"]]
        self.assertIn("Sztuka (JU)", titles)
        self.assertIn("OP (opakowanie)", titles)
        summary = resp.context["summary"]
        # Counted in OP (smallest used unit), never ×JU: cartons/pallet × 10.
        self.assertEqual(summary["pallet_unit"], "OP")
        self.assertTrue(summary["pcs_per_pallet"] and summary["pcs_per_pallet"] % 10 == 0)

    def test_marm_builds_inner_pack_from_opz(self):
        self._import(
            _marm("MAT-200", "SZT", "1", "1", "2", "2", "5", "0,01"),
            _marm("MAT-200", "OP", "1", "12", "6", "4", "5"),
            _marm("MAT-200", "OPZ", "1", "36", "20", "15", "11"),
            _marm("MAT-200", "KAR", "1", "240", "40", "30", "25"),
        )
        instr = Product.objects.get(code="MAT-200").instructions.get(name="Import migracji")
        ip = instr.inner_pack
        self.assertIsNotNone(ip)
        self.assertEqual((ip.length_cm, ip.width_cm, ip.height_cm), (20.0, 15.0, 11.0))
        self.assertEqual(ip.units_per_pack, 36)
        self.assertEqual(ip.sales_units_per_pack, 3)        # op w opz = 36/12
        self.assertEqual(instr.packs_per_carton, 7)         # round(240/36)

    def test_marm_material_without_piece_row(self):
        # A material may lack a SZT row (only OP/KAR) — must not crash on None rows.
        resp = self._import(
            _marm("MAT-300", "OP", "1", "10", "6", "4", "5", "0,2"),
            _marm("MAT-300", "KAR", "1", "100", "40", "30", "25", "20"),
        )
        self.assertEqual(resp.status_code, 302)
        p = Product.objects.get(code="MAT-300")
        instr = PalletizationInstruction.objects.get(product=p, name="Import migracji")
        self.assertEqual(instr.pcs_per_carton, 100)
        self.assertEqual((instr.carton_l, instr.carton_w, instr.carton_h), (40, 30, 25))
