"""Regresja B-004…B-008 (BUGS-FOUND.md) — elementy UI zgodne ze strażnikami widoków.

- B-004: przyciski zapisu na listach danych podstawowych tylko dla Admin / MD / superuser
- B-005: link „Django Admin” tylko dla is_staff
- B-006: link „Mapa magazynu 3D” na stronie stanów tylko dla modułu „magazyn”
- B-007: reguły pakowania klienta — kontrolki także dla Administratorów
- B-008: zakładka MATinfo w skanerze wg modułu phv z nadpisaniami (UserModuleAccess)
"""
from django.test import TestCase
from django.urls import reverse

from testkit import factories as f
from testkit.personas import client_for, make


class MasterDataButtonsTests(TestCase):     # B-004
    LISTS = {
        "ui:planner_products": "ui:planner_product_new",
        "ui:planner_cartons": "ui:planner_carton_new",
        "ui:planner_instructions": "ui:planner_instruction_new",
        "ui:planner_inner_packs": "ui:planner_inner_pack_new",
        "ui:planner_categories": "ui:planner_category_new",
        "ui:planner_locations": "ui:planner_location_new",
    }

    def test_write_buttons_follow_view_guard(self):
        for persona, visible in (("Master Data", True), ("Administratorzy", True), ("superuser", True),
                                 ("Podgląd", False), ("Transport", False), ("bez_roli", False)):
            client = client_for(persona)
            for list_url, write_url in self.LISTS.items():
                with self.subTest(persona=persona, page=list_url):
                    html = client.get(reverse(list_url)).content.decode()
                    self.assertEqual(reverse(write_url) in html, visible)

    def test_row_actions_hidden_for_readers(self):
        product = f.ProductFactory()
        html = client_for("Podgląd").get(reverse("ui:planner_products")).content.decode()
        self.assertIn(product.code, html)                                  # lista dalej widoczna
        self.assertNotIn(reverse("ui:planner_product_edit", args=[product.pk]), html)
        self.assertNotIn(reverse("ui:planner_product_delete", args=[product.pk]), html)


class AdminLinkTests(TestCase):             # B-005
    def test_django_admin_link_only_for_staff(self):
        self.assertNotIn('href="/admin/"', client_for("Master Data").get("/").content.decode())
        self.assertIn('href="/admin/"', client_for("superuser").get("/").content.decode())


class StockMapLinkTests(TestCase):          # B-006
    def test_map_link_only_with_magazyn_module(self):
        url, map_url = reverse("ui:planner_stock"), reverse("ui:warehouse_map")
        for persona, visible in (("Master Data", True), ("Kontrola HU", False), ("Lider kontroli", False)):
            with self.subTest(persona=persona):
                r = client_for(persona).get(url)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(map_url in r.content.decode(), visible)


class CustomerRulesTests(TestCase):         # B-007
    def test_admin_group_sees_rule_controls(self):
        customer = f.CustomerFactory()
        add = reverse("ui:customer_rule_add", args=[customer.pk])
        edit = reverse("ui:planner_customer_edit", args=[customer.pk])
        for persona, visible in (("Administratorzy", True), ("Master Data", True), ("Transport", False)):
            with self.subTest(persona=persona):
                r = client_for(persona).get(edit)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(add in r.content.decode(), visible)


class ScannerMatinfoTabTests(TestCase):     # B-008
    def _tab_visible(self, user):
        from django.test import Client
        client = Client()
        client.force_login(user)
        html = client.get(reverse("ui:hu_my_shift")).content.decode()
        return reverse("ui:phv_home") in html

    def test_override_revokes_tab(self):
        user = make("Kontrola HU")
        self.assertTrue(self._tab_visible(user))
        f.UserModuleAccessFactory(user=user, module_key="phv", allowed=False)
        self.assertFalse(self._tab_visible(user))

    def test_override_grants_tab(self):
        user = make("Lider kontroli")
        f.UserModuleAccessFactory(user=user, module_key="phv", allowed=False)
        self.assertFalse(self._tab_visible(user))
        user.module_access.filter(module_key="phv").update(allowed=True)
        self.assertTrue(self._tab_visible(user))
