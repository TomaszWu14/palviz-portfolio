"""HU import from a PowerBI/SAP export (pickHU, REF, LOT, EXP, qty, location)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import Product, Shipment, HandlingUnit
from ui.roles import ALL_GROUPS


def _user():
    u = get_user_model().objects.create_user(username="imp", password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


CSV = (
    "Dostawa;pickHU;REF;Opis;LOT;Data ważności;Ilość;JM;Lokalizacja;Typ magazynu;Odbiorca\n"
    "81782091;HU001;RG-50;Rękawice;LOT123;2027-05-31;80;OP;A-12-3;WT01;PHARMO\n"
    "81782091;HU001;NL100;Nonvi;LOT999;2026-12-01;12;KAR;A-12-3;WT01;PHARMO\n"
    "81782091;HU002;RG-50;Rękawice;LOT777;2028-01-15;40;OP;B-01-1;WT01;PHARMO\n"
)


class HUImportTests(TestCase):
    def setUp(self):
        self.client.force_login(_user())
        Product.objects.create(code="RG-50", name="Rękawice")

    def _upload(self, text=CSV):
        f = SimpleUploadedFile("hu.csv", text.encode("utf-8"), content_type="text/csv")
        return self.client.post(reverse("ui:planner_hu_import"), {"file": f})

    def test_import_creates_hus_and_items_with_lot_expiry(self):
        resp = self._upload()
        self.assertEqual(resp.status_code, 302)
        sh = Shipment.objects.get(name="81782091")
        self.assertEqual(sh.handling_units.count(), 2)
        hu1 = HandlingUnit.objects.get(shipment=sh, code="HU001")
        self.assertEqual(hu1.location, "A-12-3")
        self.assertEqual(hu1.warehouse_type, "WT01")
        self.assertEqual(hu1.items.count(), 2)
        it = hu1.items.get(ref_code="RG-50")
        self.assertEqual(it.lot, "LOT123")
        self.assertEqual(str(it.expiry), "2027-05-31")
        self.assertEqual(it.alt_qty, 80)
        self.assertEqual(it.product.code, "RG-50")        # linked to existing product

    def test_stock_status_column_imported(self):
        text = (
            "Dostawa;pickHU;REF;Ilość;JM;Lokalizacja;Typ magazynu;Status zapasu\n"
            "81782091;HU050;RG-50;10;OP;C-01-1;0010;B6\n"
        )
        self._upload(text)
        hu = HandlingUnit.objects.get(code="HU050")
        self.assertEqual(hu.stock_status, "B6")

    def test_stock_status_absent_stays_blank(self):
        self._upload()                                     # bazowy CSV bez kolumny statusu
        self.assertEqual(HandlingUnit.objects.get(code="HU001").stock_status, "")

    def test_reimport_replaces_items(self):
        self._upload()
        self._upload()                                     # second import
        sh = Shipment.objects.get(name="81782091")
        # Items replaced, not duplicated.
        self.assertEqual(HandlingUnit.objects.get(shipment=sh, code="HU001").items.count(), 2)

    def test_warehouse_type_filled_from_later_row(self):
        # First row of a pickHU has a blank Typ magazynu; a later row carries it. The HU
        # must still end up tagged so the warehouse-type filter can find it.
        text = (
            "Dostawa;pickHU;REF;Ilość;JM;Lokalizacja;Typ magazynu\n"
            "81782091;HU009;RG-50;10;OP;;\n"
            "81782091;HU009;RG-50;5;OP;A-99-9;WCGL\n"
        )
        self._upload(text)
        hu = HandlingUnit.objects.get(shipment__name="81782091", code="HU009")
        self.assertEqual(hu.warehouse_type, "WCGL")
        self.assertEqual(hu.location, "A-99-9")


POWERBI = (
    "Stock_oraz_DLT[Produkt];Stock_oraz_DLT[Miejsce składowania];Stock_oraz_DLT[Jednostka obsługi];"
    "Stock_oraz_DLT[Podst. jedn. miary];Stock_oraz_DLT[Termin ważności];Stock_oraz_DLT[Rodzaj zapasów];"
    "Stock_oraz_DLT[Partia];Stock_oraz_DLT[Typ dokumentu];Stock_oraz_DLT[Typ magazynu];[SumDostępna_ilość]\n"
    "(48301)V(BP-30F);B0-28-471D;11514258;Sztuka;01.02.2030;F2;1000184982;Nieprzypisane;0050;8\n"
    "(48201)V(BP-20F);A1-09-130C;10440264;Sztuka;01.05.2030;F2;1000194333;Nieprzypisane;0070;17\n"
)


class HUImportPowerBITests(TestCase):
    def setUp(self):
        self.client.force_login(_user())

    def test_real_powerbi_headers_map_correctly(self):
        from huctl.views.hu import _map_hu_columns
        header = [h.strip().lower() for h in POWERBI.splitlines()[0].split(";")]
        idx = _map_hu_columns(header)
        self.assertEqual(idx["pickhu"], 2)       # Jednostka obsługi (NOT 'Podst. jedn. miary')
        self.assertEqual(idx["ref"], 0)          # Produkt
        self.assertEqual(idx["location"], 1)     # Miejsce składowania
        self.assertEqual(idx["unit"], 3)         # Podst. jedn. miary
        self.assertEqual(idx["expiry"], 4)       # Termin ważności
        self.assertEqual(idx["lot"], 6)          # Partia
        self.assertEqual(idx["warehouse"], 8)    # Typ magazynu (NOT 'Typ dokumentu')
        self.assertEqual(idx["qty"], 9)          # SumDostępna_ilość
        self.assertNotIn("shipment", idx)        # 'Typ dokumentu' must NOT be a delivery

    def test_powerbi_import_runs(self):
        f = SimpleUploadedFile("stock.csv", POWERBI.encode("utf-8"), content_type="text/csv")
        self.client.post(reverse("ui:planner_hu_import"), {"file": f})
        sh = Shipment.objects.get(name="Stock magazynowy")
        hu = HandlingUnit.objects.get(shipment=sh, code="11514258")
        self.assertEqual(hu.warehouse_type, "0050")
        self.assertEqual(hu.location, "B0-28-471D")
        it = hu.items.first()
        self.assertEqual(it.lot, "1000184982")
        self.assertEqual(str(it.expiry), "2030-02-01")


class HUImportConversionTests(TestCase):
    def setUp(self):
        self.client.force_login(_user())
        from ui.models import PalletizationInstruction
        p = Product.objects.create(code="BP-30F", name="Opaska")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=2, demand_pcs=100, is_active=True)  # 2 OP per KAR

    CONV = (
        "Stock_oraz_DLT[Jednostka obsługi];Stock_oraz_DLT[Produkt];Stock_oraz_DLT[Podst. jedn. miary];[SumDostępna_ilość]\n"
        "HU9;(48301)V(BP-30F);Sztuka;10\n"     # 10 OP, composite REF
    )

    def test_composite_ref_links_product_and_converts_to_kar(self):
        f = SimpleUploadedFile("c.csv", self.CONV.encode("utf-8"), content_type="text/csv")
        self.client.post(reverse("ui:planner_hu_import"), {"file": f})
        hu = HandlingUnit.objects.get(code="HU9")
        it = hu.items.first()
        self.assertEqual(it.product.code, "BP-30F")     # matched inside the composite
        self.assertEqual(it.ref_code, "BP-30F")
        self.assertEqual(it.base_qty, 10)               # 10 OP
        self.assertEqual(it.alt_unit, "KAR")
        self.assertEqual(it.alt_qty, 5)                 # 10 OP / 2 = 5 KAR

    def test_counting_5_kar_for_10_op_is_ok(self):
        f = SimpleUploadedFile("c.csv", self.CONV.encode("utf-8"), content_type="text/csv")
        self.client.post(reverse("ui:planner_hu_import"), {"file": f})
        hu = HandlingUnit.objects.get(code="HU9")
        it = hu.items.first()
        self.client.get(reverse("ui:hu_control_detail", args=[hu.pk]))      # start control
        self.client.post(reverse("ui:hu_control_count", args=[hu.pk, it.pk]),
                         {"action": "confirm", "c_kar": "5",       # addytywnie: 5 KAR = 10 OP
                          "batch_ok": "1", "expiry_ok": "1"})
        it.refresh_from_db()
        self.assertEqual(it.result, "ok")


class ControlHubSeparationTests(TestCase):
    def setUp(self):
        self.client.force_login(_user())

    STOCK = ("Stock_oraz_DLT[Jednostka obsługi];Stock_oraz_DLT[Produkt];[SumDostępna_ilość]\n"
             "HU1;RG-50;8\n")

    def test_stock_import_flagged_and_separated(self):
        f = SimpleUploadedFile("s.csv", self.STOCK.encode("utf-8"), content_type="text/csv")
        resp = self.client.post(reverse("ui:planner_hu_import"), {"file": f})
        self.assertRedirects(resp, reverse("ui:hu_control_hub"))     # import lives in Control now
        sh = Shipment.objects.get(name="Stock magazynowy")
        self.assertTrue(sh.is_stock)
        # Excluded from the transport (Wysyłki) list…
        wys = self.client.get(reverse("ui:planner_shipments"))
        self.assertNotIn(sh, list(wys.context["page_obj"]))
        # …but shown in the Control hub.
        hub = self.client.get(reverse("ui:hu_control_hub"))
        self.assertEqual(hub.status_code, 200)
        self.assertIn(sh, list(hub.context["stock"]))


class HUImportHolesTests(TestCase):
    """Dziury wsadu (grill 2026-09-05, pyt. 30): braki partii/daty wchodzą, ale głośno."""

    def setUp(self):
        self.client.force_login(_user())
        Product.objects.create(code="RG-50", name="Rękawice")

    def test_missing_batch_and_expiry_warns(self):
        csv = (
            "Dostawa;pickHU;REF;Opis;Partia dostawcy;Data ważności;Ilość;JM\n"
            "81782091;HU001;RG-50;Rękawice;;;80;OP\n"
            "81782091;HU002;RG-50;Rękawice;PART1;2027-05-31;40;OP\n"
        )
        f = SimpleUploadedFile("wsad.csv", csv.encode("utf-8"), content_type="text/csv")
        r = self.client.post(reverse("ui:planner_hu_import"), {"file": f}, follow=True)
        msgs = " ".join(str(m) for m in r.context["messages"])
        self.assertIn("Dziury wsadu", msgs)
        self.assertIn("1 bez partii dostawcy", msgs)
        self.assertIn("1 bez terminu ważności", msgs)

    def test_clean_feed_has_no_hole_warning(self):
        csv = (
            "Dostawa;pickHU;REF;Opis;Partia dostawcy;Data ważności;Ilość;JM\n"
            "81782091;HU001;RG-50;Rękawice;PART1;2027-05-31;80;OP\n"
        )
        f = SimpleUploadedFile("wsad.csv", csv.encode("utf-8"), content_type="text/csv")
        r = self.client.post(reverse("ui:planner_hu_import"), {"file": f}, follow=True)
        msgs = " ".join(str(m) for m in r.context["messages"])
        self.assertNotIn("Dziury wsadu", msgs)


class HUImportRunLabelTests(TestCase):
    """Trwały ślad dziur wsadu w ImportRun.label (messages znikają po odświeżeniu)."""

    def setUp(self):
        self.client.force_login(_user())
        Product.objects.create(code="RG-50", name="Rękawice")

    def test_import_run_label_records_holes(self):
        from ui.models import ImportRun
        csv = (
            "Dostawa;pickHU;REF;Opis;Partia dostawcy;Data ważności;Ilość;JM\n"
            "81782091;HU001;RG-50;Rękawice;;;80;OP\n"
        )
        f = SimpleUploadedFile("wsad.csv", csv.encode("utf-8"), content_type="text/csv")
        self.client.post(reverse("ui:planner_hu_import"), {"file": f})
        run = ImportRun.objects.filter(kind="hu_stock_file").first()
        self.assertIsNotNone(run)
        self.assertIn("dziury:", run.label)
        self.assertIn("1 bez partii", run.label)
        self.assertIn("1 bez daty", run.label)
        self.assertIn("wsad.csv", run.label)
