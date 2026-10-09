"""Kaskada hierarchii opakowań: pack_into (sztuka→OPZ→karton→paleta) i widok wariantów."""
from django.test import TestCase


class PackIntoTests(TestCase):
    def test_simple_grid(self):
        from ui.views.carton_opt import pack_into
        r = pack_into((40, 30, 20), (10, 10, 10))     # 4×3 na warstwę × 2 warstwy
        self.assertEqual(r["count"], 24)
        self.assertEqual(r["layers"], 2)
        self.assertFalse(r["error"])

    def test_rotation_helps(self):
        from ui.views.carton_opt import pack_into
        # 20×10 unit w 30×20 kontenerze: bez rotacji 1/warstwę, z rotacją 3.
        r = pack_into((30, 20, 10), (20, 10, 10))
        self.assertGreaterEqual(r["count"], 3)

    def test_smaller_unit_never_fewer(self):
        from ui.views.carton_opt import pack_into
        big = pack_into((60, 40, 30), (20, 20, 15))["count"]
        small = pack_into((60, 40, 30), (10, 10, 15))["count"]
        self.assertGreaterEqual(small, big)

    def test_zero_dim_is_error_not_crash(self):
        from ui.views.carton_opt import pack_into
        r = pack_into((40, 30, 20), (0, 10, 10))
        self.assertIsNone(r["count"])
        self.assertTrue(r["error"])

    def test_unit_bigger_than_container(self):
        from ui.views.carton_opt import pack_into
        r = pack_into((10, 10, 10), (20, 20, 20))
        self.assertIsNone(r["count"])
        self.assertTrue(r["error"])


class AlternativeSlotTests(TestCase):
    def test_slot_and_level_dims_fields(self):
        from ui.models import Product, CartonAlternative
        p = Product.objects.create(code="H-1", name="Hier")
        alt = CartonAlternative.objects.create(
            product=p, label="Wariant B", slot="B",
            length_cm=40, width_cm=30, height_cm=20,
            pack_l_cm=20, pack_w_cm=15, pack_h_cm=10,
            unit_l_cm=5, unit_w_cm=5, unit_h_cm=10)
        alt.refresh_from_db()
        self.assertEqual(alt.slot, "B")
        self.assertEqual(alt.pack_l_cm, 20)
        self.assertEqual(alt.unit_h_cm, 10)


class HierarchyCascadeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from ui.models import Product, PalletizationInstruction
        cls.p = Product.objects.create(
            code="CAS-1", name="Cascade",
            unit_length_cm=5, unit_width_cm=5, unit_height_cm=10)
        cls.instr = PalletizationInstruction.objects.create(
            product=cls.p, version=1, is_active=True,
            carton_l=40, carton_w=30, carton_h=20,
            unit_weight=0.2, pcs_per_carton=48, demand_pcs=1000,
            pallet_length_cm=120, pallet_width_cm=80,
            pallet_base_height_cm=15, max_height_total_cm=180)

    def test_four_levels_in_order(self):
        from ui.views.carton_opt import _hierarchy_levels
        lv = _hierarchy_levels(self.instr, self.p)
        self.assertEqual([l["key"] for l in lv],
                         ["unit", "inner_pack", "carton", "pallet"])

    def test_pack_override_cascades_up(self):
        from ui.views.carton_opt import _hierarchy_levels
        base = _hierarchy_levels(self.instr, self.p, pack=(20, 15, 10))
        small = _hierarchy_levels(self.instr, self.p, pack=(10, 15, 10))
        # Mniejsze OPZ → w kartonie mieści się ich nie mniej.
        c_base = base[2]["spec"]["contains"]
        c_small = small[2]["spec"]["contains"]
        self.assertGreaterEqual(c_small, c_base)

    def test_carton_override_changes_pallet(self):
        from ui.views.carton_opt import _hierarchy_levels
        lv = _hierarchy_levels(self.instr, self.p, carton=(60, 40, 20))
        self.assertEqual(lv[3]["key"], "pallet")
        self.assertIsNotNone(lv[3]["spec"]["contains"])   # kartonów/paletę policzony


class VariantsViewHierarchyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        from ui.roles import ALL_GROUPS
        cls.user = get_user_model().objects.create_user("opt", password="x")
        for g in ALL_GROUPS:
            cls.user.groups.add(Group.objects.get_or_create(name=g)[0])
        from ui.models import Product, PalletizationInstruction
        cls.p = Product.objects.create(code="VH-1", name="VH",
            unit_length_cm=5, unit_width_cm=5, unit_height_cm=10)
        PalletizationInstruction.objects.create(
            product=cls.p, version=1, is_active=True,
            carton_l=40, carton_w=30, carton_h=20, unit_weight=0.2,
            pcs_per_carton=48, demand_pcs=1000, pallet_length_cm=120,
            pallet_width_cm=80, pallet_base_height_cm=15, max_height_total_cm=180)

    def setUp(self):
        self.client.force_login(self.user)

    def test_context_has_hierarchy_a(self):
        from django.urls import reverse
        resp = self.client.get(reverse("ui:carton_opt_variants"),
                               {"product": "VH-1"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["hier_a"]), 4)
        self.assertIsNone(resp.context["variant_b"])

    def test_save_slot_b_with_level_dims_and_cascade(self):
        from django.urls import reverse
        from ui.models import CartonAlternative
        next_url = reverse("ui:carton_opt_variants") + f"?product={self.p.code}"
        resp = self.client.post(reverse("ui:carton_opt_variant_save"), {
            "product_id": self.p.pk, "label": "Wariant B", "slot": "B",
            "length_cm": 40, "width_cm": 30, "height_cm": 25,
            "pack_l": 20, "pack_w": 15, "pack_h": 12,
            "next": next_url})
        self.assertRedirects(resp, next_url, fetch_redirect_response=False)
        alt = CartonAlternative.objects.get(product=self.p, slot="B")
        self.assertEqual(alt.pack_h_cm, 12)
        resp = self.client.get(reverse("ui:carton_opt_variants"),
                               {"product": "VH-1"})
        self.assertEqual(len(resp.context["hier_b"]), 4)
        self.assertIsNone(resp.context["hier_c"])


class VariantsTemplateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        from ui.roles import ALL_GROUPS
        cls.user = get_user_model().objects.create_user("opt2", password="x")
        for g in ALL_GROUPS:
            cls.user.groups.add(Group.objects.get_or_create(name=g)[0])
        from ui.models import Product, PalletizationInstruction
        cls.p = Product.objects.create(code="VH-1", name="VH",
            unit_length_cm=5, unit_width_cm=5, unit_height_cm=10)
        PalletizationInstruction.objects.create(
            product=cls.p, version=1, is_active=True,
            carton_l=40, carton_w=30, carton_h=20, unit_weight=0.2,
            pcs_per_carton=48, demand_pcs=1000, pallet_length_cm=120,
            pallet_width_cm=80, pallet_base_height_cm=15, max_height_total_cm=180)

    def setUp(self):
        self.client.force_login(self.user)

    def test_renders_four_levels_and_tabs(self):
        from django.urls import reverse
        resp = self.client.get(reverse("ui:carton_opt_variants"), {"product": "VH-1"})
        self.assertContains(resp, "Sztuka / opakowanie")
        self.assertContains(resp, "Opakowanie zbiorcze")
        # Układ 3-kolumnowy A|B|C obok siebie (decyzja usera 2026-08-24, zamiast zakładek).
        self.assertContains(resp, 'class="vgrid"')
        self.assertContains(resp, "Wariant A — obecny")
        self.assertContains(resp, "Wariant B")
        self.assertContains(resp, "Wariant C")
        self.assertContains(resp, "height:480px")   # paleta w kolumnie
