"""Regresja dwóch błędów plan_racks (wh3d/model_from_layout.py), przypiętych wcześniej
jako „obecne zachowanie” w test_plan_racks_characterization:

1. Parzystość para/korytarz przy przyjętym rozstawie liczy się od ostatniego ZNANEGO odstępu
   (realny odstęp mapy albo szerokość korytarza z konfiguracji alej), a nie od indeksu linii —
   po korytarzu kolejne ciasne linie zaczynają nową parę: plecami 0,1 m, korytarz 3,0 m, …
2. Duplikat kodu lokalizacji (po normalizacji strip/upper) liczy się raz — pierwsze wystąpienie
   wygrywa (pozycja, poziom), jak w import_locations.
"""
from django.test import SimpleTestCase

from wh3d.model_from_layout import BACK_TO_BACK_M, CORRIDOR_M, plan_racks

S = 0.8   # podziałka gniazda [m] w testach rozstawu


def _row(aisle, row, stacks=(100,), col0=0, zone="B0", level=1):
    """Alejka pozioma: każdy bok = 3 kolumny (A, B, C) w wierszu `row`."""
    out, col = [], col0
    for st in stacks:
        out += [(f"{zone}-{aisle}-{st}{L}", col + i, row, level) for i, L in enumerate("ABC")]
        col += 4
    return out


def _gaps(rows, aisle_cfg=None, key="y"):
    """Odstępy (pozycja[i+1] − pozycja[i]) kolejnych regałów ułożonych w wierszach `rows`."""
    cells = []
    for i, row in enumerate(rows):
        cells += _row(f"{i + 1:02d}", row)
    racks, rep = plan_racks(cells, {}, slot_mm=int(S * 1000), aisle_cfg=aisle_cfg)
    pos = [r[key] for r in sorted(racks, key=lambda r: r["rack_id"])]
    return [round(b - a, 6) for a, b in zip(pos, pos[1:], strict=False)], racks[0]["depth"], rep


class ParityAfterKnownGapTests(SimpleTestCase):
    def assertGaps(self, got, want):
        self.assertEqual(len(got), len(want))
        for i, (g, w) in enumerate(zip(got, want, strict=True)):
            self.assertAlmostEqual(g, w, msg=f"odstęp nr {i}: {got} ≠ {want}")

    def test_tight_pair_after_real_gap_starts_back_to_back(self):
        # Scenariusz tight_parity_after_real_gap: wiersze 0 | 5 6 7 (0,8 m < głębokość 1,1 m).
        gaps, d, rep = _gaps([0, 5, 6, 7])
        self.assertTrue(rep["assumed_spacing"])
        self.assertGaps(gaps, [5 * S, d + BACK_TO_BACK_M, d + CORRIDOR_M])

    def test_longer_run_after_real_gap_alternates_from_pair(self):
        gaps, d, _ = _gaps([0, 5, 6, 7, 8])
        self.assertGaps(gaps, [5 * S, d + BACK_TO_BACK_M, d + CORRIDOR_M, d + BACK_TO_BACK_M])

    def test_real_gap_after_odd_run_resets_parity(self):
        # 0|1 para, 1→2 korytarz, 2→7 realny odstęp, 7|8 nowa para (dawniej: korytarz, k=3).
        gaps, d, _ = _gaps([0, 1, 2, 7, 8])
        self.assertGaps(gaps, [d + BACK_TO_BACK_M, d + CORRIDOR_M, 5 * S, d + BACK_TO_BACK_M])

    def test_configured_corridor_resets_parity(self):
        # Korytarz z konfiguracji alej za rzędem 01 → 02|03 to nowa para (dawniej 02 | 3 m | 03).
        gaps, d, rep = _gaps([0, 1, 2, 3], aisle_cfg={"B0-01": 1.8})
        self.assertTrue(rep["assumed_spacing"])
        self.assertGaps(gaps, [d + 1.8, d + BACK_TO_BACK_M, d + CORRIDOR_M])

    def test_tight_grid_without_known_gaps_unchanged(self):
        # Zwykła ciasna siatka: wynik identyczny jak przed poprawką (para, korytarz, para, …).
        gaps, d, rep = _gaps([0, 1, 2, 3, 4, 5])
        self.assertTrue(rep["assumed_spacing"])
        self.assertGaps(gaps, [d + BACK_TO_BACK_M, d + CORRIDOR_M] * 2 + [d + BACK_TO_BACK_M])

    def test_only_known_gaps_do_not_assume_spacing(self):
        gaps, d, rep = _gaps([0, 5, 10], aisle_cfg={"B0-02": 2.5})
        self.assertFalse(rep["assumed_spacing"])
        self.assertGaps(gaps, [5 * S, d + 2.5])

    def test_vertical_lines_reset_parity_too(self):
        # Alejki pionowe (oś x): kolumny 0 | 5 6 7.
        cells = []
        for i, col in enumerate([0, 5, 6, 7]):
            cells += [(f"B0-{i + 1:02d}-{st}A", col, j * 3, 1) for j, st in enumerate((100, 200, 300))]
        racks, _ = plan_racks(cells, {}, slot_mm=int(S * 1000))
        xs = [r["x"] for r in sorted(racks, key=lambda r: r["rack_id"])]
        d = racks[0]["depth"]
        self.assertAlmostEqual(xs[1] - xs[0], 5 * S)
        self.assertAlmostEqual(xs[2] - xs[1], d + BACK_TO_BACK_M)
        self.assertAlmostEqual(xs[3] - xs[2], d + CORRIDOR_M)


