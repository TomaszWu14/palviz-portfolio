"""Testy charakteryzujące phv_data._storage_strategy (CODE-001) — przypinają OBECNE zachowanie
przed podziałem na helpery.

Pełny wynik _storage_strategy() dla zestawu scenariuszy (budowanych w DB, każdy w osobnym
savepoincie wycofywanym po odczycie) porównywany ze snapshotem JSON
(data/storage_strategy_snapshot.json). Wynik nie zawiera PK — tylko kody HU/lokalizacji.
Regeneracja (tylko świadomie, gdy zmiana zachowania jest zamierzona):
    STORAGE_STRATEGY_SNAPSHOT_UPDATE=1 python manage.py test ui.tests.test_storage_strategy_characterization
"""
import datetime as dt
import json
import os
from pathlib import Path

from django.db import transaction
from django.test import TestCase

from ui.models import (FixLocation, HandlingUnit, HandlingUnitItem, Product, Shipment,
                       WarehouseLocationMaster, WarehouseLocationMasterBatch)
from ui.views.phv import _storage_strategy

SNAPSHOT = Path(__file__).with_name("data") / "storage_strategy_snapshot.json"

D = dt.date


class _Builder:
    """Mały builder stocku: każda paleta (HU) w osobnym shipmencie stockowym."""

    def __init__(self, code="REF-X"):
        self.p = Product.objects.create(code=code, name="X")
        self.n = 0

    def hu(self, wt, loc, items=((10, None, ""),), status="", dims=None, code=None,
           is_stock=True, ref=None, link_product=True):
        self.n += 1
        sh = Shipment.objects.create(name=f"S{self.n}", is_stock=is_stock)
        l_, w_, h_ = dims or (None, None, None)
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code=code if code is not None else f"HU{self.n:03d}",
                                         warehouse_type=wt, location=loc, stock_status=status,
                                         length_cm=l_, width_cm=w_, height_cm=h_)
        for qty, expiry, lot in items:
            HandlingUnitItem.objects.create(hu=hu, product=self.p if link_product else None,
                                            ref_code=ref or self.p.code, expected_qty=qty,
                                            expiry=expiry, lot=lot)
        return hu

    def fix(self, loc, min_qty=0, max_qty=0, wh="", uom="", ref=None):
        FixLocation.objects.create(location_code=loc, ref_code=ref or self.p.code, warehouse_type=wh,
                                   min_qty=min_qty, max_qty=max_qty, uom=uom)


def _master(locs, active=True, pick=False, put=False):
    b = WarehouseLocationMasterBatch.objects.create(name="M", is_active=active)
    for loc in locs:
        WarehouseLocationMaster.objects.create(batch=b, location_code=loc, blocked_pick=pick, blocked_put=put)


# ─── scenariusze ─────────────────────────────────────────────────────────────

def sc_empty():
    return _Builder().p


def sc_processes():
    """Procesy 0050/0052/0070: >12 lokalizacji (cap), warianty wielkości liter/spacji,
    HU bez lokalizacji (wiersz „—" z ilością), zero qty."""
    b = _Builder()
    for i in range(14):
        b.hu("0050", f"B0-{i:02d}", items=((5 + i, None, ""),))
    b.hu("0052", "N1-01", items=((7, None, ""),))
    b.hu("0052", "n1-01 ", items=((3, None, ""),))          # ten sam klucz po normalizacji
    b.hu("0052", "", items=((4, None, ""),))                 # brak lokalizacji → „—"
    b.hu(" 0070 ", "A1", items=((0, None, ""),))             # typ ze spacjami, qty 0 → None
    return b.p


