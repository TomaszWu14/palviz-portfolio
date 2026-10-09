"""Regresje naprawionych błędów hierarchii opakowań (fix/hierarchy):

  1. `has_media` palety liczone z JSON-u (klucze `carton_artwork`/`carton_glb_url`),
     a nie podciągiem — wcześniej paleta NIGDY nie miała media, a etykieta „artwork"
     dawała fałszywe True,
  2. `with_artwork=False` pomija też grafiki kartonu na palecie (wcześniej poziom
     palety dociągał je zawsze — zbędne zapytania w macierzy Data Center),
  3. alert spójności palety był tautologią (szt/paleta liczone z tego samego iloczynu)
     — teraz porównuje OPZ/paleta × szt/OPZ z szt/paleta,
  4–6. `enrich_pallet_metrics`: spójne czyszczenie `mult_exact`, clamp/brak danych dla
     wartości ujemnych, zaokrąglenie „half up" i mnożnik ≥ 1 dla dodatniego stosunku.
"""
import json
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from testkit.factories import (CartonFactory, InnerPackFactory, InstructionFactory,
                               ProductFactory)
from ui.hierarchy import build_hierarchy, enrich_pallet_metrics
from ui.models import CartonArtwork

LAYOUT = [{"name": "L", "layers_used": 6, "cartons_per_pallet": 48,
           "placements": [{"x": 0, "y": 0, "dx": 40, "dy": 30}]}]


def _level(res, key):
    return next(lvl for lvl in res["levels"] if lvl["key"] == key)


def _carton_with_art(**kw):
    c = CartonFactory(length_cm=40, width_cm=30, height_cm=25, **kw)
    CartonArtwork.objects.create(carton=c, face="front", kind="print", x_pct=0, y_pct=0,
                                 w_pct=50, h_pct=50, rotation_deg=0, z=0, image="a/art.png")
    return c


class PalletMediaTests(TestCase):
    def test_pallet_has_media_from_carton_artwork(self):
        c = _carton_with_art(name="KAR-M")
        p = ProductFactory(code="HB-ART", unit_length_cm=None, unit_width_cm=None,
                           unit_height_cm=None)
        InstructionFactory(product=p, carton=c, pcs_per_carton=10, unit_weight=0.1,
                           layouts=LAYOUT)
        res = build_hierarchy(p)
        self.assertIn("carton_artwork", _level(res, "pallet")["three_data"])
        self.assertTrue(_level(res, "pallet")["has_media"])

    def test_pallet_has_media_from_carton_glb(self):
        c = CartonFactory(name="KAR-G", length_cm=40, width_cm=30, height_cm=25,
                          glb_model="carton_models/k.glb")
        p = ProductFactory(code="HB-GLB")
        InstructionFactory(product=p, carton=c, pcs_per_carton=10, unit_weight=0.1,
                           layouts=LAYOUT)
        self.assertTrue(_level(build_hierarchy(p), "pallet")["has_media"])

    def test_pallet_without_media(self):
        c = CartonFactory(name="KAR-N", length_cm=40, width_cm=30, height_cm=25)
        p = ProductFactory(code="HB-NONE")
        InstructionFactory(product=p, carton=c, pcs_per_carton=10, unit_weight=0.1,
                           layouts=LAYOUT)
        self.assertFalse(_level(build_hierarchy(p), "pallet")["has_media"])

    def test_label_named_artwork_is_not_media(self):
        # Etykieta = nazwa kartonu; podciąg '"artwork"' łapał ją jako grafikę.
        c = CartonFactory(name="artwork", length_cm=40, width_cm=30, height_cm=25)
        p = ProductFactory(code="HB-LBL")
        InstructionFactory(product=p, carton=c, pcs_per_carton=10, unit_weight=0.1,
                           layouts=LAYOUT)
        res = build_hierarchy(p)
        self.assertFalse(_level(res, "carton")["has_media"])
        self.assertFalse(_level(res, "pallet")["has_media"])


class WithArtworkTests(TestCase):
    def test_no_artwork_skips_pallet_carton_art(self):
        c = _carton_with_art(name="KAR-W")
        p = ProductFactory(code="HB-NOART")
        InstructionFactory(product=p, carton=c, pcs_per_carton=10, unit_weight=0.1,
                           layouts=LAYOUT)
        pallet = json.loads(_level(build_hierarchy(p, with_artwork=False), "pallet")["three_data"])
        self.assertNotIn("carton_artwork", pallet)
        pallet = json.loads(_level(build_hierarchy(p), "pallet")["three_data"])
        self.assertIn("carton_artwork", pallet)


