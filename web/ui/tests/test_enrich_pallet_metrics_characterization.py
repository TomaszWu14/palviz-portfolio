"""Testy charakteryzacyjne `enrich_pallet_metrics` (CODE-001) — przypinają DOKŁADNE
obecne zachowanie (vol_pct + per-poziom `mult`/`mult_exact`) dla każdej gałęzi,
zanim funkcja zostanie podzielona. Refaktor ma zostawić je zielone bez zmiany
oczekiwań. Funkcja jest czysta (bez bazy) → instrukcja jako SimpleNamespace.

Dawne dziwactwa przypięte w CODE-001 zostały ŚWIADOMIE naprawione (fix/hierarchy),
a odpowiednie przypadki zaktualizowane:
  • `mult_exact` czyszczony spójnie z `mult` (ponowne wywołanie nie zostawia flagi),
  • zaokrąglenie „half up" (2.5 → 3) zamiast bankierskiego round(),
  • ujemne cpp → vol_pct None (brak danych); ujemny licznik → mult None,
  • dodatni stosunek < 0.5 → `mult=1` (min. ×1, `mult_exact=False`), nie 0.
"""
from types import SimpleNamespace

from django.test import SimpleTestCase

from ui.hierarchy import enrich_pallet_metrics

KEYS = ["pallet", "carton", "inner_pack", "sales_unit", "unit", "ju"]


def _instr(**over):
    base = dict(max_height_total_cm=180, pallet_base_height_cm=15, pallet_length_cm=120,
                pallet_width_cm=80, carton_l=40, carton_w=30, carton_h=25, units_per_piece=1)
    base.update(over)
    return SimpleNamespace(**base)


def _levels(*keys):
    return [{"key": k} for k in (keys or KEYS)]


def _summary(**over):
    base = dict(cartons_per_pallet=48, packs_per_pallet=96, sales_units_per_pallet=192,
                pcs_per_pallet=576)
    base.update(over)
    return base


def _run(levels, summary, instr):
    vol = enrich_pallet_metrics(levels, summary, instr)
    return vol, [{k: v for k, v in lvl.items() if k != "key"} for lvl in levels]


class VolPctTests(SimpleTestCase):
    def test_full_data(self):
        # 48 × 30000 / (120×80×165=1584000) = 90.9 → 91
        self.assertEqual(enrich_pallet_metrics([], _summary(), _instr()), 91)

    def test_capped_at_100(self):
        self.assertEqual(enrich_pallet_metrics([], _summary(cartons_per_pallet=500), _instr()), 100)

    def test_no_instr(self):
        self.assertIsNone(enrich_pallet_metrics([], _summary(), None))

    def test_no_summary(self):
        self.assertIsNone(enrich_pallet_metrics([], None, _instr()))
        self.assertIsNone(enrich_pallet_metrics([], {}, _instr()))

    def test_zero_or_missing_cpp(self):
        self.assertIsNone(enrich_pallet_metrics([], _summary(cartons_per_pallet=0), _instr()))
        self.assertIsNone(enrich_pallet_metrics([], _summary(cartons_per_pallet=None), _instr()))

    def test_zero_pallet_volume(self):
        self.assertIsNone(enrich_pallet_metrics([], _summary(), _instr(pallet_length_cm=None)))
        self.assertIsNone(enrich_pallet_metrics([], _summary(), _instr(max_height_total_cm=15)))

    def test_negative_usable_height(self):
        self.assertIsNone(enrich_pallet_metrics([], _summary(), _instr(max_height_total_cm=10)))

    def test_none_heights_treated_as_zero(self):
        # max=None → usable_h = 0 - 15 < 0 → brak; base=None → usable_h = 180.
        self.assertIsNone(enrich_pallet_metrics([], _summary(), _instr(max_height_total_cm=None)))
        # 48 × 30000 / (120×80×180=1728000) = 83.3 → 83
        self.assertEqual(
            enrich_pallet_metrics([], _summary(), _instr(pallet_base_height_cm=None)), 83)

    def test_zero_carton_volume(self):
        self.assertIsNone(enrich_pallet_metrics([], _summary(), _instr(carton_h=None)))
        self.assertIsNone(enrich_pallet_metrics([], _summary(), _instr(carton_l=0)))

    def test_negative_cpp_is_no_data(self):
        # Naprawione: ujemne cpp = brak danych (wcześniej -91%).
        self.assertIsNone(enrich_pallet_metrics([], _summary(cartons_per_pallet=-48), _instr()))

    def test_half_up_rounding(self):
        # paleta 100×100×200 = 2e6 cm³, karton 1 cm³: 10000 szt → 0.5% → 1; 50000 → 2.5% → 3
        # (wcześniej bankierskie round(): 0 i 2).
        instr = _instr(pallet_length_cm=100, pallet_width_cm=100, max_height_total_cm=200,
                       pallet_base_height_cm=0, carton_l=1, carton_w=1, carton_h=1)
        self.assertEqual(enrich_pallet_metrics([], _summary(cartons_per_pallet=10000), instr), 1)
        self.assertEqual(enrich_pallet_metrics([], _summary(cartons_per_pallet=50000), instr), 3)