def sc_buckets():
    """Kubły: FEFO (wiele lotów, brak dat), statusy, objętości, blokady mastera, 9010 DLT/GR,
    wysyłka, nieznany typ, pusty typ (tylko w sumie), 0051 (wydawcze, nie proces)."""
    b = _Builder()
    b.hu("0010", "C1-01", items=((10, D(2027, 5, 1), "L1"), (5, D(2026, 12, 1), "L2")),
         status="B6", dims=(120, 80, 150))
    b.hu("0010", "C1-02", items=((20, None, "LX"),), status="Q4", dims=(120, 80, None))
    b.hu("0010", "C1-03", items=((2.5, D(2026, 1, 1), " L3 "),), status="c7", dims=(1, 1, 1))
    b.hu("0010", "", items=((1, D(2026, 1, 1), ""),), status="ZZ", code="")
    b.hu("0010", "c1-04", items=((3, None, ""),), status=" ")
    b.hu("0011", "C2-01", items=((8, None, ""),))
    b.hu("9010", "DLT-01", items=((30, None, ""),))
    b.hu("9010", "GR-02", items=((20, None, ""),))
    b.hu("9010", "SPLIT-3", items=((1, None, ""),))
    b.hu("92ex", "W1", items=((50, None, ""),), dims=(100, 100, 100))
    b.hu("BROK", "X1", items=((6, None, ""),))
    b.hu("", "Z1", items=((9, None, ""),))
    b.hu("0051", "P1", items=((4, None, ""),))
    _master(["C1-01"], pick=True)
    _master(["C1-02"], put=True)
    _master(["C1-03"], active=False, pick=True)           # nieaktywny batch → ignorowany
    _master(["C1-04"], pick=True)                         # HU ma „c1-04" → dopasowanie po normalizacji
    _master(["C2-01"])                                    # bez blokady
    return b.p


def sc_bucket_cap():
    """>60 palet w kubełku: rows przycięte do 60, suma objętości i count po wszystkich."""
    b = _Builder()
    for i in range(62):
        b.hu("0010", f"C{i:02d}", items=((1, D(2026, 1, 1) + dt.timedelta(days=62 - i), ""),),
             dims=(100, 100, 100))
    return b.p


def sc_fixes():
    """Fixy: poniżej min, równo min, min=0, brak max/uom/wh, case-insensitive lokalizacja i REF."""
    b = _Builder("REF-FX")
    b.hu("0050", "b0-15", items=((40, None, ""),))
    b.hu("0010", "C9-01", items=((11, None, ""),))
    b.fix("B0-15", min_qty=60, max_qty=480, wh="0050", uom="OP")
    b.fix("A0-01", min_qty=0, max_qty=0)
    b.fix("A0-02", min_qty=0.4, max_qty=10.6, ref="ref-fx")
    b.fix("C9-01", min_qty=11, wh="0010")
    b.fix("Z0-01", min_qty=5, ref="OTHER")                # inny REF → pominięty
    return b.p


def sc_fix_only():
    b = _Builder()
    b.fix("F1", min_qty=1)
    return b.p


def sc_matching():
    """Dopasowanie pozycji: po FK albo po ref_code (iexact); shipment nie-stockowy pominięty;
    wiele pozycji tej samej HU sumuje się, HU liczona raz."""
    b = _Builder("REF-M")
    b.hu("0010", "M1", items=((1, None, ""),), ref="ref-m", link_product=False)
    b.hu("0010", "M2", items=((2, None, ""), (3, None, "")))
    b.hu("0010", "M3", items=((100, None, ""),), is_stock=False)
    b.hu("0010", "M4", items=((100, None, ""),), ref="REF-OTHER", link_product=False)
    b.hu("0050", "B1", items=((0, None, ""),), status="B6")
    return b.p


def sc_zero_qty():
    b = _Builder()
    b.hu("0010", "C1", items=((0, None, ""),), status="B6")
    return b.p


SCENARIOS = {
    "empty": sc_empty,
    "processes": sc_processes,
    "buckets": sc_buckets,
    "bucket_cap": sc_bucket_cap,
    "fixes": sc_fixes,
    "fix_only": sc_fix_only,
    "matching": sc_matching,
    "zero_qty": sc_zero_qty,
}


def _run(builder):
    with transaction.atomic():
        out = json.loads(json.dumps(_storage_strategy(builder()), ensure_ascii=False, default=str))
        transaction.set_rollback(True)
    return out


