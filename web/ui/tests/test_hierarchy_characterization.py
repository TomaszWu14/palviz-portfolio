"""Testy charakteryzacyjne `build_hierarchy` (CODE-001) — przypinają DOKŁADNY wynik
obecnej implementacji (poziomy, three_data jako string JSON, summary, alerty, logi)
dla zestawu wejść pokrywającego każdą gałąź. Refaktor ma zostawić je zielone bez
zmiany oczekiwań. Snapshoty wygenerowane z kodu sprzed refaktoru.

Jedyna normalizacja: `"id": <pk grafiki>` → `"id": 0` (sekwencje PK nie są resetowane
między testami). Pliki grafik/modeli ustawiane nazwą (bez zapisu na dysk)."""
import json
import re
from pathlib import Path

from django.test import TestCase

from testkit.factories import (CartonFactory, InnerPackFactory, InstructionFactory,
                               ProductCategoryFactory, ProductFactory)
from ui.hierarchy import build_hierarchy
from ui.models import CartonArtwork, InnerPackArtwork, ProductArtwork

LAYOUT = [{"name": "L", "layers_used": 6, "cartons_per_pallet": 48,
           "placements": [{"x": 0, "y": 0, "dx": 40, "dy": 30}]}]


def _norm(res):
    """Wynik → porównywalna struktura (three_data zostaje stringiem — kolejność kluczy też)."""
    levels = [{**l, "three_data": re.sub(r'"id": \d+', '"id": 0', l["three_data"])}
              for l in res["levels"]]
    return {"levels": levels, "summary": res["summary"], "alerts": res["alerts"]}


def _art(model, **fk):
    return model.objects.create(face="front", kind="print", x_pct=1, y_pct=2, w_pct=50,
                                h_pct=40, rotation_deg=0, z=0, image="a/art.png", **fk)


# ── Scenariusze (każdy → (product, kwargs build_hierarchy)) ─────────────────────

def s_full():
    """Layout + karton (glb+grafika) + OPZ z instrukcji (grafika) + sztuka (glb+grafika),
    przeliczniki spójne, bez kategorii, upp=1."""
    ip = InnerPackFactory(name="OPZ", length_cm=20, width_cm=15, height_cm=12,
                          units_per_pack=6, ean="5900000000011")
    c = CartonFactory(name="KAR-A", length_cm=40, width_cm=30, height_cm=25, ean="5900000000028",
                      glb_model="carton_models/k.glb", inner_pack=ip, packs_per_carton=2)
    p = ProductFactory(code="CH-FULL", ean="5900000000035", unit_length_cm=10,
                       unit_width_cm=8, unit_height_cm=5, glb_model="product_models/p.glb")
    InstructionFactory(product=p, carton=c, inner_pack=ip, packs_per_carton=2,
                       pcs_per_inner_pack=6, pcs_per_carton=12, unit_weight=0.5,
                       carton_tare=0.3, layouts=LAYOUT)
    _art(CartonArtwork, carton=c)
    _art(InnerPackArtwork, inner_pack=ip)
    _art(ProductArtwork, product=p)
    return p, {}


def s_full_no_artwork():
    """Jak s_full, ale with_artwork=False — paleta też bez grafik kartonu (fix/hierarchy;
    wcześniej paleta dociągała je zawsze). glb zostaje (bez zapytania)."""
    p, _ = s_full()
    return p, {"with_artwork": False}


def s_split():
    """upp=3 → poziom JU (ju_image + ju_glb); instrukcja bez kartonu z bazy (carton=None),
    podstawa palety 0 (fallback meta→0)."""
    p = ProductFactory(code="CH-SPLIT", ean="5900000000042", unit_length_cm=12,
                       unit_width_cm=6, unit_height_cm=3, ju_image="product_art/ju.png",
                       ju_glb_model="product_models/ju.glb")
    InstructionFactory(product=p, carton=None, carton_l=40, carton_w=30, carton_h=25,
                       pcs_per_carton=10, unit_weight=0.2, units_per_piece=3,
                       pallet_base_height_cm=0, layouts=LAYOUT)
    return p, {}


