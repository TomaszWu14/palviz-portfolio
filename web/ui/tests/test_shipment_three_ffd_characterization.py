"""Testy charakteryzujące _build_shipment_three_data_ffd (CODE-001) — przypinają OBECNE zachowanie
przed podziałem funkcji na prywatne helpery.

Każdy scenariusz → pełny wynik FFD (scena 3D) streszczony do liczby palet / pudeł / wysokości /
SKU per paleta ORAZ zahaszowany (sha256 z kanonicznego JSON całej sceny, łącznie z pozycjami
pudeł), porównywany ze snapshotem data/shipment_three_ffd_snapshot.json. Wejście to czysty dict
(bez ORM/PK), wynik nie zależy od czasu ani OR-Tools. Regeneracja (tylko świadomie, gdy zmiana
zachowania jest zamierzona):
    SHIPMENT_THREE_FFD_SNAPSHOT_UPDATE=1 python manage.py test ui.tests.test_shipment_three_ffd_characterization
"""
import hashlib
import json
import os
from pathlib import Path

from django.test import SimpleTestCase

from ui.views.core.helpers import _build_shipment_three_data_ffd

SNAPSHOT = Path(__file__).with_name("data") / "shipment_three_ffd_snapshot.json"


def _prod(code):
    return type("P", (), {"code": code})()


def _line(code, n, dims=(40, 30, 20), w=5.0, color="#abcdef", **extra):
    lc = {"product": _prod(code), "n_cartons": n, "carton_dims": dims,
          "carton_weight_kg": w, "color": color}
    lc.update(extra)
    return lc


def _paz(code, pallets, cpp, dims=(40, 30, 20), w=5.0, color="#123456"):
    return _line(code, pallets * (cpp or 0), dims=dims, w=w, color=color, unit="pal",
                 paz_pallets=pallets, cartons_per_pallet=cpp)


def _summary(data):
    if data is None:
        return None
    canon = json.dumps(data, sort_keys=True, separators=(",", ":"))
    head = {k: v for k, v in data.items() if k != "pallets"}
    return {
        **head,
        "sha256": hashlib.sha256(canon.encode("utf-8")).hexdigest(),
        "pallets": [
            {"n_boxes": len(p["boxes"]), "height_cm": p["height_cm"], "mixed": p["mixed"],
             "empty": p.get("empty"), "skus": sorted({b["label"] for b in p["boxes"]})}
            for p in data["pallets"]
        ],
    }


def _within_tolerance(box, over=2):
    """Pudło mieści się w obrysie palety 120×80 + zwisie `over` cm na stronę."""
    (x, _y, z), (dl, dw, _dh) = box["pos"], box["dims"]
    return abs(x) + dl / 2 <= 60 + over + 0.05 and abs(z) + dw / 2 <= 40 + over + 0.05