class StorageStrategyCharacterizationTests(TestCase):
    maxDiff = None

    def test_snapshot(self):
        got = {name: _run(fn) for name, fn in SCENARIOS.items()}
        if os.environ.get("STORAGE_STRATEGY_SNAPSHOT_UPDATE"):
            SNAPSHOT.write_text(json.dumps(got, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                encoding="utf-8")
        want = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(sorted(got), sorted(want))
        for name in want:
            with self.subTest(scenario=name):
                self.assertEqual(got[name], want[name])

    def test_empty(self):
        self.assertEqual(_run(sc_empty), {
            "processes": [], "fixes": [], "stock_groups": [], "stock_categories": [],
            "stock_total": None, "other_types": [], "has_process": False})

    def test_has_process_from_fix_only(self):
        s = _run(sc_fix_only)
        self.assertTrue(s["has_process"])
        self.assertEqual(s["processes"], [])

    def test_process_location_cap_12(self):
        s = _run(sc_processes)
        fix = next(p for p in s["processes"] if p["code"] == "0050")
        self.assertEqual(len(fix["locations"]), 12)

    def test_process_row_without_location_has_qty(self):
        # Naprawione (było: qty None): HU bez lokalizacji → wiersz „—" z ilością tej palety
        # (spójny klucz: ilość zapisana i szukana pod _locnorm(lokalizacji) == "").
        s = _run(sc_processes)
        near = next(p for p in s["processes"] if p["code"] == "0052")
        self.assertIn({"code": "—", "qty": 4}, near["locations"])

    def test_process_case_variants_merged(self):
        # Naprawione (było: 2 wiersze, każdy z sumą 10 — podwójne liczenie): „N1-01" i
        # „n1-01 " to jedna lokalizacja po normalizacji (strip + upper) → jeden wiersz, suma 10.
        s = _run(sc_processes)
        near = next(p for p in s["processes"] if p["code"] == "0052")
        n1 = [r for r in near["locations"] if r["code"].upper() == "N1-01"]
        self.assertEqual(n1, [{"code": "N1-01", "qty": 10}])
        self.assertEqual(sum(r["qty"] or 0 for r in near["locations"]), 14)

    def test_blocked_flag_is_case_insensitive(self):
        # Naprawione (było: „c1-04" nie łapało blokady „C1-04"): ta sama normalizacja
        # lokalizacji po stronie HU i mastera.
        s = _run(sc_buckets)
        rows = next(g for g in s["stock_groups"] if g["code"] == "0010")["rows"]
        by_loc = {r["location"]: r["blocked"] for r in rows}
        self.assertTrue(by_loc["C1-01"])
        self.assertTrue(by_loc["C1-02"])
        self.assertFalse(by_loc["C1-03"])                 # nieaktywny batch
        self.assertTrue(by_loc["c1-04"])

    def test_blocked_flag_master_spelling_variant(self):
        # Odwrotny wariant: master zapisany małymi literami ze spacją, HU wielkimi.
        def sc():
            b = _Builder()
            b.hu("0010", "D1-01", items=((1, None, ""),))
            _master([" d1-01"], put=True)
            return b.p
        rows = _run(sc)["stock_groups"][0]["rows"]
        self.assertTrue(rows[0]["blocked"])

    def test_stock_groups_order_and_empty_type_only_in_total(self):
        s = _run(sc_buckets)
        codes = [g["code"] for g in s["stock_groups"]]
        self.assertEqual(codes[0], "0051")                # wydawcze na górze
        self.assertNotIn("", codes)
        self.assertEqual(s["stock_total"]["count"], 13)   # pusta HU typu "" liczona w sumie
        self.assertFalse(s["has_process"])

    def test_bucket_cap_60(self):
        g = _run(sc_bucket_cap)["stock_groups"][0]
        self.assertEqual((g["count"], len(g["rows"]), g["total_volume_m3"]), (62, 60, 62.0))

    def test_zero_qty_gives_none(self):
        s = _run(sc_zero_qty)
        self.assertEqual(s["stock_total"], {"count": 1, "base_qty": None, "blocked_qty": None})
        self.assertIsNone(s["stock_groups"][0]["base_qty"])