def s_split_no_artwork():
    p, _ = s_split()
    return p, {"with_artwork": False}


def s_estimate_fallbacks():
    """Bez layoutu → estymacja geometryczna kartonów/paletę (cpp_estimated). OPZ i
    OPZ/karton z KARTONU (instrukcja pusta), szt/OPZ z InnerPack.units_per_pack,
    niespójność 3×5≠12 (alert + log), opak. handlowe (sales_unit_l_cm) → sales/paleta."""
    ip = InnerPackFactory(name="OPZ-B", length_cm=18, width_cm=14, height_cm=10,
                          units_per_pack=5, sales_unit_l_cm=9, sales_units_per_pack=2)
    c = CartonFactory(name="", length_cm=40, width_cm=30, height_cm=25,
                      inner_pack=ip, packs_per_carton=3)
    p = ProductFactory(code="CH-EST", ean="", unit_length_cm=10, unit_width_cm=8,
                       unit_height_cm=5)
    InstructionFactory(product=p, carton=c, pcs_per_carton=12, unit_weight=0.25)
    return p, {}


def s_no_pallet_possible():
    """Bez layoutu i estymacja = 0 (wysokość max ≤ podstawa) → brak palety w summary;
    OPZ bez szt/OPZ → qty z wymiarów, bez unit_data; produkt bez wymiarów sztuki."""
    ip = InnerPackFactory(name="OPZ-C", length_cm=18, width_cm=14, height_cm=10,
                          units_per_pack=None)
    c = CartonFactory(name="KAR-C", length_cm=40, width_cm=30, height_cm=25)
    p = ProductFactory(code="CH-NOPAL", ean="", unit_length_cm=None, unit_width_cm=None,
                       unit_height_cm=None)
    InstructionFactory(product=p, carton=c, inner_pack=ip, packs_per_carton=4,
                       pcs_per_carton=8, unit_weight=1.0, max_height_total_cm=10)
    return p, {}


def s_no_instruction():
    """Brak instrukcji → tylko poziom sztuki + alert o braku instrukcji."""
    return ProductFactory(code="CH-NOINS", ean="5900000000059", unit_length_cm=10,
                          unit_width_cm=10, unit_height_cm=10), {}


def s_nothing():
    """Brak instrukcji i wymiarów → pusto."""
    return ProductFactory(code="CH-EMPTY", ean="", unit_length_cm=None, unit_width_cm=None,
                          unit_height_cm=None), {}


def s_category_missing():
    """Kategoria wymaga pallet,carton,ju — brak layoutu i upp=1 → alerty braków + filtr."""
    cat = ProductCategoryFactory(code="CH1", hierarchy_levels="pallet, carton,ju,bogus")
    c = CartonFactory(name="KAR-D", length_cm=40, width_cm=30, height_cm=25)
    p = ProductFactory(code="CH-CAT", ean="", category=cat, unit_length_cm=10, unit_width_cm=8,
                       unit_height_cm=5)
    InstructionFactory(product=p, carton=c, pcs_per_carton=10, unit_weight=0.1,
                       max_height_total_cm=10)
    return p, {}


def s_category_subset():
    """Kategoria carton,unit — poziom palety wycięty filtrem (bez alertu)."""
    cat = ProductCategoryFactory(code="CH2", hierarchy_levels="carton,unit")
    c = CartonFactory(name="KAR-E", length_cm=40, width_cm=30, height_cm=25)
    p = ProductFactory(code="CH-SUB", ean="5900000000066", category=cat, unit_length_cm=10, unit_width_cm=8,
                       unit_height_cm=5)
    InstructionFactory(product=p, carton=c, pcs_per_carton=10, unit_weight=0.1,
                       layouts=LAYOUT)
    return p, {}


