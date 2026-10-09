"""Polityka bramki kontroli HU — testowana bez HTTP (SimpleTestCase, zero DB, zero request).

To jest zysk deepeningu #1: kolejność ~7 bramek i ich komunikaty/redirecty były dawniej
osiągalne tylko przez POST z podrasowaną sesją; teraz to czyste wywołania funkcji.
"""
from django.test import SimpleTestCase

from huctl.count_policy import (count_gate, whole_units_ok,
                                photo_required_for_flags, is_blind_recount_needed)


_OK_KW = dict(zone_label="A", zone_ok=True, type_ok=True, hu_status="in_control",
              recheck=False, recheck_by_original=False, scan_src="scan", enforce=False)


class CountGateOrder(SimpleTestCase):
    def test_all_pass(self):
        self.assertTrue(count_gate(**_OK_KW).ok)

    def test_zone_first_and_names_zone(self):
        r = count_gate(**{**_OK_KW, "zone_ok": False, "zone_label": "B0"})
        self.assertFalse(r.ok)
        self.assertIn("B0", r.message)          # nazwa strefy interpolowana
        self.assertEqual(r.redirect_to, "menu")

    def test_type_before_status(self):
        # typ NIE pod kontrolą + status terminalny → wygrywa typ (wcześniejszy w kolejności)
        r = count_gate(**{**_OK_KW, "type_ok": False, "hu_status": "ok"})
        self.assertIn("nie jest objęty", r.message)
        self.assertEqual(r.redirect_to, "menu")

    def test_terminal_statuses(self):
        for status, frag in (("ok", "zablokowany"), ("escaped", "wyjechało")):
            r = count_gate(**{**_OK_KW, "hu_status": status})
            self.assertFalse(r.ok, status)
            self.assertEqual(r.redirect_to, "detail")
            self.assertIn(frag, r.message)

    def test_recheck_by_original_blocks(self):
        r = count_gate(**{**_OK_KW, "recheck": True, "recheck_by_original": True})
        self.assertIn("inny kontroler", r.message)
        # ta sama osoba, ale NIE rekontrola → przechodzi (guard tylko w recheck)
        self.assertTrue(count_gate(**{**_OK_KW, "recheck": False, "recheck_by_original": True}).ok)

    def test_scan_enforce_only_when_enabled(self):
        blocked = count_gate(**{**_OK_KW, "enforce": True, "scan_src": None})
        self.assertIn("SKANEREM", blocked.message)
        # flaga wyłączona → brak skanu nie blokuje
        self.assertTrue(count_gate(**{**_OK_KW, "enforce": False, "scan_src": None}).ok)
        # zeskanowane → przechodzi mimo enforce
        self.assertTrue(count_gate(**{**_OK_KW, "enforce": True, "scan_src": "scan"}).ok)

    def test_scan_beats_nothing_after_it(self):
        # scan-enforce jest ostatnią bramką pre-foto; wcześniejsza (strefa) ma pierwszeństwo
        r = count_gate(**{**_OK_KW, "zone_ok": False, "enforce": True, "scan_src": None})
        self.assertEqual(r.redirect_to, "menu")   # to strefa, nie scan


class Predicates(SimpleTestCase):
    def test_whole_units(self):
        self.assertTrue(whole_units_ok(15.0))
        self.assertTrue(whole_units_ok(15.03))     # w 2-dec tolerancji
        self.assertFalse(whole_units_ok(15.5))

    def test_photo_required(self):
        self.assertEqual(photo_required_for_flags({"damaged": True}, has_photo=False), "Uszkodzony towar")
        self.assertEqual(photo_required_for_flags({"bad_placement": True}, has_photo=False), "Nieprawidłowe ułożenie")
        self.assertIsNone(photo_required_for_flags({"damaged": True}, has_photo=True))   # foto dołączone
        self.assertIsNone(photo_required_for_flags({"wrong_batch": True}, has_photo=False))  # nie wymaga foto

    def test_blind_recount(self):
        # rozjazd bez potwierdzenia/foto/recheck → wymuś przeliczenie
        self.assertTrue(is_blind_recount_needed(10.0, 12.0, sure=False, has_photo=False, recheck=False))
        # zgodność → nie
        self.assertFalse(is_blind_recount_needed(12.0, 12.0, sure=False, has_photo=False, recheck=False))
        # potwierdził / foto / recheck → pomiń prompt mimo rozjazdu
        self.assertFalse(is_blind_recount_needed(10.0, 12.0, sure=True, has_photo=False, recheck=False))
        self.assertFalse(is_blind_recount_needed(10.0, 12.0, sure=False, has_photo=True, recheck=False))
        self.assertFalse(is_blind_recount_needed(10.0, 12.0, sure=False, has_photo=False, recheck=True))


class SharedThresholds(SimpleTestCase):
    """CODE-004: tolerancja liczenia i limit wierszy importu żyją w jednym miejscu."""

    def test_qty_mismatch_uses_tolerance(self):
        from huctl.count_policy import COUNT_TOLERANCE, qty_mismatch
        self.assertFalse(qty_mismatch(12.04, 12.0))
        self.assertTrue(qty_mismatch(12.0 + COUNT_TOLERANCE + 0.01, 12.0))

    def test_no_duplicated_literals(self):
        import pathlib
        import re
        web = pathlib.Path(__file__).resolve().parents[2]
        offenders = []
        for app in ("huctl", "transport", "ui"):
            for p in (web / app).rglob("*.py"):
                if "tests" in p.parts or "migrations" in p.parts:
                    continue
                text = p.read_text(encoding="utf-8")
                if re.search(r"len\(rows\)\s*>\s*\d", text):
                    offenders.append(f"{p.name}: literalny limit wierszy (użyj MAX_IMPORT_ROWS)")
                if app == "huctl" and p.name != "count_policy.py" and re.search(r"[<>]=?\s*0\.05\b", text):
                    offenders.append(f"{p.name}: literalna tolerancja 0.05 (użyj qty_mismatch)")
        self.assertEqual(offenders, [])