class DuplicateCodesTests(SimpleTestCase):
    MASTER = {"B0-01-100A": {"width_mm": 900, "depth_mm": 1000, "height_mm": 1000},
              "B0-01-200A": {"width_mm": 900, "depth_mm": 1400, "height_mm": 2000}}

    def test_duplicate_counted_once_in_cells_and_depth_median(self):
        # Scenariusz duplicate_codes: 100A dwa razy nie może przeważyć mediany głębokości.
        cells = [("B0-01-100A", 0, 0, 1), ("B0-01-100A", 0, 0, 1), ("B0-01-200A", 3, 0, 1)]
        racks, rep = plan_racks(cells, self.MASTER)
        self.assertEqual(rep["cells"], 2)
        self.assertEqual(racks[0]["depth_cm"], 120)            # mediana(1000, 1400), nie 1000
        self.assertAlmostEqual(racks[0]["depth"], 1.2)
        self.assertEqual(racks[0]["level_height_cm"], 215)     # max poziomu 1 = 2000 + 150 belki

    def test_first_occurrence_position_wins(self):
        # Duplikat (inna wielkość liter/spacje) w innym miejscu mapy nie wydłuża regału
        # i nie robi z alejki „wielorzędowej” — liczy się pozycja pierwszego wystąpienia.
        cells = [("B0-01-100A", 0, 0, 1), ("B0-01-200A", 3, 0, 1), ("b0-01-100a ", 20, 7, 1)]
        racks, rep = plan_racks(cells, {}, slot_mm=1000)
        self.assertEqual(rep["cells"], 2)
        self.assertAlmostEqual(racks[0]["width"], 4 * 1.0)      # kolumny 0..3, nie 0..20
        self.assertEqual(rep["multi_row_aisles"], [])

    def test_first_occurrence_level_wins_in_level_median(self):
        # Duplikat z innym poziomem nie dodaje „fantomowego” poziomu do mediany wysokości.
        cells = [("B0-01-100A", 0, 0, 1), ("B0-01-200A", 3, 0, 2), ("B0-01-100A", 0, 0, 3)]
        racks, rep = plan_racks(cells, self.MASTER, slot_mm=1000)
        r = racks[0]
        self.assertEqual(rep["cells"], 2)
        self.assertEqual(r["n_levels"], 2)                      # nie 3 z duplikatu
        self.assertEqual(r["level_height_cm"], 165)             # mediana(1000, 2000) + 150 belki
        self.assertEqual(r["depth_cm"], 120)                    # mediana(1000, 1400)

    def test_normalization_matches_parse_code(self):
        # Ten sam kod po strip/upper = ta sama lokalizacja; liczy się pierwsze wystąpienie.
        cells = [(" b0-01-100a", 5, 2, 1), ("B0-01-100A ", 0, 0, 1), ("B0-01-100A", 9, 9, 1),
                 ("B0-01-200A", 3, 2, 1)]
        racks, rep = plan_racks(cells, {}, slot_mm=1000)
        r = racks[0]
        self.assertEqual(rep["cells"], 2)
        self.assertEqual(r["n_bays"], 2)
        self.assertAlmostEqual(r["width"], 3 * 1.0)             # kolumny 3..5
        self.assertEqual(r["angle"], 180.0)                     # bok 100 (kol. 5) na prawo od 200 (kol. 3)
        self.assertEqual(rep["multi_row_aisles"], [])

    def test_duplicate_unparsable_code_skipped_once(self):
        cells = [("DOK-1", 0, 0, 1), (" dok-1 ", 1, 0, 1), ("B0-01-100A", 0, 2, 1), ("B0-01-100a", 0, 2, 1)]
        _, rep = plan_racks(cells, {}, slot_mm=1000)
        self.assertEqual((rep["skipped_codes"], rep["cells"]), (1, 1))

    def test_distinct_codes_unaffected(self):
        cells = _row("01", 0, (100, 200))
        _, rep = plan_racks(cells, {}, slot_mm=1000)
        self.assertEqual(rep["cells"], 6)
