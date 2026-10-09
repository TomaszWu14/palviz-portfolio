"""Testy charakteryzujące _calc_shipment_data (CODE-001) — przypinają OBECNE zachowanie przed podziałem.

Pełny wynik _calc_shipment_data dla zestawu scenariuszy porównywany ze snapshotem JSON
(data/calc_shipment_data_snapshot.json) — bez PK: linie/produkty zastąpione kodem produktu,
scena 3D („three”) streszczona do liczby palet i pudeł per paleta. Regeneracja (tylko świadomie,
gdy zmiana zachowania jest zamierzona):
    CALC_SHIPMENT_SNAPSHOT_UPDATE=1 python manage.py test ui.tests.test_calc_shipment_data_characterization
"""
import json
import os
from pathlib import Path

from django.test import TestCase

from transport.models import Shipment, ShipmentLine
from ui.models import Customer, PalletizationInstruction, Product
from ui.views.core.helpers import _calc_shipment_data
from ui.views.core.helpers_shipment_calc import _latest_instructions

SNAPSHOT = Path(__file__).with_name("data") / "calc_shipment_data_snapshot.json"

_LAYOUT_40 = [{"name": "grid", "cartons_per_pallet": 40, "cartons_per_layer": 8}]


def _three_summary(three):
    if three is None:
        return None
    return {
        "n_pallets": three.get("n_pallets"),
        "max_h_cm": three.get("max_h_cm"),
        "pallets": [
            {"n_boxes": len(p.get("boxes") or []), "empty": bool(p.get("empty")),
             "mixed": bool(p.get("mixed")),
             "skus": sorted({b.get("label") or b.get("code") or "" for b in (p.get("boxes") or [])})}
            for p in (three.get("pallets") or [])
        ],
    }


def _normalize(calc):
    """Wynik bez PK/obiektów ORM → struktura JSON-owalna i stabilna."""
    out = dict(calc)
    out["lines"] = []
    for lc in calc["lines"]:
        d = dict(lc)
        ln = d.pop("line")
        d["line"] = {"quantity": ln.quantity, "unit": ln.unit, "order": ln.order}
        d["product"] = d.pop("product").code
        d["carton_dims"] = list(d["carton_dims"])
        out["lines"].append(d)
    out["scenarios"] = []
    for sc in calc["scenarios"]:
        s = dict(sc)
        s["three"] = _three_summary(s["three"])
        out["scenarios"].append(s)
    return out


def _instr(product, version=1, l=40, w=30, h=25, uw=2.0, ppc=4, tare=0.5, layouts=None,
           active=True, unit_volume=None):
    return PalletizationInstruction.objects.create(
        product=product, version=version, carton_l=l, carton_w=w, carton_h=h,
        unit_weight=uw, pcs_per_carton=ppc, carton_tare=tare, is_active=active,
        layouts=_LAYOUT_40 if layouts is None else layouts, unit_volume_m3=unit_volume)


class CalcShipmentDataCharacterizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        P = Product.objects.create
        cls.pA = P(code="A-KAR", name="Kartonowy")
        cls.pB = P(code="B-SZT", name="Sztukowy")
        cls.pC = P(code="C-PAL", name="Paletowy")
        cls.pD = P(code="D-NOLAYOUT", name="Bez układu")
        cls.pE = P(code="E-ZEROCPP", name="Układ z cpp=0")
        cls.pF = P(code="F-MARM", name="Objętość z MARM")
        cls.pG = P(code="G-NOSTACK", name="Niepiętrowalny", stackable=False)
        cls.pH = P(code="H-MAXL", name="Limit warstw", max_stack_layers=3)
        cls.pX = P(code="X-MISSING", name="Brak instrukcji")
        cls.pZ = P(code="Z-ZERODIM", name="Zerowe wymiary")
        cls.pV = P(code="V-VERS", name="Wersje")
        cls.pHeavy = P(code="HVY", name="Ciężki")

        _instr(cls.pA, l=60, w=40, h=40, uw=3.0, ppc=2, tare=1.0)
        _instr(cls.pB, l=30, w=20, h=15, uw=0.25, ppc=12, tare=0.2)
        _instr(cls.pC, l=40, w=30, h=25, uw=1.5, ppc=6, tare=0.3)
        _instr(cls.pD, l=50, w=40, h=30, uw=1.0, ppc=1, tare=0.0, layouts=[])
        _instr(cls.pE, l=50, w=40, h=30, uw=1.0, ppc=1, tare=0.0,
               layouts=[{"name": "zero", "cartons_per_pallet": 0}])
        _instr(cls.pF, l=40, w=40, h=40, uw=0.5, ppc=10, tare=0.1, unit_volume=0.004)
        _instr(cls.pG, l=60, w=40, h=50, uw=5.0, ppc=1, tare=0.5)
        _instr(cls.pH, l=30, w=30, h=30, uw=2.0, ppc=2, tare=0.2)
        _instr(cls.pZ, l=0, w=30, h=20)
        # pcs_per_carton=0 w bazie → guard `or 1`
        _instr(cls.pV, version=1, l=20, w=20, h=20, uw=1.0, ppc=0, tare=0.0)
        _instr(cls.pV, version=2, l=30, w=30, h=30, uw=1.0, ppc=0, tare=0.0)
        _instr(cls.pV, version=3, l=99, w=99, h=99, uw=9.0, ppc=1, tare=9.0, active=False)
        _instr(cls.pHeavy, l=40, w=30, h=25, uw=50.0, ppc=1, tare=0.0)

        cls.cust_cap = Customer.objects.create(name="Klient 200", code="K200", max_pallet_weight_kg=200)
        cls.cust_loose = Customer.objects.create(name="Klient 5000", code="K5000", max_pallet_weight_kg=5000)
        cls.cust_none = Customer.objects.create(name="Klient bez limitu", code="K0")

        def sh(name, lines, **kw):
            s = Shipment.objects.create(name=name, **kw)
            for i, (p, q, u) in enumerate(lines):
                ShipmentLine.objects.create(shipment=s, product=p, quantity=q, unit=u, order=i)
            return s

        cls.sh = {
            "empty": sh("EMPTY", []),
            "all_units": sh("UNITS", [
                (cls.pA, 30, "kar"), (cls.pB, 50, "szt"), (cls.pC, 1.5, "pal"),
                (cls.pD, 2, "pal"), (cls.pE, 3, "pal")]),
            "missing_and_zero_dims": sh("MISS", [
                (cls.pX, 7, "kar"), (cls.pA, 5, "kar"), (cls.pZ, 3, "szt")]),
            "only_missing": sh("ONLYMISS", [(cls.pX, 2, "pal")]),
            # (shipment, product) jest unikalne → wariant drugiej jednostki w osobnej przesyłce.
            "marm_volume_and_versions": sh("MARM", [(cls.pF, 25, "szt"), (cls.pV, 7, "kar")]),
            "marm_volume_and_versions_2": sh("MARM2", [(cls.pF, 3, "kar"), (cls.pV, 5, "szt")]),
            "stackability": sh("STACK", [(cls.pG, 6, "kar"), (cls.pH, 10, "kar")]),
            "full_pallets_plus_remainders": sh("FULL", [
                (cls.pA, 95, "kar"), (cls.pC, 50, "kar"), (cls.pB, 400, "szt")]),
            "mixed_leftovers": sh("MIX", [
                (cls.pA, 7, "kar"), (cls.pC, 9, "kar"), (cls.pH, 11, "kar"), (cls.pF, 4, "kar")]),
            "customer_cap": sh("CAP", [(cls.pHeavy, 20, "kar")], customer=cls.cust_cap),
            "customer_loose_cap": sh("LOOSE", [(cls.pHeavy, 30, "kar")], customer=cls.cust_loose),
            "customer_no_cap": sh("NOCAP", [(cls.pHeavy, 30, "kar")], customer=cls.cust_none),
            "actual_hu_anchor": sh("HU", [(cls.pA, 95, "kar")], actual_hu_count=7, warehouse_pallets=4),
            "warehouse_anchor": sh("WH", [(cls.pA, 95, "kar")], warehouse_pallets=4),
            "small_courier": sh("SMALL", [(cls.pB, 24, "szt")]),
            "stow_zero_on_shipment": sh("STOW0", [(cls.pA, 40, "kar")], stowage_efficiency_pct=0),
            "stow_saved_55": sh("STOW55", [(cls.pA, 40, "kar")], stowage_efficiency_pct=55),
        }

    # (scenariusz, klucz przesyłki, kwargs)
    CASES = [
        ("empty", "empty", {}),
        ("all_units", "all_units", {}),
        ("all_units_no_packing", "all_units", {"with_packing": False}),
        ("missing_and_zero_dims", "missing_and_zero_dims", {}),
        ("only_missing", "only_missing", {}),
        ("marm_volume_and_versions", "marm_volume_and_versions", {}),
        ("marm_volume_and_versions_2", "marm_volume_and_versions_2", {}),
        ("stackability", "stackability", {}),
        ("full_pallets_plus_remainders", "full_pallets_plus_remainders", {}),
        ("mixed_leftovers", "mixed_leftovers", {}),
        ("customer_cap", "customer_cap", {"stow_eff": 95}),
        ("customer_loose_cap", "customer_loose_cap", {}),
        ("customer_no_cap", "customer_no_cap", {}),
        ("actual_hu_anchor", "actual_hu_anchor", {}),
        ("warehouse_anchor", "warehouse_anchor", {}),
        ("small_courier", "small_courier", {}),
        ("stow_zero_on_shipment", "stow_zero_on_shipment", {}),
        ("stow_saved_55", "stow_saved_55", {}),
        ("stow_clamped_low", "full_pallets_plus_remainders", {"stow_eff": 10}),
        ("stow_clamped_high", "full_pallets_plus_remainders", {"stow_eff": 150}),
        ("stow_string", "full_pallets_plus_remainders", {"stow_eff": "70", "with_packing": False}),
        ("custom_heights", "full_pallets_plus_remainders",
         {"heights": [250, 0, None, 150, "150", 199.7], "with_packing": False}),
        ("heights_all_falsy", "full_pallets_plus_remainders", {"heights": [0, None]}),
        ("heights_empty_list", "full_pallets_plus_remainders", {"heights": [], "with_packing": False}),
        ("borderline_range_sweep_60", "full_pallets_plus_remainders", {"stow_eff": 60, "with_packing": False}),
        ("borderline_range_sweep_77", "full_pallets_plus_remainders", {"stow_eff": 77, "with_packing": False}),
        ("borderline_range_sweep_88", "full_pallets_plus_remainders", {"stow_eff": 88, "with_packing": False}),
        ("empty_instr_map", "all_units", {"instr_map": {}, "with_packing": False}),
    ]

    def _results(self):
        return {name: _normalize(_calc_shipment_data(self.sh[key], **kw)) for name, key, kw in self.CASES}

    def test_snapshot(self):
        got = json.loads(json.dumps(self._results(), sort_keys=True, ensure_ascii=False))
        if os.environ.get("CALC_SHIPMENT_SNAPSHOT_UPDATE"):
            SNAPSHOT.parent.mkdir(exist_ok=True)
            SNAPSHOT.write_text(json.dumps(got, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                                encoding="utf-8")
        expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(sorted(got), sorted(expected))
        for name in expected:
            with self.subTest(scenario=name):
                self.assertEqual(got[name], expected[name])

    def test_prefetched_lines_and_instr_map_match_plain_call(self):
        """Ścieżka listy przesyłek (prefetch linii + instr_map strony) = zwykłe wywołanie, bez zapytań."""
        from django.db.models import Prefetch
        s = self.sh["all_units"]
        plain = _normalize(_calc_shipment_data(s, with_packing=False))
        pref = (Shipment.objects.filter(pk=s.pk)
                .prefetch_related(Prefetch("lines", queryset=ShipmentLine.objects
                                           .select_related("product").order_by("order", "id")))
                .get())
        imap = _latest_instructions([ln.product_id for ln in pref.lines.all()])
        with self.assertNumQueries(0):
            got = _normalize(_calc_shipment_data(pref, with_packing=False, instr_map=imap))
        self.assertEqual(got, plain)

    def test_latest_active_instruction_wins(self):
        imap = _latest_instructions([self.pV.pk, self.pX.pk])
        self.assertEqual(set(imap), {self.pV.pk})
        self.assertEqual(imap[self.pV.pk].version, 2)

    def test_three_reused_per_scenario_and_lines_are_same_objects(self):
        s = self.sh["mixed_leftovers"]
        calc = _calc_shipment_data(s)
        for sc in calc["scenarios"]:
            self.assertIsNotNone(sc["three"])
            self.assertEqual(sc["three"]["max_h_cm"], sc["max_h_cm"])
        self.assertTrue(all(lc["line"].shipment_id == s.pk for lc in calc["lines"]))

    def test_pal_line_without_layout_is_skipped_with_hard_warning(self):
        """Regresja: linia „pal” bez układu (layouts=[]) lub z cartons_per_pallet=0 NIE liczy się
        jako 1 karton na paletę (dawny guard `or 1` zaniżał objętość/wagę/palety i rysował
        „pełne” palety z 1 kartonem). Jak brak instrukcji: pominięta + twarde ostrzeżenie."""
        calc = _calc_shipment_data(self.sh["all_units"], with_packing=False)
        codes = {lc["product"].code for lc in calc["lines"]}
        self.assertNotIn("D-NOLAYOUT", codes)
        self.assertNotIn("E-ZEROCPP", codes)
        self.assertEqual(codes, {"A-KAR", "B-SZT", "C-PAL"})
        self.assertTrue(calc["has_missing"])
        self.assertEqual({m["code"] for m in calc["missing_products"]}, {"D-NOLAYOUT", "E-ZEROCPP"})
        self.assertIn("D-NOLAYOUT: brak układu palety (kartonów na paletę) w instrukcji — pominięto",
                      calc["errors"])
        # Karton/sztuka bez układu nadal się liczy (cpp nie jest tam potrzebne).
        s = Shipment.objects.create(name="KAR-NOLAYOUT")
        ShipmentLine.objects.create(shipment=s, product=self.pD, quantity=4, unit="kar", order=0)
        calc = _calc_shipment_data(s, with_packing=False)
        self.assertEqual([lc["n_cartons"] for lc in calc["lines"]], [4])
        self.assertFalse(calc["has_missing"])

    def test_pal_line_without_layout_draws_no_one_carton_pallets(self):
        calc = _calc_shipment_data(self.sh["all_units"])
        for sc in calc["scenarios"]:
            skus = {b.get("label") for p in sc["three"]["pallets"] for b in (p.get("boxes") or [])}
            self.assertFalse(skus & {"D-NOLAYOUT", "E-ZEROCPP"})

    def test_heights_are_rounded_not_truncated(self):
        calc = _calc_shipment_data(self.sh["small_courier"], heights=[199.7, "150,4", 180.5],
                                   with_packing=False)
        self.assertEqual([sc["max_h_cm"] for sc in calc["scenarios"]], [150, 180, 200])

    def test_invalid_heights_are_skipped_without_exception(self):
        calc = _calc_shipment_data(self.sh["small_courier"], heights=["abc", "", -20, "nan", 210],
                                   with_packing=False)
        self.assertEqual([sc["max_h_cm"] for sc in calc["scenarios"]], [210])
        calc = _calc_shipment_data(self.sh["small_courier"], heights=["abc", object()],
                                   with_packing=False)
        self.assertEqual([sc["max_h_cm"] for sc in calc["scenarios"]], [180, 225])