# (nazwa scenariusza, linie, kwargs dla _build_shipment_three_data_ffd)
def _cases():
    return [
        ("empty_lines", [], {}),
        ("only_zero_quantities", [_line("Z", 0), _line("Z2", -3)], {}),
        ("only_oversize_carton", [_line("BIG", 5, dims=(130, 90, 30))], {}),
        ("oversize_skipped_others_kept",
         [_line("BIG", 5, dims=(90, 90, 30)), _line("OK", 10)], {}),
        ("mono_exact_full_pallets", [_line("A", 144)], {"max_h": 200}),
        ("mono_full_plus_remainder", [_line("A", 150, color="#3b82f6")], {"max_h": 200}),
        ("mixed_leftovers", [
            _line("A", 7, dims=(60, 40, 40)), _line("B", 9, dims=(40, 30, 25), color="#f59e0b"),
            _line("C", 11, dims=(30, 30, 30), color="#10b981"),
            _line("D", 4, dims=(40, 40, 40), color="#ef4444")], {}),
        ("dominant_sku", [
            _line("DOM", 60, color="#3b82f6"),
            _line("X", 4, dims=(40, 30, 25), color="#ef4444"),
            _line("Y", 3, dims=(40, 30, 25), color="#10b981")], {}),
        ("rotation_layer_turn", [_line("T", 8, dims=(40, 30, 20))], {"max_h": 34}),
        ("rotation_layer_no_turn", [_line("S", 8, dims=(30, 40, 20))], {"max_h": 34}),
        ("pool_rotates_wide_carton", [_line("W", 3, dims=(50, 85, 20))], {"max_h": 120}),
        # Karton 83×85 w puli: wejściowa orientacja wystaje (85 > 80+2·2), obrócona 85×83
        # mieści się w tolerancji → pula go obraca (dawniej cicho znikał z widoku 3D).
        ("pool_drops_rotatable_carton", [_line("R", 1, dims=(83, 85, 20))], {"max_h": 60}),
        # Remis per-layer (1 = 1): wybierana orientacja mieszcząca się w obrysie + tolerancji
        # (85×83), a nie wejściowa 83×85 z głębokością 85 > 80+2·2.
        ("mono_overhang_tie_orientation", [_line("R", 6, dims=(83, 85, 20))], {"max_h": 60}),
        ("weight_cap_limits_layers", [_line("HEAVY", 100, w=50.0)], {"max_h": 240, "max_w": 1000}),
        ("weight_cap_below_one_layer", [_line("HV", 12, w=200.0)], {"max_h": 200, "max_w": 1000}),
        # Karton cięższy niż limit palety: nadal rysowany (towar nie znika z widoku), każdy
        # na osobnej palecie ponad limit — scena niesie jawne ostrzeżenie (klucz "warnings").
        ("carton_heavier_than_cap", [_line("OVER", 2, w=1500.0)], {"max_h": 200, "max_w": 1000}),
        ("no_weight_limit_zero", [_line("HEAVY", 100, w=50.0)], {"max_h": 240, "max_w": 0}),
        ("no_weight_limit_none", [_line("HEAVY", 30, w=50.0)], {"max_h": 240, "max_w": None}),
        ("missing_weight_defaults_1kg", [_line("NW", 20, w=None), _line("NW0", 5, w=0)], {}),
        ("max_layers_cap", [_line("ML", 40, max_layers=2)], {"max_h": 200}),
        # Karton wyższy niż ładunek: lmax_h=max(1,…) → mono-palety o 1 warstwie ponad limitem
        # wysokości; resztki w puli też lądują pojedynczo (każda na nowej palecie). Układ bez
        # zmian, ale scena niesie jawne ostrzeżenie (klucz "warnings").
        ("carton_taller_than_load", [_line("TALL", 11, dims=(40, 30, 200))], {"max_h": 150}),
        ("paz_whole_at_build_height", [_paz("PAZ", 2, 30)], {"max_h": 225}),
        ("paz_no_cpp_uses_geometry",
         [_line("PAZ", 50, unit="pal", paz_pallets=1, cartons_per_pallet=None)], {"max_h": 230}),
        ("paz_zero_pallets_packs_cartons",
         [dict(_line("PZ", 20, unit="pal", paz_pallets=0, cartons_per_pallet=30))], {"max_h": 225}),
        ("paz_broken_below_build_height", [_paz("PAZ", 2, 30)], {"max_h": 180}),
        ("target_pads_empty_slack", [
            _line("A", 18, dims=(40, 30, 30)),
            _line("B", 14, dims=(60, 40, 20), color="#f59e0b")],
         {"max_h": 220, "target_bins": 5}),
        # Monopalety (3) ≥ target → pool_target=0 → bez „dociśnięcia”.
        ("target_not_above_mono", [
            _line("A", 30, dims=(50, 40, 30)),
            _line("B", 25, dims=(40, 30, 35), color="#f59e0b"),
            _line("C", 20, dims=(30, 20, 15), color="#10b981")],
         {"max_h": 120, "target_bins": 1}),
        # Tylko pula; naturalnie >1 paleta, target=1 → przepakowanie do 1 palety bez limitu wysokości.
        ("target_forces_repack", [
            _line("A", 10, dims=(50, 40, 30)),
            _line("B", 20, dims=(40, 30, 35), color="#f59e0b"),
            _line("C", 20, dims=(30, 20, 15), color="#10b981")],
         {"max_h": 120, "target_bins": 1}),
        # 1 monopaleta + pula; target=2 → pool_target=1 < naturalnej liczby → przepakowanie.
        ("target_mono_and_pool_repack", [
            _line("M", 12, dims=(50, 40, 30)),
            _line("B", 20, dims=(40, 30, 35), color="#f59e0b"),
            _line("C", 20, dims=(30, 20, 15), color="#10b981")],
         {"max_h": 120, "target_bins": 2}),
        # Pula mieści się w budżecie → brak przepakowania, dopełnienie pustymi.
        ("target_pool_fits_budget", [_line("A", 10), _line("B", 5, dims=(50, 40, 30))],
         {"max_h": 200, "target_bins": 3}),
        ("target_below_mono_count", [_line("A", 300)], {"max_h": 200, "target_bins": 1}),
        ("target_mono_plus_repack", [
            _line("A", 150), _line("B", 40, dims=(50, 40, 30), color="#f59e0b"),
            _line("C", 30, dims=(30, 30, 30), color="#10b981")],
         {"max_h": 150, "target_bins": 4}),
        ("render_cap_truncates", [_line("A", 300)], {"max_h": 200, "render_cap": 1}),
        ("render_cap_none", [_line("A", 300)], {"max_h": 200, "render_cap": None}),
        ("render_cap_with_slack", [_line("A", 10)],
         {"max_h": 200, "target_bins": 4, "render_cap": 2}),
        ("mixed_heights_settle", [
            _line("T", 24, dims=(40, 30, 30)),
            _line("S", 24, dims=(40, 30, 10), color="#f59e0b")], {}),
    ]


