"""Slotting analytics: ABC classification + what-if travel simulation."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ui.slotting import abc_classify, simulate_slotting, slot_distance
from ui.models import Product, Shipment, ShipmentLine
from ui.roles import GROUP_MASTER_DATA


class AbcPureTests(SimpleTestCase):
    def test_abc_classes_by_cumulative_share(self):
        rows = abc_classify({"A": 80, "B": 15, "C": 5})
        by = {r["key"]: r["klass"] for r in rows}
        self.assertEqual(by["A"], "A")   # 80% cum → A
        self.assertEqual(by["B"], "B")   # 95% cum → B
        self.assertEqual(by["C"], "C")   # 100% → C
        self.assertEqual(rows[0]["key"], "A")          # sorted by demand desc
        self.assertAlmostEqual(rows[0]["share"], 0.8)

    def test_ignores_zero_and_negative(self):
        self.assertEqual(len(abc_classify({"x": 0, "y": -3, "z": 5})), 1)


class SimulationPureTests(SimpleTestCase):
    def test_savings_from_rearrangement(self):
        # demand [10,5,1]; potencjał = worst (41) − optimized (23) = 18. Baseline = worst
        # (deterministyczny), bo bez realnej mapy SKU→slot nie liczymy „obecnej" drogi.
        sim = simulate_slotting({"a": 10, "b": 5, "c": 1}, [3, 1, 2])
        self.assertEqual(sim["worst"], 41.0)
        self.assertEqual(sim["baseline"], 41.0)
        self.assertEqual(sim["optimized"], 23.0)
        self.assertEqual(sim["savings"], 18.0)
        self.assertGreater(sim["savings_pct"], 0)
        # Optimum is never worse than worst case.
        self.assertLessEqual(sim["optimized"], sim["worst"])

    def test_empty_inputs_safe(self):
        self.assertEqual(simulate_slotting({}, [1, 2])["savings_pct"], 0.0)
        self.assertEqual(simulate_slotting({"a": 1}, [])["n_matched"], 0)

    def test_slot_distance_manhattan(self):
        self.assertEqual(slot_distance(3, 4, (0, 0)), 7)
        self.assertEqual(slot_distance(3, 4, (1, 1)), 5)


class SlottingViewTests(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_user(username="md", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)
        p1 = Product.objects.create(code="FAST", name="Szybki")
        p2 = Product.objects.create(code="SLOW", name="Wolny")
        for i in range(5):
            sh = Shipment.objects.create(name=f"S{i}", status="draft")
            ShipmentLine.objects.create(shipment=sh, product=p1, quantity=100, unit="kar", order=0)
            ShipmentLine.objects.create(shipment=sh, product=p2, quantity=1, unit="kar", order=1)

    def test_analysis_page_renders_abc(self):
        resp = self.client.get(reverse("ui:slotting_analysis"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Slotting")
        # FAST has far higher demand → class A and listed first.
        abc = resp.context["abc"]
        self.assertEqual(abc[0]["key"], "FAST")
        self.assertEqual(abc[0]["klass"], "A")


class DemandUnitNormalizationTests(TestCase):
    """Linie w różnych jednostkach (szt/kar/pal) muszą być sprowadzone do wspólnej jednostki
    (kartonów) — inaczej 2 pal < 100 szt, a fizycznie 2 palety to znacznie więcej."""
    def setUp(self):
        from ui.models import PalletizationInstruction
        from ui.views.slotting import _demand_by_product
        self._demand = _demand_by_product
        self.pal_prod = Product.objects.create(code="PALLETY", name="Na paletach")
        self.szt_prod = Product.objects.create(code="SZTUKI", name="Na sztuki")
        # PALLETY: 1 karton = 10 szt, 1 paleta = 40 kartonów.
        PalletizationInstruction.objects.create(
            product=self.pal_prod, version=1, is_active=True, unit_weight=1.0, pcs_per_carton=10,
            carton_l=30, carton_w=20, carton_h=20,
            layouts=[{"name": "L", "cartons_per_pallet": 40}], selected_layout="L")
        PalletizationInstruction.objects.create(
            product=self.szt_prod, version=1, is_active=True, unit_weight=1.0, pcs_per_carton=10,
            carton_l=30, carton_w=20, carton_h=20,
            layouts=[{"name": "L", "cartons_per_pallet": 40}], selected_layout="L")
        sh = Shipment.objects.create(name="S", status="draft")
        ShipmentLine.objects.create(shipment=sh, product=self.pal_prod, quantity=2, unit="pal", order=0)
        ShipmentLine.objects.create(shipment=sh, product=self.szt_prod, quantity=100, unit="szt", order=1)

    def test_pallets_outrank_pieces(self):
        demands, _ = self._demand()
        # 2 pal × 40 = 80 kartonów-ekwiwalentu; 100 szt / 10 = 10 kartonów.
        self.assertEqual(demands["PALLETY"], 80.0)
        self.assertEqual(demands["SZTUKI"], 10.0)
        self.assertGreater(demands["PALLETY"], demands["SZTUKI"])

    def test_script_tag_in_code_escaped_not_500(self):
        # Kod produktu z </script> nie może wywalić strony ani wyjść z bloku JSON (json_script).
        Product.objects.create(code="X</script><img src=x>", name="Zły")
        sh = Shipment.objects.get()
        ShipmentLine.objects.create(shipment=sh, product=Product.objects.get(code="X</script><img src=x>"),
                                    quantity=5, unit="kar", order=2)
        u = get_user_model().objects.create_user(username="md2", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)
        resp = self.client.get(reverse("ui:slotting_analysis"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "</script><img src=x>", html=False)