class PalletConsistencyAlertTests(TestCase):
    def _product(self, instr_ppc, carton_ppc, pcs_ip=6, pcs_per_carton=12):
        ip = InnerPackFactory(name="OPZ-X", length_cm=20, width_cm=15, height_cm=12,
                              units_per_pack=pcs_ip)
        c = CartonFactory(name="KAR-X", length_cm=40, width_cm=30, height_cm=25,
                          inner_pack=ip, packs_per_carton=carton_ppc)
        p = ProductFactory(code="HB-ALERT")
        InstructionFactory(product=p, carton=c, inner_pack=ip, packs_per_carton=instr_ppc,
                           pcs_per_inner_pack=pcs_ip, pcs_per_carton=pcs_per_carton,
                           unit_weight=0.1, layouts=LAYOUT)
        return p

    def test_pallet_chain_mismatch_raises_alert(self):
        # Instrukcja: 2 OPZ × 6 szt = 12 szt/karton (OK), ale karton w bazie ma 4 OPZ →
        # KPI „OPZ na palecie" = 192, a 192 × 6 ≠ 576 szt/paleta.
        res = build_hierarchy(self._product(instr_ppc=2, carton_ppc=4))
        self.assertEqual(res["summary"]["packs_per_pallet"], 192)
        self.assertIn("Niespójność: 192 OPZ/paleta × 6 szt/OPZ = 1152 ≠ 576 szt/paleta",
                      res["alerts"])

    def test_consistent_chain_no_alert(self):
        self.assertEqual(build_hierarchy(self._product(instr_ppc=2, carton_ppc=2))["alerts"], [])

    def test_no_duplicate_when_carton_alert_already_raised(self):
        res = build_hierarchy(self._product(instr_ppc=3, carton_ppc=3, pcs_ip=5))
        self.assertEqual(len(res["alerts"]), 1)
        self.assertTrue(res["alerts"][0].startswith("Niespójność master daty"))


def _instr(**over):
    base = dict(max_height_total_cm=180, pallet_base_height_cm=15, pallet_length_cm=120,
                pallet_width_cm=80, carton_l=40, carton_w=30, carton_h=25, units_per_piece=1)
    base.update(over)
    return SimpleNamespace(**base)


def _summary(**over):
    base = dict(cartons_per_pallet=48, packs_per_pallet=96, sales_units_per_pallet=192,
                pcs_per_pallet=576)
    base.update(over)
    return base


class EnrichMetricsFixTests(SimpleTestCase):
    def test_rerun_clears_stale_mult_exact(self):
        levels = [{"key": "carton"}, {"key": "unit"}]
        enrich_pallet_metrics(levels, _summary(), _instr())
        enrich_pallet_metrics(levels, None, _instr())
        self.assertEqual(levels[0], {"key": "carton", "mult": None})

    def test_rerun_with_missing_count_clears_mult_exact(self):
        levels = [{"key": "carton"}, {"key": "unit"}]
        enrich_pallet_metrics(levels, _summary(), _instr())
        enrich_pallet_metrics(levels, _summary(pcs_per_pallet=None), _instr())
        self.assertEqual(levels[0], {"key": "carton", "mult": None})

    def test_negative_cpp_is_no_data(self):
        self.assertIsNone(enrich_pallet_metrics([], _summary(cartons_per_pallet=-48), _instr()))

    def test_negative_next_level_is_no_data(self):
        levels = [{"key": "carton"}, {"key": "unit"}]
        enrich_pallet_metrics(levels, _summary(pcs_per_pallet=-96), _instr())
        self.assertEqual(levels[0], {"key": "carton", "mult": None})

    def test_vol_pct_half_up(self):
        instr = _instr(pallet_length_cm=100, pallet_width_cm=100, max_height_total_cm=200,
                       pallet_base_height_cm=0, carton_l=1, carton_w=1, carton_h=1)
        self.assertEqual(enrich_pallet_metrics([], _summary(cartons_per_pallet=50000), instr), 3)
        self.assertEqual(enrich_pallet_metrics([], _summary(cartons_per_pallet=10000), instr), 1)

    def test_mult_half_up(self):
        levels = [{"key": "carton"}, {"key": "unit"}]
        enrich_pallet_metrics(levels, _summary(cartons_per_pallet=2, pcs_per_pallet=5), _instr())
        self.assertEqual(levels[0]["mult"], 3)
        self.assertFalse(levels[0]["mult_exact"])

    def test_small_positive_ratio_is_at_least_one(self):
        levels = [{"key": "carton"}, {"key": "unit"}]
        enrich_pallet_metrics(levels, _summary(cartons_per_pallet=10, pcs_per_pallet=3), _instr())
        self.assertEqual(levels[0]["mult"], 1)
        self.assertFalse(levels[0]["mult_exact"])
