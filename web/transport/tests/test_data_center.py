"""Data Center hub: overview cards + unified import center render and link correctly."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.roles import GROUP_MASTER_DATA, GROUP_TRANSPORT


def _user(*groups):
    u = get_user_model().objects.create_user("u", password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class DataCenterTests(TestCase):
    def test_master_data_user_sees_import_center(self):
        self.client.force_login(_user(GROUP_MASTER_DATA))
        r = self.client.get(reverse("ui:data_center"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Centrum importu")
        self.assertContains(r, "Produkty")
        self.assertContains(r, "Klienci")
        # upload posts to a real import endpoint
        self.assertContains(r, reverse("ui:excel_import_products"))
        self.assertContains(r, reverse("ui:planner_customer_import"))

    def test_transport_only_user_denied(self):
        self.client.force_login(_user(GROUP_TRANSPORT))
        r = self.client.get(reverse("ui:data_center"))
        self.assertEqual(r.status_code, 403)

    def test_latest_instruction_prefetch_aware(self):
        """N+1 fix: latest_instruction używa cache prefetcha (0 extra query) i zwraca
        najwyższą AKTYWNĄ wersję — tak samo jak ścieżka zapytania."""
        from django.db.models import prefetch_related_objects
        from ui.models import Product, PalletizationInstruction
        p = Product.objects.create(code="LI1", name="X", ean="1")
        common = dict(carton_l=40, carton_w=30, carton_h=25, unit_weight=0.5,
                      pcs_per_carton=10, demand_pcs=100)
        PalletizationInstruction.objects.create(product=p, version=1, is_active=True, **common)
        PalletizationInstruction.objects.create(product=p, version=2, is_active=True, **common)
        PalletizationInstruction.objects.create(product=p, version=3, is_active=False, **common)
        # bez prefetcha (ścieżka zapytania)
        self.assertEqual(Product.objects.get(pk=p.pk).latest_instruction().version, 2)
        # z prefetchem: ta sama odpowiedź, bez dodatkowego zapytania
        pf = Product.objects.get(pk=p.pk)
        prefetch_related_objects([pf], "instructions")
        with self.assertNumQueries(0):
            self.assertEqual(pf.latest_instruction().version, 2)
        # produkt bez aktywnych instrukcji → None (default), nie wyjątek
        p2 = Product.objects.create(code="LI2", name="Y", ean="2")
        prefetch_related_objects([p2], "instructions")
        with self.assertNumQueries(0):
            self.assertIsNone(p2.latest_instruction())

    def test_health_metrics(self):
        from ui.models import Product, PalletizationInstruction
        # ready product: instruction + EAN
        p_ok = Product.objects.create(code="OK1", name="Gotowy", ean="5900001")
        PalletizationInstruction.objects.create(product=p_ok, version=1, carton_l=40, carton_w=30,
                                                carton_h=25, unit_weight=0.5, pcs_per_carton=10,
                                                demand_pcs=100, is_active=True)
        # gap product: no instruction, no EAN
        Product.objects.create(code="GAP1", name="Brak", ean="")
        self.client.force_login(_user(GROUP_MASTER_DATA))
        h = self.client.get(reverse("ui:data_center")).context["health"]
        self.assertEqual(h["total"], 2)
        self.assertEqual(h["ready"], 1)
        self.assertEqual(h["score"], 50)
        self.assertEqual(h["no_instr"], 1)
        self.assertEqual(h["no_ean"], 1)

    def test_health_score_full_when_no_products(self):
        self.client.force_login(_user(GROUP_MASTER_DATA))
        h = self.client.get(reverse("ui:data_center")).context["health"]
        self.assertEqual(h["score"], 100)        # no products → nothing incomplete


class PackagingQuickEditTests(TestCase):
    def setUp(self):
        from ui.models import Carton
        self.client.force_login(_user(GROUP_MASTER_DATA))
        self.c = Carton.objects.create(name="K1", length_cm=40, width_cm=30, height_cm=20,
                                       unit_weight_kg=0.5, pieces_per_carton=10, tare_kg=0.1)

    def test_renders_table(self):
        r = self.client.get(reverse("ui:data_center_packaging"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "K1")
        self.assertContains(r, f"c{self.c.pk}_l")

    def test_bulk_save_updates_changed_row(self):
        self.client.post(reverse("ui:data_center_packaging"), {
            f"c{self.c.pk}_l": "45", f"c{self.c.pk}_w": "30", f"c{self.c.pk}_h": "22",
            f"c{self.c.pk}_ppc": "12", f"c{self.c.pk}_uw": "0,6", f"c{self.c.pk}_tare": "0.2"})
        self.c.refresh_from_db()
        self.assertEqual((self.c.length_cm, self.c.height_cm, self.c.pieces_per_carton), (45, 22, 12))
        self.assertEqual(self.c.unit_weight_kg, 0.6)   # comma decimal accepted

    def test_invalid_dims_skipped(self):
        self.client.post(reverse("ui:data_center_packaging"), {
            f"c{self.c.pk}_l": "0", f"c{self.c.pk}_w": "30", f"c{self.c.pk}_h": "22",
            f"c{self.c.pk}_ppc": "12", f"c{self.c.pk}_uw": "0.6"})
        self.c.refresh_from_db()
        self.assertEqual(self.c.length_cm, 40)         # unchanged (L<=0 rejected)

    def test_zero_weight_rejected(self):
        # uw=0 wywala kalkulator (waga musi być >0) — odrzuć jak inne błędne wiersze.
        self.client.post(reverse("ui:data_center_packaging"), {
            f"c{self.c.pk}_l": "45", f"c{self.c.pk}_w": "30", f"c{self.c.pk}_h": "22",
            f"c{self.c.pk}_ppc": "12", f"c{self.c.pk}_uw": "0", f"c{self.c.pk}_tare": "0.1"})
        self.c.refresh_from_db()
        self.assertEqual((self.c.length_cm, self.c.unit_weight_kg), (40, 0.5))   # niezmienione

    def test_negative_tare_rejected(self):
        self.client.post(reverse("ui:data_center_packaging"), {
            f"c{self.c.pk}_l": "45", f"c{self.c.pk}_w": "30", f"c{self.c.pk}_h": "22",
            f"c{self.c.pk}_ppc": "12", f"c{self.c.pk}_uw": "0.6", f"c{self.c.pk}_tare": "-1"})
        self.c.refresh_from_db()
        self.assertEqual(self.c.length_cm, 40)         # niezmienione (tare<0 odrzucone)

    def test_change_warns_instructions_stale(self):
        r = self.client.post(reverse("ui:data_center_packaging"), {
            f"c{self.c.pk}_l": "45", f"c{self.c.pk}_w": "30", f"c{self.c.pk}_h": "22",
            f"c{self.c.pk}_ppc": "12", f"c{self.c.pk}_uw": "0.6", f"c{self.c.pk}_tare": "0.2"},
            follow=True)
        self.assertContains(r, "NIE zostały automatycznie przeliczone")

    def test_save_triggers_discrepancy_recheck(self):
        from unittest.mock import patch
        with patch("ui.notifications.run_stock_discrepancy_checks", return_value=0) as m:
            self.client.post(reverse("ui:data_center_packaging"), {
                f"c{self.c.pk}_l": "45", f"c{self.c.pk}_w": "30", f"c{self.c.pk}_h": "22",
                f"c{self.c.pk}_ppc": "12", f"c{self.c.pk}_uw": "0.6", f"c{self.c.pk}_tare": "0.1"})
        m.assert_called_once()

    def test_no_change_no_recheck(self):
        from unittest.mock import patch
        with patch("ui.notifications.run_stock_discrepancy_checks") as m:
            self.client.post(reverse("ui:data_center_packaging"), {
                f"c{self.c.pk}_l": "40", f"c{self.c.pk}_w": "30", f"c{self.c.pk}_h": "20",
                f"c{self.c.pk}_ppc": "10", f"c{self.c.pk}_uw": "0.5", f"c{self.c.pk}_tare": "0.1"})
        m.assert_not_called()


class ReviewTests(TestCase):
    def test_lists_product_without_instruction(self):
        from ui.models import Product
        Product.objects.create(code="P-NOINSTR", name="Bez instrukcji", ean="")
        self.client.force_login(_user(GROUP_MASTER_DATA))
        r = self.client.get(reverse("ui:data_center_review"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "P-NOINSTR")
        self.assertContains(r, "bez instrukcji paletyzacji")

    def test_detects_duplicate_ean(self):
        from ui.models import Product
        Product.objects.create(code="DUP-A", name="Produkt A", ean="5901111")
        Product.objects.create(code="DUP-B", name="Produkt B", ean="5901111")
        Product.objects.create(code="UNIQ", name="Inny", ean="5902222")
        self.client.force_login(_user(GROUP_MASTER_DATA))
        r = self.client.get(reverse("ui:data_center_review"))
        dup = r.context["dup_ean"]
        self.assertEqual(len(dup), 1)                      # only the shared EAN
        self.assertEqual(dup[0]["ean"], "5901111")
        self.assertEqual(len(dup[0]["products"]), 2)
        self.assertContains(r, "Zduplikowane kody EAN")
        self.assertContains(r, "DUP-A")
        self.assertContains(r, "DUP-B")

    def test_blank_eans_not_flagged_as_duplicate(self):
        from ui.models import Product
        Product.objects.create(code="E1", name="A", ean="")
        Product.objects.create(code="E2", name="B", ean="")
        self.client.force_login(_user(GROUP_MASTER_DATA))
        r = self.client.get(reverse("ui:data_center_review"))
        self.assertEqual(r.context["dup_ean"], [])

    def _marm_group(self, resp):
        return next(g for g in resp.context["groups"] if g["key"] == "not_in_marm")

    def test_flags_product_not_in_sap_marm(self):
        from ui.models import Product, MaterialReference
        Product.objects.create(code="IN-MARM", name="W SAP", ean="1")
        Product.objects.create(code="OUT-MARM", name="Spoza SAP", ean="2")
        MaterialReference.objects.create(code="IN-MARM", name="W SAP")
        self.client.force_login(_user(GROUP_MASTER_DATA))
        r = self.client.get(reverse("ui:data_center_review"))
        codes = [p.code for p in self._marm_group(r)["items"]]
        self.assertEqual(codes, ["OUT-MARM"])
        self.assertContains(r, "Produkty spoza SAP MARM")

    def test_marm_check_skipped_when_no_reference(self):
        from ui.models import Product
        Product.objects.create(code="ANY", name="X", ean="9")
        self.client.force_login(_user(GROUP_MASTER_DATA))
        r = self.client.get(reverse("ui:data_center_review"))
        # No MaterialReference rows → check is skipped (no false positives).
        self.assertEqual(self._marm_group(r)["items"], [])
