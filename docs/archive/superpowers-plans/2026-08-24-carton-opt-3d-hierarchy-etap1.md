# Optymalizacja opakowań 3D — Etap 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ekran „Warianty kartonów" pokazuje pełną hierarchię 4 poziomów (sztuka→OPZ→karton→paleta) z geometryczną kaskadą przeliczeń dla wariantów B/C, z powiększonym renderem palety.

**Architecture:** Nowy bezwymiarowy helper `pack_into()` reużywa `PalletCalculator` (kontener traktowany jak „paleta" o footprincie L×W i max wysokości H). Widok `carton_opt_variants` buduje dla A/B/C po 4 poziomy `{dims, three_data, spec}` renderowane przez istniejący `palviz-three.js`. `CartonAlternative` dostaje pola wymiarów sztuki/OPZ i slot B/C (migracja). Zero zapisu do master-danych (Etap 2).

**Tech Stack:** Django 5, `palletizer` (framework-free), three.js (vendored, `renderPalVizLevel`), simple_history.

## Global Constraints

- Polish-first UI (etykiety, komunikaty).
- Wariant A funkcjonalnie nietykalny — tylko wzrost renderu (720px, width:100%).
- `palletizer/` bez importów Django.
- Nowa migracja (nie edytować istniejących); po merge z main uruchomić palviz-migrate-guard w razie konfliktu leafów.
- Testy: `palletizer/tests` + `ui.tests` (env: `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=..`).
- Branch: `claude/carton-opt-3d-hierarchy`; commit po każdym tasku.

---

### Task 1: `pack_into()` — bezwymiarowy packer poziomu

**Files:**
- Modify: `web/ui/views/carton_opt.py` (obok `_variant_fill`, ~linia 202)
- Test: `web/ui/tests/test_carton_opt_hierarchy.py` (nowy)

**Interfaces:**
- Consumes: `PalletCalculator`, `CartonVariant`, `Dimensions`, `PalletType` (już importowane w module przez `from .core import *` / lokalne importy jak w `_variant_fill`).
- Produces: `pack_into(container_lwh: tuple[int,int,int], unit_lwh: tuple[int,int,int], unit_weight_kg: float = 0.001) -> dict` → `{"count": int|None, "per_layer": int|None, "layers": int|None, "fill": int|None, "placements": list, "error": str}`. `count=None` + `error` gdy nie mieści się / zły wymiar.

- [ ] **Step 1: Failing test**

```python
# web/ui/tests/test_carton_opt_hierarchy.py
"""Kaskada hierarchii opakowań: pack_into (sztuka→OPZ→karton→paleta) i widok wariantów."""
from django.test import TestCase


class PackIntoTests(TestCase):
    def test_simple_grid(self):
        from ui.views.carton_opt import pack_into
        r = pack_into((40, 30, 20), (10, 10, 10))     # 4×3 na warstwę × 2 warstwy
        self.assertEqual(r["count"], 24)
        self.assertEqual(r["layers"], 2)
        self.assertFalse(r["error"])

    def test_rotation_helps(self):
        from ui.views.carton_opt import pack_into
        # 20×10 unit w 30×20 kontenerze: bez rotacji 1/warstwę, z rotacją 3.
        r = pack_into((30, 20, 10), (20, 10, 10))
        self.assertGreaterEqual(r["count"], 3)

    def test_smaller_unit_never_fewer(self):
        from ui.views.carton_opt import pack_into
        big = pack_into((60, 40, 30), (20, 20, 15))["count"]
        small = pack_into((60, 40, 30), (10, 10, 15))["count"]
        self.assertGreaterEqual(small, big)

    def test_zero_dim_is_error_not_crash(self):
        from ui.views.carton_opt import pack_into
        r = pack_into((40, 30, 20), (0, 10, 10))
        self.assertIsNone(r["count"])
        self.assertTrue(r["error"])

    def test_unit_bigger_than_container(self):
        from ui.views.carton_opt import pack_into
        r = pack_into((10, 10, 10), (20, 20, 20))
        self.assertIsNone(r["count"])
        self.assertTrue(r["error"])
```

- [ ] **Step 2: Run — verify FAIL**

Run (z `web/`): `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests.test_carton_opt_hierarchy -v 1`
Expected: `ImportError: cannot import name 'pack_into'`.

- [ ] **Step 3: Implementacja**

```python
# web/ui/views/carton_opt.py — pod _variant_fill
def pack_into(container_lwh, unit_lwh, unit_weight_kg=0.001):
    """Bezwymiarowy packer poziomu hierarchii: ile jednostek unit_lwh mieści się w
    kontenerze container_lwh. Reużywa PalletCalculator (kontener = 'paleta' o footprincie
    L×W i max wysokości H, waga praktycznie nieograniczona) — zero nowej geometrii.
    Zwraca {count, per_layer, layers, fill, placements, error}."""
    cl, cw, ch = container_lwh
    ul, uw, uh = unit_lwh
    if min(cl, cw, ch, ul, uw, uh) <= 0:
        return {"count": None, "per_layer": None, "layers": None, "fill": None,
                "placements": [], "error": "Zerowy wymiar"}
    try:
        pallet = PalletType(
            name="LEVEL", dims=Dimensions(l_cm=int(cl), w_cm=int(cw), h_cm=int(ch)),
            max_stack_height_cm=int(ch), max_weight_kg=100000, base_height_cm=0,
        )
        unit = CartonVariant(
            sku="UNIT", variant="LVL",
            dims=Dimensions(l_cm=int(ul), w_cm=int(uw), h_cm=int(uh)),
            unit_weight_kg=max(0.001, float(unit_weight_kg)),
            pieces_per_carton=1, demand_pieces=100000, allow_rotation=True,
        ).validate()
        res = PalletCalculator.calculate(unit, pallet)
        best = max(res.layouts, key=lambda L: L.cartons_per_pallet)
        count = best.cartons_per_pallet
        if not count:
            return {"count": None, "per_layer": None, "layers": None, "fill": None,
                    "placements": [], "error": "Nie mieści się"}
        fill = min(100, round(100 * count * ul * uw * uh / (cl * cw * ch)))
        return {"count": count, "per_layer": best.cartons_per_layer,
                "layers": best.layers_used, "fill": fill,
                "placements": [p.__dict__ if hasattr(p, "__dict__") else p
                               for p in getattr(best, "placements", [])],
                "error": ""}
    except Exception as exc:
        return {"count": None, "per_layer": None, "layers": None, "fill": None,
                "placements": [], "error": f"Błąd pakowania: {exc}"[:120]}
```

Uwaga wykonawcza: dopasuj konstruktory `PalletType`/atrybuty layoutu do realnych
definicji w `palletizer/domain` i `pallet_calculator` (spójrz jak robi to `_build_pallet`
i `_eval_layouts` w tym samym pliku — użyj tych samych ścieżek dostępu; jeśli layouty są
dict-ami, czytaj `layout["cartons_per_pallet"]` itd.). Test z Step 1 jest wyrocznią.

- [ ] **Step 4: Run — verify PASS** (ta sama komenda co Step 2)

- [ ] **Step 5: Commit**

```bash
git add web/ui/views/carton_opt.py web/ui/tests/test_carton_opt_hierarchy.py
git commit -m "feat(carton-opt): pack_into — bezwymiarowy packer poziomu hierarchii"
```

---

### Task 2: Model — `CartonAlternative` z wymiarami sztuki/OPZ i slotem B/C

**Files:**
- Modify: `web/ui/models.py:1889-1911` (klasa `CartonAlternative`)
- Create: migracja (`makemigrations`)
- Test: `web/ui/tests/test_carton_opt_hierarchy.py` (dopisz klasę)

**Interfaces:**
- Produces: pola `unit_l_cm/unit_w_cm/unit_h_cm` (IntegerField, null=True, blank=True — sztuka), `pack_l_cm/pack_w_cm/pack_h_cm` (j.w. — OPZ), `slot` (CharField choices `[("", "—"), ("B", "B"), ("C", "C")]`, default `""`, blank=True). Null = „poziom bez zmian, weź z hierarchii bazowej".

- [ ] **Step 1: Failing test**

```python
class AlternativeSlotTests(TestCase):
    def test_slot_and_level_dims_fields(self):
        from ui.models import Product, CartonAlternative
        p = Product.objects.create(code="H-1", name="Hier")
        alt = CartonAlternative.objects.create(
            product=p, label="Wariant B", slot="B",
            length_cm=40, width_cm=30, height_cm=20,
            pack_l_cm=20, pack_w_cm=15, pack_h_cm=10,
            unit_l_cm=5, unit_w_cm=5, unit_h_cm=10)
        alt.refresh_from_db()
        self.assertEqual(alt.slot, "B")
        self.assertEqual(alt.pack_l_cm, 20)
        self.assertEqual(alt.unit_h_cm, 10)
```

- [ ] **Step 2: Run — verify FAIL** (`TypeError: unexpected keyword arguments`)

- [ ] **Step 3: Dodaj pola do modelu**

```python
    # Wymiary niższych poziomów hierarchii dla kaskady B/C (null = bez zmian, z bazy).
    unit_l_cm = models.IntegerField(null=True, blank=True, verbose_name="L sztuki [cm]")
    unit_w_cm = models.IntegerField(null=True, blank=True, verbose_name="W sztuki [cm]")
    unit_h_cm = models.IntegerField(null=True, blank=True, verbose_name="H sztuki [cm]")
    pack_l_cm = models.IntegerField(null=True, blank=True, verbose_name="L OPZ [cm]")
    pack_w_cm = models.IntegerField(null=True, blank=True, verbose_name="W OPZ [cm]")
    pack_h_cm = models.IntegerField(null=True, blank=True, verbose_name="H OPZ [cm]")
    slot = models.CharField(max_length=1, blank=True, default="",
                            choices=[("", "—"), ("B", "B"), ("C", "C")],
                            verbose_name="Slot porównania")
```

Potem: `python manage.py makemigrations ui` (simple_history dołoży pola do Historical
automatycznie).

- [ ] **Step 4: Run — verify PASS**

- [ ] **Step 5: Commit** (`git add web/ui/models.py web/ui/migrations/00XX_*.py web/ui/tests/test_carton_opt_hierarchy.py`, msg: `feat(carton-opt): CartonAlternative — wymiary sztuki/OPZ + slot B/C`)

---

### Task 3: Kaskada — `_hierarchy_levels()` w widoku

**Files:**
- Modify: `web/ui/views/carton_opt.py` (nowy helper + rozbudowa `carton_opt_variants`, linia ~260)
- Test: `web/ui/tests/test_carton_opt_hierarchy.py` (dopisz)

**Interfaces:**
- Consumes: `pack_into` (Task 1), `build_hierarchy` (`ui.hierarchy`), `_variant_fill`.
- Produces: `_hierarchy_levels(instr, product, unit=None, pack=None, carton=None) -> list[dict]` — 4 poziomy w kolejności **sztuka, OPZ, karton, paleta**; każdy `{key, title, dims: (l,w,h)|None, three_data: str(JSON), spec: dict}`. `spec` zawiera `{dims_str, contains, fill, error}` (`contains` = „ile mieści jednostek niższego poziomu"). Argumenty `unit/pack/carton` to krotki `(l,w,h)` nadpisujące bazę z hierarchii (None = baza). Poziom paleta liczony przez `_variant_fill` (spójność z resztą modułu); three_data palety typu `"pallet"` z `placements` z layoutu.

- [ ] **Step 1: Failing test**

```python
class HierarchyCascadeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from ui.models import Product, PalletizationInstruction
        cls.p = Product.objects.create(
            code="CAS-1", name="Cascade",
            unit_length_cm=5, unit_width_cm=5, unit_height_cm=10)
        cls.instr = PalletizationInstruction.objects.create(
            product=cls.p, version=1, is_active=True,
            carton_l=40, carton_w=30, carton_h=20,
            unit_weight=0.2, pcs_per_carton=48, demand_pcs=1000,
            pallet_length_cm=120, pallet_width_cm=80,
            pallet_base_height_cm=15, max_height_total_cm=180)

    def test_four_levels_in_order(self):
        from ui.views.carton_opt import _hierarchy_levels
        lv = _hierarchy_levels(self.instr, self.p)
        self.assertEqual([l["key"] for l in lv],
                         ["unit", "inner_pack", "carton", "pallet"])

    def test_pack_override_cascades_up(self):
        from ui.views.carton_opt import _hierarchy_levels
        base = _hierarchy_levels(self.instr, self.p, pack=(20, 15, 10))
        small = _hierarchy_levels(self.instr, self.p, pack=(10, 15, 10))
        # Mniejsze OPZ → w kartonie mieści się ich nie mniej.
        c_base = base[2]["spec"]["contains"]
        c_small = small[2]["spec"]["contains"]
        self.assertGreaterEqual(c_small, c_base)

    def test_carton_override_changes_pallet(self):
        from ui.views.carton_opt import _hierarchy_levels
        lv = _hierarchy_levels(self.instr, self.p, carton=(60, 40, 20))
        self.assertEqual(lv[3]["key"], "pallet")
        self.assertIsNotNone(lv[3]["spec"]["contains"])   # kartonów/paletę policzony
```

- [ ] **Step 2: Run — verify FAIL** (brak `_hierarchy_levels`)

- [ ] **Step 3: Implementacja**

```python
def _hierarchy_levels(instr, product, unit=None, pack=None, carton=None):
    """4 poziomy hierarchii (sztuka→OPZ→karton→paleta) z geometryczną kaskadą.
    unit/pack/carton = (l,w,h) nadpisujące bazę; None = wymiary z hierarchii/instrukcji.
    Każdy poziom: {key,title,dims,three_data,spec}; spec={dims_str,contains,fill,error}."""
    import json as _json
    # Baza: sztuka z Product.unit_*, OPZ z instrukcji/inner_pack, karton z instrukcji.
    ip = instr.inner_pack or (instr.carton.inner_pack if instr.carton else None)
    base_unit = (product.unit_length_cm, product.unit_width_cm, product.unit_height_cm)
    base_pack = (ip.length_cm, ip.width_cm, ip.height_cm) if ip else (None,) * 3
    u = unit or (base_unit if all(base_unit) else None)
    p_ = pack or (base_pack if all(base_pack) else None)
    c = carton or (instr.carton_l, instr.carton_w, instr.carton_h)

    def _box(key, title, dims, inner=None, inner_n=None, color="#DCC4A0"):
        td = {"type": "box", "l": dims[0], "w": dims[1], "h": dims[2],
              "label": product.code, "color": color}
        if inner and inner_n:
            td.update({"type": "box_with_units", "unit_l": inner[0],
                       "unit_w": inner[1], "unit_h": inner[2], "units": inner_n})
        return {"key": key, "title": title, "dims": dims,
                "three_data": _json.dumps(td)}

    levels = []
    # 1) sztuka
    if u:
        lvl = _box("unit", "Sztuka / opakowanie", u, color="#C9B896")
        lvl["spec"] = {"dims_str": "×".join(map(str, u)), "contains": None,
                       "fill": None, "error": ""}
        levels.append(lvl)
    else:
        levels.append({"key": "unit", "title": "Sztuka / opakowanie", "dims": None,
                       "three_data": "", "spec": {"dims_str": "—", "contains": None,
                       "fill": None, "error": "Brak wymiarów sztuki w master data"}})
    # 2) OPZ — mieści sztuki (pack_into gdy oba znane)
    if p_:
        r = pack_into(p_, u) if u else {"count": None, "fill": None, "error": ""}
        lvl = _box("inner_pack", "Opakowanie zbiorcze (OPZ)", p_,
                   inner=u if (u and r["count"]) else None, inner_n=r["count"],
                   color="#D8C7A0")
        lvl["spec"] = {"dims_str": "×".join(map(str, p_)), "contains": r["count"],
                       "fill": r["fill"], "error": r["error"]}
        levels.append(lvl)
    else:
        levels.append({"key": "inner_pack", "title": "Opakowanie zbiorcze (OPZ)",
                       "dims": None, "three_data": "", "spec": {"dims_str": "—",
                       "contains": None, "fill": None,
                       "error": "Brak OPZ w hierarchii materiału"}})
    # 3) karton — mieści OPZ (albo sztuki, gdy brak OPZ)
    inner_dims = p_ or u
    r = pack_into(c, inner_dims) if inner_dims else {"count": None, "fill": None, "error": ""}
    lvl = _box("carton", "Karton", c,
               inner=inner_dims if (inner_dims and r["count"]) else None,
               inner_n=r["count"], color="#A07840")
    lvl["spec"] = {"dims_str": "×".join(map(str, c)), "contains": r["count"],
                   "fill": r["fill"], "error": r["error"]}
    levels.append(lvl)
    # 4) paleta — istniejąca ścieżka (spójna z tabelą wariantów)
    vf = _variant_fill(c[0], c[1], c[2], instr)
    meta_l = instr.pallet_length_cm or 120
    meta_w = instr.pallet_width_cm or 80
    base_h = instr.pallet_base_height_cm or 15
    pallet_td = ""
    if vf["fits"]:
        pr = pack_into((meta_l, meta_w,
                        (instr.max_height_total_cm or 180) - base_h), c)
        pallet_td = _json.dumps({
            "type": "pallet",
            "pallet": {"l": meta_l, "w": meta_w, "base_h": base_h},
            "carton": {"l": c[0], "w": c[1], "h": c[2]},
            "placements": pr["placements"], "layers": pr["layers"] or 1,
            "label": product.code, "color": "#DCC4A0"})
    levels.append({"key": "pallet", "title": "Paleta", "dims": (meta_l, meta_w, None),
                   "three_data": pallet_td,
                   "spec": {"dims_str": f"{meta_l}×{meta_w}",
                            "contains": vf["cartons_per_pallet"],
                            "fill": vf["fill"], "error": vf["error"]}})
    return levels
```

Uwaga wykonawcza: format `placements` musi zgadzać się z tym, co `renderPalVizLevel`
czyta dla typu `"pallet"` (wzorzec: `hierarchy.py:135-146` — placements z
`layout["placements"]`). Jeśli `pack_into` nie odda zgodnych placements, zbuduj je z
`per_layer`/`layers` w tym samym kształcie co layouty silnika (sprawdź strukturę w
`instr.get_selected_layout()["placements"]` i odwzoruj klucze).

- [ ] **Step 4: Run — verify PASS**

- [ ] **Step 5: Commit** (`feat(carton-opt): _hierarchy_levels — geometryczna kaskada 4 poziomów`)

---

### Task 4: Widok + zapis wariantu ze slotem i wymiarami poziomów

**Files:**
- Modify: `web/ui/views/carton_opt.py` — `carton_opt_variants` (kontekst) i `carton_opt_variant_save` (nowe pola)
- Test: `web/ui/tests/test_carton_opt_hierarchy.py` (dopisz)

**Interfaces:**
- Consumes: `_hierarchy_levels` (Task 3), pola modelu (Task 2).
- Produces: kontekst szablonu dostaje `hier_a` (poziomy dla A/bazy), `variant_b`, `variant_c` (obiekt `CartonAlternative` slotu lub None), `hier_b`, `hier_c` (poziomy lub None). `carton_opt_variant_save` przyjmuje dodatkowo `slot`, `unit_l/unit_w/unit_h`, `pack_l/pack_w/pack_h` (opcjonalne; puste → None).

- [ ] **Step 1: Failing test**

```python
class VariantsViewHierarchyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # user z rolami jak w test_carton_opt.py (skopiuj helper _user stamtąd)
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        from ui.roles import ALL_GROUPS
        cls.user = get_user_model().objects.create_user("opt", password="x")
        for g in ALL_GROUPS:
            cls.user.groups.add(Group.objects.get_or_create(name=g)[0])
        from ui.models import Product, PalletizationInstruction
        cls.p = Product.objects.create(code="VH-1", name="VH",
            unit_length_cm=5, unit_width_cm=5, unit_height_cm=10)
        PalletizationInstruction.objects.create(
            product=cls.p, version=1, is_active=True,
            carton_l=40, carton_w=30, carton_h=20, unit_weight=0.2,
            pcs_per_carton=48, demand_pcs=1000, pallet_length_cm=120,
            pallet_width_cm=80, pallet_base_height_cm=15, max_height_total_cm=180)

    def setUp(self):
        self.client.force_login(self.user)

    def test_context_has_hierarchy_a(self):
        from django.urls import reverse
        resp = self.client.get(reverse("ui:carton_opt_variants"),
                               {"product": "VH-1"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["hier_a"]), 4)
        self.assertIsNone(resp.context["variant_b"])

    def test_save_slot_b_with_level_dims_and_cascade(self):
        from django.urls import reverse
        from ui.models import CartonAlternative
        self.client.post(reverse("ui:carton_opt_variant_save"), {
            "product_id": self.p.pk, "label": "Wariant B", "slot": "B",
            "length_cm": 40, "width_cm": 30, "height_cm": 25,
            "pack_l": 20, "pack_w": 15, "pack_h": 12,
            "next": f"?product={self.p.code}"})
        alt = CartonAlternative.objects.get(product=self.p, slot="B")
        self.assertEqual(alt.pack_h_cm, 12)
        resp = self.client.get(reverse("ui:carton_opt_variants"),
                               {"product": "VH-1"})
        self.assertEqual(len(resp.context["hier_b"]), 4)
        self.assertIsNone(resp.context["hier_c"])
```

- [ ] **Step 2: Run — verify FAIL** (KeyError `hier_a`)

- [ ] **Step 3: Implementacja**

W `carton_opt_variants`, po zbudowaniu obecnego kontekstu (przed `render`):

```python
    hier_a = _hierarchy_levels(instr, product) if instr else None
    def _slot_ctx(slot):
        alt = (product.carton_alternatives
               .filter(is_active=True, slot=slot).order_by("-updated_at").first())
        if not (alt and instr):
            return alt, None
        unit = ((alt.unit_l_cm, alt.unit_w_cm, alt.unit_h_cm)
                if alt.unit_l_cm and alt.unit_w_cm and alt.unit_h_cm else None)
        pack = ((alt.pack_l_cm, alt.pack_w_cm, alt.pack_h_cm)
                if alt.pack_l_cm and alt.pack_w_cm and alt.pack_h_cm else None)
        return alt, _hierarchy_levels(instr, product, unit=unit, pack=pack,
                                      carton=(alt.length_cm, alt.width_cm, alt.height_cm))
    variant_b, hier_b = _slot_ctx("B") if product else (None, None)
    variant_c, hier_c = _slot_ctx("C") if product else (None, None)
```

i dodaj do kontekstu `render(...)`: `"hier_a": hier_a, "variant_b": variant_b,
"hier_b": hier_b, "variant_c": variant_c, "hier_c": hier_c`.

W `carton_opt_variant_save` (linia ~341), po odczycie obecnych pól:

```python
    slot = (request.POST.get("slot") or "").strip().upper()[:1]
    if slot not in ("B", "C"):
        slot = ""
    def _opt_int(name):
        raw = (request.POST.get(name) or "").strip()
        return int(raw) if raw.isdigit() and int(raw) > 0 else None
    extra = {"slot": slot,
             "unit_l_cm": _opt_int("unit_l"), "unit_w_cm": _opt_int("unit_w"),
             "unit_h_cm": _opt_int("unit_h"),
             "pack_l_cm": _opt_int("pack_l"), "pack_w_cm": _opt_int("pack_w"),
             "pack_h_cm": _opt_int("pack_h")}
```

…i przekaż `**extra` do `create(...)`/aktualizacji wariantu (dopasuj do istniejącego
kształtu funkcji — zapis przez update pól + `save()` gdy edycja, jak dziś).
Zasada: zapis slotu `B`/`C` nadpisuje poprzedni wariant tego slotu (deaktywuj stary:
`product.carton_alternatives.filter(slot=slot, is_active=True).exclude(pk=alt.pk)
.update(is_active=False)`), żeby slot był unikalny.

- [ ] **Step 4: Run — verify PASS**

- [ ] **Step 5: Commit** (`feat(carton-opt): warianty B/C ze slotem, wymiarami poziomów i kaskadą w kontekście`)

---

### Task 5: Szablon — A stały + zakładki B/C, 4 rendery + mini-spec, paleta 720px

**Files:**
- Modify: `web/ui/templates/ui/carton_opt/variants.html`
- Test: `web/ui/tests/test_carton_opt_hierarchy.py` (dopisz smoke render)

**Interfaces:**
- Consumes: kontekst z Task 4 (`hier_a/hier_b/hier_c`, `variant_b/variant_c`).

- [ ] **Step 1: Failing test**

```python
class VariantsTemplateTests(TestCase):
    # setUpTestData/setUp jak w VariantsViewHierarchyTests (skopiuj)
    def test_renders_four_levels_and_tabs(self):
        from django.urls import reverse
        resp = self.client.get(reverse("ui:carton_opt_variants"), {"product": "VH-1"})
        self.assertContains(resp, "Sztuka / opakowanie")
        self.assertContains(resp, "Opakowanie zbiorcze")
        self.assertContains(resp, 'data-tab="B"')
        self.assertContains(resp, 'data-tab="C"')
        self.assertContains(resp, "height:720px")   # powiększony render palety
```

- [ ] **Step 2: Run — verify FAIL**

- [ ] **Step 3: Implementacja szablonu**

Zmiany w `variants.html`:

1. **Render palety A (linia ~87):** `style="width:100%;height:360px"` → `height:720px`.
2. **Partial poziomu** — dodaj blok wielokrotnego użytku (include albo pętla inline):

```html
{# pętla po poziomach wariantu: lv = {key,title,dims,three_data,spec} #}
{% for lv in levels %}
<div class="card" style="padding:0;margin-bottom:12px">
  <div style="padding:10px 16px;border-bottom:1px solid var(--gray-100);font-weight:700">
    {{ forloop.counter }}. {{ lv.title }}</div>
  {% if lv.three_data %}
  <canvas data-three='{{ lv.three_data }}'
          style="width:100%;height:{% if lv.key == 'pallet' %}720px{% else %}320px{% endif %};display:block"
          aria-label="Render 3D — {{ lv.title }}"></canvas>
  {% endif %}
  <table class="table" style="font-size:12px">
    <tr><td>Wymiary</td><td class="text-right" style="font-family:monospace">{{ lv.spec.dims_str }} cm</td></tr>
    {% if lv.spec.contains %}<tr><td>Mieści</td><td class="text-right"><strong>{{ lv.spec.contains }}</strong> szt. niższego poziomu</td></tr>{% endif %}
    {% if lv.spec.fill %}<tr><td>Wypełnienie</td><td class="text-right"><strong>{{ lv.spec.fill }}%</strong></td></tr>{% endif %}
    {% if lv.spec.error %}<tr><td colspan="2" style="color:var(--red)">{{ lv.spec.error }}</td></tr>{% endif %}
  </table>
</div>
{% endfor %}
```

Użyj `{% with levels=hier_a %}` dla sekcji A (pod istniejącym renderem palety —
albo zamiast niego, bo poziom „paleta" w pętli już renderuje paletę; **zostaw
istniejący canvas A** i renderuj w sekcji A tylko poziomy unit/inner_pack/carton,
żeby „wariant A nietykalny" znaczyło: dotychczasowa paleta + dołożone niższe poziomy).

3. **Zakładki B/C** pod sekcją A:

```html
<div class="co-nav" style="margin-top:20px">
  <a href="#" data-tab="B" class="js-vtab active">Wariant B</a>
  <a href="#" data-tab="C" class="js-vtab">Wariant C</a>
</div>
<div id="vtab-B">
  {% if hier_b %}{% with levels=hier_b %}{# pętla poziomów jw. #}{% endwith %}
  {% else %}<div class="card" style="padding:20px;color:var(--gray-500)">
    Brak wariantu B — zapisz wymiary w formularzu poniżej (slot B).</div>{% endif %}
</div>
<div id="vtab-C" style="display:none">{# analogicznie hier_c #}</div>
<script>
document.querySelectorAll(".js-vtab").forEach(a => a.addEventListener("click", e => {
  e.preventDefault();
  document.querySelectorAll(".js-vtab").forEach(x => x.classList.remove("active"));
  a.classList.add("active");
  document.getElementById("vtab-B").style.display = a.dataset.tab === "B" ? "" : "none";
  document.getElementById("vtab-C").style.display = a.dataset.tab === "C" ? "" : "none";
}));
</script>
```

4. **Formularz wariantu** (`.vform`, linia ~208): dodaj select `slot` (—/B/C) i dwie
   grupy opcjonalnych pól: `OPZ L/W/H [cm]` (`pack_l/pack_w/pack_h`) i
   `Sztuka L/W/H [cm]` (`unit_l/unit_w/unit_h`), etykiety po polsku, placeholder
   „bez zmian". Grid rozszerz (np. dwa wiersze).
5. **Skrypt renderujący** (linia ~242): warunek `{% if pallet_three %}` zamień na
   `{% if pallet_three or hier_a %}` — canvasy poziomów też mają `data-three`,
   istniejąca pętla `querySelectorAll("canvas[data-three]")` załapie wszystkie.
   Canvasy w ukrytej zakładce C wyrenderuj przy pierwszym przełączeniu (w handlerze
   zakładek zawołaj ponownie render dla canvasów bez flagi `data-rendered` — patrz
   wzorzec ResizeObserver z commitu 93a7cc2, wystarczy jednak proste: renderuj
   wszystkie od razu; three.js renderuje też w display:none canvas po pokazaniu —
   jeśli nie, dorender w handlerze).

- [ ] **Step 4: Run — verify PASS** + pełny moduł:
`... python manage.py test ui.tests.test_carton_opt_hierarchy ui.tests.test_carton_opt -v 1`

- [ ] **Step 5: Commit** (`feat(carton-opt): UI — hierarchia 4 poziomów, zakładki B/C, paleta 720px`)

---

### Task 6: Regresja całości + dowóz

**Files:** brak nowych.

- [ ] **Step 1:** `python -m unittest discover -s palletizer/tests -p "test_*.py"` → OK (26)
- [ ] **Step 2:** z `web/`: `... python manage.py test ui.tests -v 1` → OK (pełny suite)
- [ ] **Step 3:** `... python manage.py check` → no issues
- [ ] **Step 4:** migracje: sprawdź pojedynczy leaf (`python manage.py makemigrations --check --dry-run`); konflikt → skill palviz-migrate-guard
- [ ] **Step 5:** push + PR przez skill **palviz-ship** (branch `claude/carton-opt-3d-hierarchy`, base `main`)