def _run(lines, kwargs):
    kw = {"max_h": 200, "max_w": 1000}
    kw.update(kwargs)
    return _build_shipment_three_data_ffd({"lines": lines}, **kw)


class ShipmentThreeFfdCharacterizationTests(SimpleTestCase):
    maxDiff = None

    def test_snapshot(self):
        actual = {name: _summary(_run(lines, kw)) for name, lines, kw in _cases()}
        if os.environ.get("SHIPMENT_THREE_FFD_SNAPSHOT_UPDATE"):
            SNAPSHOT.write_text(json.dumps(actual, indent=1, ensure_ascii=False, sort_keys=True)
                                + "\n", encoding="utf-8")
        expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(sorted(expected), sorted(actual))
        for name in expected:
            with self.subTest(scenario=name):
                self.assertEqual(expected[name], actual[name])

    def test_deterministic(self):
        for name, lines, kw in _cases():
            with self.subTest(scenario=name):
                self.assertEqual(_summary(_run(lines, kw)), _summary(_run(lines, kw)))

    def test_empty_input_returns_none(self):
        self.assertIsNone(_run([], {}))
        self.assertIsNone(_run([_line("Z", 0)], {}))
        self.assertIsNone(_run([_line("BIG", 3, dims=(130, 90, 30))], {}))

    def test_dominant_sku_never_scattered(self):
        data = _run([_line("DOM", 150), _line("X", 5, dims=(40, 30, 25)),
                     _line("Y", 4, dims=(30, 30, 25))], {})
        with_dom = [p for p in data["pallets"] if "DOM" in {b["label"] for b in p["boxes"]}]
        mixed_dom = [p for p in with_dom if p["mixed"]]
        self.assertGreaterEqual(len(with_dom) - len(mixed_dom), 1)
        self.assertLessEqual(len(mixed_dom), 1)

    def test_rotatable_carton_turned_into_tolerance(self):
        # Pula: 83×85 nie mieści się (85 > 84), obrócony 85×83 tak → karton jest rysowany.
        data = _run([_line("R", 1, dims=(83, 85, 20))], {"max_h": 60})
        self.assertEqual(data["n_pallets"], 1)
        self.assertEqual([b["dims"] for p in data["pallets"] for b in p["boxes"]], [[85, 83, 20]])

    def test_tie_orientation_prefers_fitting(self):
        # Remis per-layer → orientacja mieszcząca się w obrysie + tolerancji (85×83).
        data = _run([_line("R", 6, dims=(83, 85, 20))], {"max_h": 60})
        boxes = [b for p in data["pallets"] for b in p["boxes"]]
        self.assertEqual({tuple(b["dims"]) for b in boxes}, {(85, 83, 20)})
        self.assertEqual(len(boxes), 6)
        self.assertTrue(all(_within_tolerance(b) for b in boxes), boxes)

    def test_layer_orientation_never_overhangs_beyond_tolerance(self):
        # Nie tylko remis: orientacja z większą liczbą kartonów/warstwę, ale wystająca poza
        # 80+2·2 (np. 30×100 → 4/warstwę z głębokością 100), nie może wygrać z mieszczącą się.
        for dims in [(83, 85, 20), (85, 83, 20), (30, 100, 20), (100, 30, 20), (50, 85, 20)]:
            with self.subTest(dims=dims):
                data = _run([_line("O", 36, dims=dims)], {"max_h": 200})
                boxes = [b for p in data["pallets"] for b in p["boxes"]]
                self.assertEqual(len(boxes), 36)                  # nic nie znika z widoku
                mono = [b for p in data["pallets"] if len(p["boxes"]) > 1 for b in p["boxes"]]
                self.assertTrue(mono)
                self.assertTrue(all(_within_tolerance(b) for b in mono), dims)

    def test_carton_heavier_than_cap_warned_and_still_drawn(self):
        data = _run([_line("OVER", 2, w=1500.0)], {"max_h": 200, "max_w": 1000})
        self.assertEqual([len(p["boxes"]) for p in data["pallets"]], [1, 1])   # nadal rysowany
        self.assertEqual(data["warnings"], [
            "Karton OVER (1500 kg) cięższy niż limit palety (1000 kg)"
            " — paleta z nim przekracza limit wagi"])

    def test_heavy_carton_without_weight_limit_not_warned(self):
        for mw in (0, None):
            with self.subTest(max_w=mw):
                self.assertNotIn("warnings", _run([_line("OVER", 2, w=1500.0)], {"max_w": mw}))

    def test_carton_taller_than_load_warned_layout_unchanged(self):
        data = _run([_line("TALL", 11, dims=(40, 30, 200))], {"max_h": 150})
        self.assertEqual([len(p["boxes"]) for p in data["pallets"]], [8, 1, 1, 1])
        self.assertEqual({p["height_cm"] for p in data["pallets"]}, {214.0})
        self.assertEqual(data["warnings"], [
            "Karton TALL (wys. 200 cm) wyższy niż limit ładunku (136 cm przy palecie 150 cm)"
            " — paleta z nim przekracza limit wysokości"])

    def test_warnings_only_where_limits_exceeded(self):
        # Klucz "warnings" pojawia się tylko przy przekroczeniu — pozostałe sceny bez zmian
        # (m.in. warstwa cięższa niż limit, ale pojedynczy karton lżejszy: brak ostrzeżenia).
        warned = {name for name, lines, kw in _cases()
                  if "warnings" in (_run(lines, kw) or {})}
        self.assertEqual(warned, {"carton_heavier_than_cap", "carton_taller_than_load",
                                  "oversize_skipped_others_kept"})

    def test_oversize_carton_warned_not_silently_dropped(self):
        # Karton większy niż paleta w obu orientacjach nie trafia do 3D, ale scena to mówi.
        data = _run([_line("BIG", 5, dims=(90, 90, 30)), _line("OK", 10)], {})
        self.assertEqual(data["warnings"], [
            "Karton BIG (90×90 cm) większy niż paleta 120×80 cm (+2 cm zwisu) — "
            "5 szt. pominięto w modelu 3D"])
        self.assertNotIn("BIG", {b["label"] for p in data["pallets"] for b in p["boxes"]})

    def test_repack_to_target_warns_overweight(self):
        # „Dociśnij do wyceny” przepakowuje do budżetu palet z pominięciem limitów — dla
        # wagi to realne ryzyko (przeładowana paleta), więc scena to mówi (rozmieszczenie
        # bez zmian). Pojedynczy karton jest lżejszy niż limit — brak ostrzeżenia „karton”.
        # 3 SKU × 3 kartony po 40 kg (resztki poniżej pełnej palety → pula), limit 200 kg:
        # naturalnie 2 palety, wycena zakłada 1 → przepakowanie do 1 palety 360 kg.
        data = _run([_line(c, 3, dims=(60, 40, 30), w=40.0) for c in ("HA", "HB", "HC")],
                    {"max_h": 200, "max_w": 200, "target_bins": 1})
        self.assertEqual(data["n_pallets"], 1)
        self.assertEqual(data["warnings"], [
            "Dociśnięcie do wyceny (1 palet): 1 palet przekracza limit wagi 200 kg "
            "(maks. 360 kg)"])

    def test_repack_within_weight_no_warning(self):
        data = _run([_line("A", 10, dims=(50, 40, 30)), _line("B", 20, dims=(40, 30, 35))],
                    {"max_h": 120, "max_w": 1000, "target_bins": 1})
        self.assertNotIn("warnings", data)

    def test_repack_to_target_exceeds_height(self):
        # „Dociśnij do wyceny”: przepakowanie do budżetu ignoruje limit wysokości (zamierzone).
        data = _run([_line("A", 10, dims=(50, 40, 30)),
                     _line("B", 20, dims=(40, 30, 35)),
                     _line("C", 20, dims=(30, 20, 15))], {"max_h": 120, "target_bins": 1})
        self.assertEqual(data["n_pallets"], 1)
        self.assertEqual(len(data["pallets"][0]["boxes"]), 50)
        self.assertGreater(data["pallets"][0]["height_cm"], 120)
