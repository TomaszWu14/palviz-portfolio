"""Planer — CRUD produktów, szablon CSV i import CSV (ui/views/products_crud.py, TEST-004)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import (
    Carton,
    InnerPack,
    MaterialReference,
    PalletizationInstruction,
    Product,
    ProductCategory,
)
from ui.roles import GROUP_MASTER_DATA, GROUP_VIEWER


def _user(username, group_name=None):
    u = get_user_model().objects.create_user(username=username, password="x")
    if group_name:
        u.groups.add(Group.objects.get_or_create(name=group_name)[0])
    return u


def _product_post(code="P-100", **extra):
    data = {"code": code, "name": "Kubek", "max_stack_layers": 0, "is_active": "on"}
    data.update(extra)
    return data


class ProductListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.viewer = _user("viewer", GROUP_VIEWER)
        cls.cat = ProductCategory.objects.create(name="Kuchnia")
        cls.p1 = Product.objects.create(code="A-1", name="Kubek", supplier_short="DEMODOST", category=cls.cat)
        cls.p2 = Product.objects.create(code="B-2", name="Talerz", supplier_short="OTHER")

    def setUp(self):
        self.client.force_login(self.viewer)

    def _codes(self, **params):
        r = self.client.get(reverse("ui:planner_products"), params)
        self.assertEqual(r.status_code, 200)
        return [p.code for p in r.context["page_obj"]]

    def test_list_and_filters(self):
        self.assertEqual(self._codes(), ["A-1", "B-2"])
        self.assertEqual(self._codes(q="talerz"), ["B-2"])
        self.assertEqual(self._codes(supplier="demodost"), ["A-1"])
        self.assertEqual(self._codes(category=self.cat.pk), ["A-1"])

    def test_no_instr_filter_excludes_products_with_active_instruction(self):
        carton = Carton.objects.create(
            name="K", length_cm=40, width_cm=30, height_cm=25, unit_weight_kg=0.5, pieces_per_carton=10
        )
        PalletizationInstruction.objects.create(
            product=self.p1,
            version=1,
            carton=carton,
            carton_l=40,
            carton_w=30,
            carton_h=25,
            unit_weight=0.5,
            pcs_per_carton=10,
        )
        self.assertEqual(self._codes(no_instr="1"), ["B-2"])

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        r = self.client.get(reverse("ui:planner_products"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])

    def test_user_without_role_forbidden(self):
        self.client.force_login(_user("norole"))
        self.assertEqual(self.client.get(reverse("ui:planner_products")).status_code, 403)

    def test_csv_template(self):
        r = self.client.get(reverse("ui:planner_product_csv_template"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("produkty_master_wzor.csv", r["Content-Disposition"])
        body = r.content.decode("utf-8")
        self.assertIn("product_code", body)
        self.assertIn("SKU-003", body)


class ProductFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.md = _user("md", GROUP_MASTER_DATA)
        cls.viewer = _user("viewer", GROUP_VIEWER)

    def setUp(self):
        self.client.force_login(self.md)

    def test_viewer_cannot_create_or_delete(self):
        p = Product.objects.create(code="X", name="X")
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(reverse("ui:planner_product_new"), _product_post()).status_code, 403)
        r = self.client.post(reverse("ui:planner_product_delete", args=[p.pk]))
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Product.objects.filter(pk=p.pk).exists())
        self.assertFalse(Product.objects.filter(code="P-100").exists())

    def test_get_new_form_prefills_from_material_reference(self):
        MaterialReference.objects.create(code="REF-1", name="Z SAP", supplier_short="SUP")
        r = self.client.get(reverse("ui:planner_product_new"), {"code": "REF-1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["ref_data"].code, "REF-1")
        self.assertEqual(r.context["form"].initial["name"], "Z SAP")

    def test_get_new_form_unknown_ref_code(self):
        r = self.client.get(reverse("ui:planner_product_new"), {"code": "NOPE"})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context["ref_data"])

    def test_create_product_only(self):
        r = self.client.post(reverse("ui:planner_product_new"), _product_post())
        self.assertRedirects(r, reverse("ui:planner_products"), fetch_redirect_response=False)
        self.assertTrue(Product.objects.filter(code="P-100", name="Kubek").exists())

    def test_edit_product(self):
        p = Product.objects.create(code="P-100", name="Stara")
        r = self.client.post(reverse("ui:planner_product_edit", args=[p.pk]), _product_post(name="Nowa"))
        self.assertEqual(r.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.name, "Nowa")

    def test_get_edit_form(self):
        p = Product.objects.create(code="P-100", name="Stara")
        r = self.client.get(reverse("ui:planner_product_edit", args=[p.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["obj"], p)

    def test_validation_error_missing_name_rerenders(self):
        r = self.client.post(reverse("ui:planner_product_new"), _product_post(name=""))
        self.assertEqual(r.status_code, 200)
        self.assertIn("name", r.context["form"].errors)
        self.assertFalse(Product.objects.exists())

    def test_validation_error_code_not_in_material_reference(self):
        MaterialReference.objects.create(code="REF-1")
        r = self.client.post(reverse("ui:planner_product_new"), _product_post(code="ZZZ"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("code", r.context["form"].errors)

    def test_create_with_new_carton_and_instruction(self):
        post = _product_post(
            carton_mode="new",
            c_name="Karton nowy",
            c_l=40,
            c_w=30,
            c_h=25,
            c_weight="0.5",
            c_pcs=12,
            c_tare="0.2",
            create_instr="1",
        )
        r = self.client.post(reverse("ui:planner_product_new"), post)
        instr = PalletizationInstruction.objects.get(product__code="P-100")
        self.assertRedirects(
            r, reverse("ui:planner_instruction_detail", args=[instr.pk]), fetch_redirect_response=False
        )
        self.assertEqual(instr.carton.name, "Karton nowy")
        self.assertEqual(instr.version, 1)

    def test_new_carton_zero_weight_saves_product_without_carton(self):
        post = _product_post(carton_mode="new", c_weight="0", create_instr="1")
        r = self.client.post(reverse("ui:planner_product_new"), post)
        self.assertRedirects(r, reverse("ui:planner_products"), fetch_redirect_response=False)
        self.assertTrue(Product.objects.filter(code="P-100").exists())
        self.assertFalse(Carton.objects.exists())

    def test_link_existing_carton(self):
        c = Carton.objects.create(
            name="Istniejący", length_cm=40, width_cm=30, height_cm=25, unit_weight_kg=0.5, pieces_per_carton=10
        )
        post = _product_post(carton_mode="existing", carton_pk=str(c.pk), create_instr="1")
        r = self.client.post(reverse("ui:planner_product_new"), post)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(PalletizationInstruction.objects.get(product__code="P-100").carton, c)

    def test_link_existing_carton_bad_pk_ignored(self):
        post = _product_post(carton_mode="existing", carton_pk="abc", create_instr="1")
        r = self.client.post(reverse("ui:planner_product_new"), post)
        self.assertRedirects(r, reverse("ui:planner_products"), fetch_redirect_response=False)
        self.assertFalse(PalletizationInstruction.objects.exists())

    def test_delete(self):
        p = Product.objects.create(code="DEL", name="Do usunięcia")
        r = self.client.post(reverse("ui:planner_product_delete", args=[p.pk]))
        self.assertRedirects(r, reverse("ui:planner_products"), fetch_redirect_response=False)
        self.assertFalse(Product.objects.filter(pk=p.pk).exists())

    def test_delete_requires_post(self):
        p = Product.objects.create(code="DEL", name="X")
        self.assertEqual(self.client.get(reverse("ui:planner_product_delete", args=[p.pk])).status_code, 405)
        self.assertTrue(Product.objects.filter(pk=p.pk).exists())


HEADER = (
    "product_code,product_name,unit_l_cm,inner_name,inner_l_cm,inner_units_per_pack,"
    "carton_name,carton_l_cm,carton_w_cm,carton_h_cm,unit_weight_kg,pieces_per_carton,"
    "pallet_code,demand_pcs\n"
)


class ProductImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.md = _user("md", GROUP_MASTER_DATA)

    def setUp(self):
        self.client.force_login(self.md)

    def _import(self, text):
        f = SimpleUploadedFile("p.csv", text.encode("utf-8-sig"), content_type="text/csv")
        return self.client.post(reverse("ui:planner_product_import"), {"file": f}, follow=True)

    def _msgs(self, r):
        return [str(m) for m in r.context["messages"]]

    def test_get_redirects(self):
        r = self.client.get(reverse("ui:planner_product_import"))
        self.assertRedirects(r, reverse("ui:planner_products"), fetch_redirect_response=False)

    def test_no_file(self):
        r = self.client.post(reverse("ui:planner_product_import"), follow=True)
        self.assertIn("Nie wybrano pliku.", self._msgs(r))

    def test_full_hierarchy_import(self):
        rows = HEADER + (
            'SKU-1,Kubek,8,Blister 6,25,6,Karton A,40,30,25,"0,35",24,EU,2400\n'
            "SKU-2,Talerz,,,,,,,,,,,,\n"
            ",bez kodu,,,,,,,,,,,,\n"
            "SKU-3,Zła waga,,,,,Karton C,30,20,20,0,10,,\n"
        )
        r = self._import(rows)
        self.assertEqual(Product.objects.count(), 3)
        self.assertEqual(InnerPack.objects.get().name, "Blister 6")
        carton = Carton.objects.get(name="Karton A")
        self.assertEqual(carton.inner_pack.name, "Blister 6")
        self.assertAlmostEqual(carton.unit_weight_kg, 0.35)
        instr = PalletizationInstruction.objects.get()
        self.assertEqual((instr.product.code, instr.version, instr.demand_pcs), ("SKU-1", 1, 2400))
        msgs = " ".join(self._msgs(r))
        self.assertIn("Produkty: +3 nowych", msgs)
        self.assertIn("brak product_code", msgs)
        self.assertIn("unit_weight_kg musi być > 0", msgs)
        self.assertIn("Błędy w 2 wierszach", msgs)

    def test_reimport_updates_and_bumps_instruction_version(self):
        row = HEADER + "SKU-1,Kubek,8,Blister 6,25,6,Karton A,40,30,25,0.35,24,EU,2400\n"
        self._import(row)
        r = self._import(row.replace("Kubek", "Kubek v2"))
        self.assertEqual(Product.objects.get(code="SKU-1").name, "Kubek v2")
        self.assertEqual(sorted(PalletizationInstruction.objects.values_list("version", flat=True)), [1, 2])
        self.assertIn("1 zaktualizowanych", " ".join(self._msgs(r)))

    def test_not_utf8_file_reports_error(self):
        f = SimpleUploadedFile("p.csv", b"\xff\xfe\x00bad", content_type="text/csv")
        r = self.client.post(reverse("ui:planner_product_import"), {"file": f}, follow=True)
        self.assertTrue(any("Błąd wczytywania pliku" in m for m in self._msgs(r)))
        self.assertFalse(Product.objects.exists())
