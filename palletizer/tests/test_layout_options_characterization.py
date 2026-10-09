"""Testy charakteryzujące PalletCalculator.generate_layout_options (CODE-001).

Przypinają OBECNE zachowanie przed podziałem funkcji: pełna lista wariantów (nazwa,
kartony/warstwę, wykorzystanie, wszystkie rozmieszczenia) w tej samej kolejności,
porównana ze snapshotem JSON (data/layout_options_snapshot.json).

OR-Tools: ``generate_layout_options`` nie korzysta z ``ortools_layer`` (ścieżka CP-SAT
jest wołana osobno przez web/), więc wynik jest deterministyczny bez żadnego patchowania.
Test ``test_does_not_touch_ortools`` pilnuje, że tak zostanie.

Regeneracja (tylko świadomie, gdy zmiana zachowania jest zamierzona):
    LAYOUT_OPTIONS_SNAPSHOT_UPDATE=1 python -m unittest palletizer.tests.test_layout_options_characterization
"""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from palletizer.config import get_pallet_preset
from palletizer.domain import CartonVariant, Dimensions, PalletType
from palletizer.services.pallet_calculator import PalletCalculator

SNAPSHOT = Path(__file__).with_name("data") / "layout_options_snapshot.json"


def _pallet(code="EU", l=None, w=None, h=None, kg=None):
    p = get_pallet_preset(code)
    return PalletType(
        code=p.code,
        dims=Dimensions(l_cm=l or p.length_cm, w_cm=w or p.width_cm, h_cm=h or p.default_max_height_cm),
        max_weight_kg=p.max_weight_kg if kg is None else kg,
    )


def _carton(l, w, h=20, rot=True):
    return CartonVariant(
        sku="T", variant="v1", dims=Dimensions(l_cm=l, w_cm=w, h_cm=h),
        unit_weight_kg=1.0, pieces_per_carton=1, demand_pieces=100, allow_rotation=rot,
    )


def scenarios():
    """nazwa → (carton, pallet)."""
    eu, z129 = _pallet("EU"), _pallet("Z129")
    s = {
        "eu_40x30": (_carton(40, 30), eu),
        "eu_60x40": (_carton(60, 40), eu),
        "eu_30x20": (_carton(30, 20), eu),
        "eu_small_10x10_square": (_carton(10, 10), eu),
        "eu_small_12x9": (_carton(12, 9), eu),
        "eu_large_100x70": (_carton(100, 70), eu),
        "eu_large_70x50": (_carton(70, 50), eu),
        "eu_near_square_31x30": (_carton(31, 30), eu),
        "eu_near_square_39x38": (_carton(39, 38), eu),
        "eu_square_25x25": (_carton(25, 25), eu),
        "eu_narrow_115x10": (_carton(115, 10), eu),
        "eu_narrow_78x9": (_carton(78, 9), eu),
        "eu_carton_equals_pallet": (_carton(120, 80), eu),
        "eu_carton_equals_pallet_swapped": (_carton(80, 120), eu),
        "eu_odd_37x23": (_carton(37, 23), eu),
        "eu_odd_33x27": (_carton(33, 27), eu),
        "eu_odd_45x35": (_carton(45, 35), eu),
        "eu_no_rotation_37x23": (_carton(37, 23, rot=False), eu),
        "eu_no_rotation_45x35": (_carton(45, 35, rot=False), eu),
        "eu_no_rotation_square_20x20": (_carton(20, 20, rot=False), eu),
        "eu_float_39_5x29_5": (_carton(39.5, 29.5), eu),
        "eu_float_26_4x17_8": (_carton(26.4, 17.8), eu),
        "z129_40x30": (_carton(40, 30), z129),
        "z129_37x23": (_carton(37, 23), z129),
        "z129_no_rotation_33x27": (_carton(33, 27, rot=False), z129),
        "custom_120x100_40x30": (_carton(40, 30), _pallet(l=120, w=100)),
        "custom_120x100_57x43": (_carton(57, 43), _pallet(l=120, w=100)),
        "custom_60x40_19x13": (_carton(19, 13), _pallet(l=60, w=40)),
        "custom_odd_pallet_101x77_33x21": (_carton(33, 21), _pallet(l=101, w=77)),
        "rotated_only_fit_90x70_on_eu": (_carton(70, 90), eu),
    }
    return s


def _r(v):
    return round(v, 6) if isinstance(v, float) else v


def _serialize(options):
    return [
        {
            "name": o.name,
            "cartons_per_layer": o.cartons_per_layer,
            "utilization_percent": _r(o.utilization_percent),
            "placements": [[_r(p.x), _r(p.y), _r(p.dx), _r(p.dy), p.rotated] for p in o.placements],
        }
        for o in options
    ]


def _dump(data):
    """Zwarty, ale diffowalny JSON: jeden wariant na linię."""
    parts = []
    for name, opts in data.items():
        body = ",\n".join("  " + json.dumps(o, ensure_ascii=False) for o in opts)
        parts.append(f" {json.dumps(name)}: [\n{body}\n ]")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def _compute_all():
    return {name: _serialize(PalletCalculator.generate_layout_options(c, p))
            for name, (c, p) in sorted(scenarios().items())}


class LayoutOptionsCharacterizationTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.actual = _compute_all()
        if os.environ.get("LAYOUT_OPTIONS_SNAPSHOT_UPDATE"):
            SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
            SNAPSHOT.write_bytes(_dump(cls.actual).encode("utf-8"))
        cls.expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    def test_same_scenarios(self):
        self.assertEqual(sorted(self.expected), sorted(self.actual))

    def test_snapshot_per_scenario(self):
        for name, exp in self.expected.items():
            with self.subTest(scenario=name):
                self.assertEqual(self.actual.get(name), exp)

    def test_at_most_15_distinct(self):
        for name, opts in self.actual.items():
            with self.subTest(scenario=name):
                self.assertLessEqual(len(opts), 15)
                sigs = {tuple(sorted(tuple(p[:4]) for p in o["placements"])) for o in opts}
                self.assertEqual(len(sigs), len(opts))

    def test_deterministic(self):
        self.assertEqual(_compute_all(), self.actual)

    def test_does_not_touch_ortools(self):
        from palletizer.services import ortools_layer
        public = [n for n in dir(ortools_layer) if callable(getattr(ortools_layer, n)) and not n.startswith("__")]
        patches = [mock.patch.object(ortools_layer, n, side_effect=AssertionError(n))
                   for n in public if getattr(getattr(ortools_layer, n), "__module__", "") == ortools_layer.__name__]
        for p in patches:
            p.start()
        try:
            c, p_ = scenarios()["eu_40x30"]
            PalletCalculator.generate_layout_options(c, p_)
        finally:
            for p in patches:
                p.stop()

    def test_callable_via_class_only(self):
        # Obecne zachowanie: metoda NIE ma @staticmethod — działa przez klasę
        # (PalletCalculator.generate_layout_options(c, p)), wywołanie przez instancję
        # przesunęłoby argumenty. Przypięte jako stan obecny (CODE-001, nie naprawiamy tu).
        c, p = scenarios()["eu_40x30"]
        self.assertTrue(PalletCalculator.generate_layout_options(c, p))
        with self.assertRaises(TypeError):
            PalletCalculator().generate_layout_options(c, p)


if __name__ == "__main__":
    unittest.main()
