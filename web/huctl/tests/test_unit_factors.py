"""B2 — wspólne przeliczniki AJM (hierarchy.unit_factors): ten sam indeks musi dawać
IDENTYCZNE wartości we wszystkich widokach (MATINFO, skaner Kontroli HU, karta).

Zdiagnozowany rozjazd: skaner liczył lokalnie (bez fallbacku inner_pack.units_per_pack,
z własnym estymatorem PAL), MATINFO przez build_hierarchy (bez estymatora) — indeks bez
layoutu: skaner pokazywał PAL, MATINFO pusto; indeks z OPZ tylko na InnerPack: skaner
szarzył kafel OPZ, MATINFO liczyło. Te testy pinują wspólne źródło."""
from django.test import TestCase

from ui.hierarchy import build_hierarchy, cartons_per_pallet_estimate, unit_factors
from ui.models import InnerPack, PalletizationInstruction, Product


def _instr(code, *, layouts=None, pcs_per_carton=24, pcs_per_inner_pack=0,
           inner_pack=None, carton=None):
    p = Product.objects.create(code=code, name=f"Materiał {code}")
    return p, PalletizationInstruction.objects.create(
        product=p, version=1, is_active=True, unit_weight=0.5,
        pcs_per_carton=pcs_per_carton, pcs_per_inner_pack=pcs_per_inner_pack,
        inner_pack=inner_pack, carton=carton,
        carton_l=40, carton_w=30, carton_h=25,
        pallet_code="EU", pallet_length_cm=120, pallet_width_cm=80,
        pallet_base_height_cm=15, max_height_total_cm=215,
        layouts=layouts or [], selected_layout="L1" if layouts else "")


class UnitFactorsTests(TestCase):
    def test_full_hierarchy_with_layout(self):
        """Pełna kaskada szt→OPZ→KAR→PAL z zapisanym layoutem."""
        _, instr = _instr("UF-1", pcs_per_inner_pack=6,
                          layouts=[{"name": "L1", "cartons_per_pallet": 32}])
        f = unit_factors(instr)
        self.assertEqual(f["opz"], 6.0)
        self.assertEqual(f["kar"], 24.0)
        self.assertEqual(f["pal"], 32 * 24.0)
        self.assertFalse(f["estimated"])

    def test_opz_fallback_from_inner_pack(self):
        """OPZ z inner_pack.units_per_pack, gdy instrukcja nie ma pcs_per_inner_pack —
        fallback, którego skanerowi wcześniej brakowało (rozjazd vs MATINFO)."""
        ip = InnerPack.objects.create(name="OPZ 12", length_cm=20, width_cm=15,
                                      height_cm=10, units_per_pack=12)
        _, instr = _instr("UF-2", inner_pack=ip,
                          layouts=[{"name": "L1", "cartons_per_pallet": 10}])
        self.assertEqual(unit_factors(instr)["opz"], 12.0)

    def test_no_layout_uses_geometric_estimate_flagged(self):
        """Bez layoutu: PAL z geometrycznego estymatora + flaga estimated.
        Karton 40×30×25 na EU 120×80, ładunek 200 cm → 8/warstwę × 8 warstw = 64."""
        _, instr = _instr("UF-3")
        f = unit_factors(instr)
        self.assertEqual(cartons_per_pallet_estimate(instr), 64)
        self.assertEqual(f["cpp"], 64)
        self.assertEqual(f["pal"], 64 * 24.0)
        self.assertTrue(f["estimated"])

    def test_matinfo_summary_matches_unit_factors(self):
        """KLUCZOWE: build_hierarchy.summary i unit_factors dają te same wartości —
        także bez layoutu (wcześniej MATINFO pokazywało pusto, skaner szacunek)."""
        for code, layouts in (("UF-4", [{"name": "L1", "cartons_per_pallet": 32,
                                         "layers_used": 4}]),
                              ("UF-5", None)):
            p, instr = _instr(code, layouts=layouts)
            f = unit_factors(instr)
            h = build_hierarchy(p, instr, with_artwork=False)
            self.assertEqual(h["summary"]["cartons_per_pallet"], f["cpp"], code)
            self.assertEqual(h["summary"]["pcs_per_pallet"], f["pal"], code)
            self.assertEqual(h["summary"]["cpp_estimated"], f["estimated"], code)

    def test_missing_dims_no_pal(self):
        _, instr = _instr("UF-6")
        PalletizationInstruction.objects.filter(pk=instr.pk).update(carton_h=0)
        instr.refresh_from_db()
        f = unit_factors(instr)
        self.assertIsNone(f["pal"])
        self.assertEqual(f["kar"], 24.0)

    def test_none_instruction(self):
        f = unit_factors(None)
        self.assertEqual(f, {"opz": None, "kar": None, "pal": None,
                             "cpp": None, "estimated": False})

    def test_scanner_tiles_use_shared_source(self):
        """Kafle skanera (hu_control._unit_factors) = wynik wspólnego serwisu."""
        from unittest.mock import MagicMock
        from huctl.views.hu_control import _unit_factors as scanner_factors
        _, instr = _instr("UF-7", pcs_per_inner_pack=6,
                          layouts=[{"name": "L1", "cartons_per_pallet": 32}])
        item = MagicMock(base_qty=0, alt_qty=0, base_unit="OP", alt_unit="KAR")
        item.hu.shipment = None
        from unittest.mock import patch
        with patch("huctl.views.hu_count._instr_for", return_value=instr):
            t = scanner_factors(item)
        f = unit_factors(instr)
        self.assertEqual((t["opz"], t["kar"], t["pal"]), (f["opz"], f["kar"], f["pal"]))