class MultTests(SimpleTestCase):
    maxDiff = None

    def test_all_levels_consistent(self):
        vol, out = _run(_levels(), _summary(), _instr(units_per_piece=3))
        self.assertEqual(vol, 91)
        self.assertEqual(out, [
            {"mult": 48, "mult_exact": True},
            {"mult": 2, "mult_exact": True},
            {"mult": 2, "mult_exact": True},
            {"mult": 3, "mult_exact": True},
            {"mult": 3, "mult_exact": True},   # OP→JU = units_per_piece
            {"mult": None},                    # ostatni poziom
        ])

    def test_missing_optional_levels_skipped(self):
        _, out = _run(_levels("pallet", "carton", "unit"), _summary(), _instr())
        self.assertEqual(out, [{"mult": 48, "mult_exact": True},
                               {"mult": 12, "mult_exact": True}, {"mult": None}])

    def test_inexact_ratio_flagged(self):
        _, out = _run(_levels("carton", "unit"), _summary(pcs_per_pallet=500), _instr())
        # 500/48 = 10.42 → 10, niecałkowity
        self.assertEqual(out, [{"mult": 10, "mult_exact": False}, {"mult": None}])

    def test_half_up_rounding_and_small_ratio(self):
        _, out = _run(_levels("carton", "unit"), _summary(cartons_per_pallet=2, pcs_per_pallet=5),
                      _instr())
        self.assertEqual(out[0], {"mult": 3, "mult_exact": False})   # 2.5 → 3 (half up)
        _, out = _run(_levels("carton", "unit"), _summary(cartons_per_pallet=10, pcs_per_pallet=3),
                      _instr())
        self.assertEqual(out[0], {"mult": 1, "mult_exact": False})   # 0.3 → min. ×1

    def test_missing_counts_give_none(self):
        _, out = _run(_levels(), _summary(packs_per_pallet=None, sales_units_per_pallet=0,
                                          pcs_per_pallet=None), _instr())
        self.assertEqual(out, [{"mult": 48, "mult_exact": True}] + [{"mult": None}] * 5)

    def test_negative_lower_level_is_skipped(self):
        _, out = _run(_levels("carton", "unit"), _summary(cartons_per_pallet=-4), _instr())
        self.assertEqual(out, [{"mult": None}, {"mult": None}])

    def test_negative_next_level_is_no_data(self):
        # Naprawione: ujemny licznik = brak danych (wcześniej mult=-2).
        _, out = _run(_levels("carton", "unit"), _summary(pcs_per_pallet=-96), _instr())
        self.assertEqual(out[0], {"mult": None})

    def test_unknown_key(self):
        _, out = _run(_levels("pallet", "layer", "carton"), _summary(), _instr())
        self.assertEqual(out, [{"mult": None}, {"mult": None}, {"mult": None}])

    def test_no_summary_all_none(self):
        for summary in (None, {}):
            vol, out = _run(_levels(), summary, _instr())
            self.assertIsNone(vol)
            self.assertEqual(out, [{"mult": None}] * 6)

    def test_summary_without_cpp_still_multiplies(self):
        vol, out = _run(_levels("inner_pack", "unit"),
                        {"packs_per_pallet": 10, "pcs_per_pallet": 60}, _instr())
        self.assertIsNone(vol)
        self.assertEqual(out, [{"mult": 6, "mult_exact": True}, {"mult": None}])

    def test_no_instr_upp_is_one(self):
        vol, out = _run(_levels("unit", "ju"), _summary(), None)
        self.assertIsNone(vol)
        self.assertEqual(out, [{"mult": 1, "mult_exact": True}, {"mult": None}])

    def test_upp_none_or_zero_is_one(self):
        for upp in (None, 0):
            _, out = _run(_levels("unit", "ju"), _summary(), _instr(units_per_piece=upp))
            self.assertEqual(out[0], {"mult": 1, "mult_exact": True})

    def test_empty_levels(self):
        self.assertEqual(_run([], _summary(), _instr()), (91, []))

    def test_single_level(self):
        self.assertEqual(_run(_levels("pallet"), _summary(), _instr()), (91, [{"mult": None}]))

    def test_rerun_clears_mult_exact(self):
        # Naprawione: `mult_exact` czyszczony razem z `mult` (wcześniej stara flaga zostawała).
        levels = _levels("carton", "unit")
        enrich_pallet_metrics(levels, _summary(), _instr())
        enrich_pallet_metrics(levels, None, _instr())
        self.assertEqual(levels[0], {"key": "carton", "mult": None})

    def test_mutates_in_place_and_keeps_other_keys(self):
        levels = [{"key": "carton", "label": "x"}, {"key": "unit", "mult": 99}]
        enrich_pallet_metrics(levels, _summary(), _instr())
        self.assertEqual(levels, [{"key": "carton", "label": "x", "mult": 12, "mult_exact": True},
                                  {"key": "unit", "mult": None}])