def s_category_invalid_only():
    """Kategoria z samymi nieznanymi kluczami = bez ograniczeń (jak puste)."""
    cat = ProductCategoryFactory(code="CH3", hierarchy_levels="layer,foo")
    p = ProductFactory(code="CH-INV", ean="", category=cat, unit_length_cm=None,
                       unit_width_cm=None, unit_height_cm=None)
    InstructionFactory(product=p, carton=None, carton_l=40, carton_w=30, carton_h=25,
                       pcs_per_carton=10, unit_weight=0.1, max_height_total_cm=10)
    return p, {}


SCENARIOS = {
    "full": s_full, "full_no_artwork": s_full_no_artwork,
    "split": s_split, "split_no_artwork": s_split_no_artwork,
    "estimate_fallbacks": s_estimate_fallbacks, "no_pallet_possible": s_no_pallet_possible,
    "no_instruction": s_no_instruction, "nothing": s_nothing,
    "category_missing": s_category_missing, "category_subset": s_category_subset,
    "category_invalid_only": s_category_invalid_only,
}

# Snapshot wyniku sprzed refaktoru (JSON obok — limit 500 linii na plik .py).
EXPECTED = json.loads((Path(__file__).with_name("hierarchy_snapshot.json"))
                      .read_text(encoding="utf-8"))


def run_scenario(name):
    """Scenariusz → znormalizowany wynik. Produkt przeładowany z bazy (typy jak w realu:
    FloatField → float), inaczej wynik zależałby od tego, czym karmiono fabrykę."""
    product, kwargs = SCENARIOS[name]()
    product = type(product).objects.get(pk=product.pk)
    res = build_hierarchy(product, **kwargs)
    assert set(res) == {"levels", "summary", "alerts", "instr"}
    assert res["instr"] == product.latest_instruction()
    return _norm(res)


class BuildHierarchyCharacterization(TestCase):
    maxDiff = None

    def _check(self, name):
        self.assertEqual(run_scenario(name), EXPECTED[name])

    def test_full(self): self._check("full")
    def test_full_no_artwork(self): self._check("full_no_artwork")
    def test_split(self): self._check("split")
    def test_split_no_artwork(self): self._check("split_no_artwork")
    def test_estimate_fallbacks(self): self._check("estimate_fallbacks")
    def test_no_pallet_possible(self): self._check("no_pallet_possible")
    def test_no_instruction(self): self._check("no_instruction")
    def test_nothing(self): self._check("nothing")
    def test_category_missing(self): self._check("category_missing")
    def test_category_subset(self): self._check("category_subset")
    def test_category_invalid_only(self): self._check("category_invalid_only")

    def test_all_scenarios_pinned(self):
        self.assertEqual(set(SCENARIOS), set(EXPECTED))

    def test_pallet_has_media_from_carton_media(self):
        # Naprawione (fix/hierarchy): paleta niesie "carton_artwork"/"carton_glb_url",
        # has_media liczone z kluczy JSON-a → True (wcześniej zawsze False).
        pallet = EXPECTED["full"]["levels"][0]
        self.assertEqual(pallet["key"], "pallet")
        self.assertIn("carton_glb_url", pallet["three_data"])
        self.assertTrue(pallet["has_media"])

    def test_explicit_instr_wins_over_latest(self):
        p, _ = s_split()
        other = InstructionFactory(product=p, version=2, is_active=False, carton=None,
                                   carton_l=50, carton_w=40, carton_h=30, pcs_per_carton=7,
                                   unit_weight=1.0, units_per_piece=1)
        res = build_hierarchy(p, instr=other)
        self.assertIs(res["instr"], other)
        carton = next(l for l in res["levels"] if l["key"] == "carton")
        self.assertEqual(carton["dims"], "50×40×30 cm")

    def test_inconsistency_is_logged(self):
        p, _ = s_estimate_fallbacks()
        with self.assertLogs("ui.hierarchy", level="WARNING") as cm:
            build_hierarchy(p)
        self.assertEqual(cm.output, [
            "WARNING:ui.hierarchy:hierarchy inconsistency CH-EST: Niespójność master daty: "
            "3 OPZ/karton × 5 szt/OPZ = 15 ≠ 12 szt/karton"])
