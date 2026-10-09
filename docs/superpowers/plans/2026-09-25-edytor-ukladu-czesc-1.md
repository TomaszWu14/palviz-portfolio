# Edytor układu — część 1 (szablony gniazd, adresy, zgodność z EWM) — plan wdrożenia

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Model magazynu (`wh3d`, „Modele magazynu”) generuje adresy miejsc paletowych z szablonów gniazd + reguł rzędów + wyjątków; „Wykryj z EWM” odtwarza obecną halę z mastera lokalizacji, a raport zgodności pokazuje 100% dla przejść B0 01–54.

**Architecture:** Trzy czyste moduły bez ORM (`wh3d/addressing.py` — generator adresów, `wh3d/ewm_detect.py` — propozycja z kodów EWM, `wh3d/ewm_compliance.py` — raport) + cienka warstwa ORM `wh3d/ewm_service.py` + nowe modele `BayTemplate` / `LocationOverride` i 3 pola na `WarehouseModelRack`. Ekrany: szablony gniazd (CRUD), 3 kolumny w edycji współrzędnych, „Wykryj z EWM” (podgląd → zapis), „Zgodność z EWM” (+ XLSX).

**Tech Stack:** Python 3.11, Django 5.2 (sync), openpyxl, testy `manage.py test` (unittest), szablony Django + CSS GROOVE (`web/ui/static/ui/css/app.css`).

**Spec:** `docs/superpowers/specs/2026-09-25-edytor-ukladu-czesc-1-design.md` (rewizja po analizie danych EWM).

## Global Constraints

- UI **Polish-first**: wszystkie etykiety, komunikaty, `verbose_name` po polsku.
- Plik ≤ **500 linii** (CI `web/scripts/file_size_check.py`).
- W `web/*/views/*.py` **bez star-importów** poza `views/__init__.py` (CI ruff F403/F405).
- `wh3d` importuje z `ui` tylko wspólne jądro: `from ui.views.core…`, `from ui.models…`, `from ui.roles…` (test `ui.tests.test_wh3d_boundary`).
- URL-e w `web/wh3d/urls.py`, nazwy w przestrzeni `ui:` (bez `app_name`); widoki re-eksportowane w `web/wh3d/views/__init__.py`.
- Uprawnienia: edycja `@_md_role` (Administratorzy + Master Data), podgląd i raport `@_planner` (zalogowany). W szablonach flaga edycji = `can_write_products` (context processor, ten sam zestaw ról co `_md_role`).
- Wszystko synchroniczne (WSGI), bez `async def`.
- Moduły `addressing.py`, `ewm_detect.py`, `ewm_compliance.py` **bez importów Django** (testowalne w `SimpleTestCase`).
- Format kodu EWM: `{strefa}-{przejście}-{gniazdo:02}{pozycja}{litera}` + `-1`/`-2` dla połówek (np. `B0-07-300C-1`).

## Środowisko (raz, przed Task 1)

Worktree: `.claude/worktrees/edytor`. Venv jest w głównym repo — podlinkuj go, żeby `web/scripts/test.sh` go znalazł:

```bash
cd .claude/worktrees/edytor
[ -e .venv ] || cmd //c mklink /J .venv C:\\Users\\tomas\\PycharmProjects\\PALVIZ\\.venv
sh web/scripts/test.sh wh3d.tests.test_model_geometry     # oczekiwane: System check OK + OK
```

Testy uruchamiasz zawsze przez `sh web/scripts/test.sh <etykiety>` (ustawia env + `manage.py check`). **Nigdy** `cd ..` + git — katalog nadrzędny `PycharmProjects` to osobne repo; git zawsze w worktree.

## Podział na PR-y (każdy: gałąź `claude/**` z aktualnego `origin/main` → PR → CI na runnerze → auto-merge → deploy)

| PR | Gałąź | Zadania |
|---|---|---|
| A | `claude/spec-edytor-ukladu` (PR #678) | rewizja speca + ten plan |
| B | `claude/edytor-adresy-modele` | Task 1, Task 2 |
| C | `claude/edytor-wykryj-ewm` | Task 3, Task 4 |
| D | `claude/edytor-szablony-ui` | Task 5 |
| E | `claude/edytor-zgodnosc-ui` | Task 6 |

Przed PR: pełny zestaw `sh web/scripts/test.sh` (wszystkie 4 appki) + `cd web && ../.venv/Scripts/python.exe manage.py makemigrations --check --dry-run` (z env jak w test.sh). Wszystkie commity pushuj **przed** otwarciem PR (auto-merge łapie stan z chwili uzbrojenia).

---

### Task 1: Generator adresów `wh3d/addressing.py`

**Files:**
- Create: `web/wh3d/addressing.py`
- Test: `web/wh3d/tests/test_addressing.py`

**Interfaces:**
- Consumes: nic (czysty Python). Obiekty wejściowe czytane przez atrybuty:
  - rząd: `zone: str, rack_id: str, n_bays: int, bay_width_cm: int, bay_numbers: str, reverse: bool`
  - szablon: `pallets_per_beam: int, levels: list[dict]` (`{"letter", "height_mm", "ewm_type", "split", "max_kg"}`)
  - wyjątek: `bay: int, position: int, letter: str ("" = całe gniazdo), half: int, action: str, value: str, template` (szablon albo None)
- Produces:
  - `CODE_RE`, `LETTER_ORDER = "ABCDSXGTYUZVW"`, `letter_rank(letter) -> tuple`
  - `parse_code(code) -> (zone, aisle, bay:int, position:int, letter, half:int) | None`
  - `make_code(zone, aisle, bay, position, letter, half=0) -> str`
  - `parse_bay_numbers(text) -> list[int]` (ValueError przy błędzie), `format_bay_numbers(numbers) -> str`
  - `row_bay_numbers(row) -> list[int]`
  - `expand_row(row, template, overrides=()) -> list[dict]` — klucze `code, bay, position, level_index, letter, half, ewm_type, blocked, along_m, z_m`
  - `expand_model(rows: [(row, template, overrides)]) -> (locations, duplicates)`; każde miejsce dostaje też `zone`, `aisle`; `duplicates = {kod: ["B0-07", …]}` tylko dla kodów z >1 miejsca

- [ ] **Step 1: Write the failing test** — `web/wh3d/tests/test_addressing.py`:

```python
"""Generator adresów modelu magazynu: szablon gniazda + reguła rzędu + wyjątki (spec cz. 1)."""
from types import SimpleNamespace as NS

from django.test import SimpleTestCase

from wh3d.addressing import (
    expand_model, expand_row, format_bay_numbers, parse_bay_numbers, parse_code, row_bay_numbers,
)


def lvl(letter, h, typ, split=False):
    return {"letter": letter, "height_mm": h, "ewm_type": typ, "split": split, "max_kg": 1000}


UPPER = [lvl("X", 1800, "0010"), lvl("Y", 1800, "0010"), lvl("Z", 1800, "0010")]
PICK = NS(pallets_per_beam=3, levels=[lvl("B", 400, "0052"), lvl("C", 400, "0052", True),
                                      lvl("D", 400, "0052", True)] + UPPER)
FLOOR = NS(pallets_per_beam=3, levels=[lvl("A", 1500, "0052")] + UPPER)
PASSAGE = NS(pallets_per_beam=4, levels=[lvl("Y", 1800, "0010"), lvl("Z", 1800, "0010")])


def row(**kw):
    base = dict(zone="B0", rack_id="07", n_bays=2, bay_width_cm=270, bay_numbers="30-31", reverse=False)
    base.update(kw)
    return NS(**base)


def ov(bay, action, letter="", position=0, half=0, value="", template=None):
    return NS(bay=bay, action=action, letter=letter, position=position, half=half, value=value, template=template)


def codes(locs):
    return [loc["code"] for loc in locs]


class ParseTests(SimpleTestCase):
    def test_parse_code_with_half_and_lowercase(self):
        self.assertEqual(parse_code("B0-07-300C-1"), ("B0", "07", 30, 0, "C", 1))
        self.assertEqual(parse_code(" b0-07-302a "), ("B0", "07", 30, 2, "A", 0))
        self.assertIsNone(parse_code("0051ZONE"))
        self.assertIsNone(parse_code("04.01"))

    def test_bay_numbers_ranges_round_trip(self):
        nums = parse_bay_numbers("10-47, 50")
        self.assertEqual(nums, list(range(10, 48)) + [50])
        self.assertEqual(format_bay_numbers(nums), "10-47,50")
        self.assertEqual(parse_bay_numbers(""), [])
        self.assertEqual(format_bay_numbers([29, 30, 33, 34, 35]), "29-30,33-35")

    def test_bay_numbers_invalid(self):
        for bad in ("10-5", "a", "10,10", "3-"):
            with self.assertRaises(ValueError, msg=bad):
                parse_bay_numbers(bad)

    def test_row_numbers_default_and_truncation(self):
        self.assertEqual(row_bay_numbers(row(bay_numbers="", n_bays=3)), [1, 2, 3])
        self.assertEqual(row_bay_numbers(row(bay_numbers="10-20", n_bays=2)), [10, 11])
        self.assertEqual(row_bay_numbers(row(bay_numbers="29-30,33", n_bays=3)), [29, 30, 33])


class ExpandRowTests(SimpleTestCase):
    def test_pick_template_with_halves(self):
        locs = expand_row(row(), PICK)
        # na gniazdo: B 3 + C½ 6 + D½ 6 + X/Y/Z 9 = 24; dwa gniazda = 48
        self.assertEqual(len(locs), 48)
        c = codes(locs)
        self.assertEqual(c[:4], ["B0-07-300B", "B0-07-301B", "B0-07-302B", "B0-07-300C-1"])
        for code in ("B0-07-300C-2", "B0-07-302D-2", "B0-07-302Z", "B0-07-310B", "B0-07-312Z"):
            self.assertIn(code, c)
        self.assertNotIn("B0-07-300C", c)
        self.assertEqual(len(set(c)), 48)

    def test_geometry_along_and_z(self):
        by = {loc["code"]: loc for loc in expand_row(row(), PICK)}
        self.assertEqual(by["B0-07-300B"]["along_m"], 0.45)          # belka 2,7 m / 3 palety
        self.assertEqual(by["B0-07-300C-1"]["along_m"], 0.225)       # połówka = pół pozycji
        self.assertEqual(by["B0-07-300C-2"]["along_m"], 0.675)
        self.assertEqual(by["B0-07-312X"]["along_m"], 4.95)          # 2. gniazdo, pozycja 2
        self.assertEqual(by["B0-07-300X"]["z_m"], 1.2)               # B+C+D = 3 × 0,4 m
        self.assertEqual(by["B0-07-300X"]["level_index"], 3)
        self.assertEqual(by["B0-07-300C-1"]["ewm_type"], "0052")
        self.assertFalse(by["B0-07-300B"]["blocked"])

    def test_reverse_mirrors_along(self):
        by = {loc["code"]: loc for loc in expand_row(row(reverse=True), PICK)}
        self.assertEqual(by["B0-07-300B"]["along_m"], 4.95)          # 5,4 m − 0,45 m
        self.assertEqual(by["B0-07-312X"]["along_m"], 0.45)

    def test_numbering_with_gaps(self):
        c = codes(expand_row(row(n_bays=3, bay_numbers="29-30,33"), FLOOR))
        self.assertEqual(sorted({code[6:8] for code in c}), ["29", "30", "33"])

    def test_bay_skip_and_bay_template(self):
        locs = expand_row(row(n_bays=4, bay_numbers="29-32"), FLOOR,
                          [ov(31, "skip"), ov(32, "skip"), ov(30, "template", template=PASSAGE)])
        c = codes(locs)
        self.assertFalse([x for x in c if x[6:8] in ("31", "32")])
        self.assertIn("B0-07-303Y", c)                               # przejazd: 4 palety, tylko Y/Z
        self.assertNotIn("B0-07-300A", c)
        self.assertIn("B0-07-290A", c)
        self.assertEqual(len(c), 12 + 8)

    def test_template_none_gives_nothing(self):
        self.assertEqual(expand_row(row(), None), [])

    def test_location_overrides(self):
        locs = expand_row(row(n_bays=1, bay_numbers="30"), FLOOR, [
            ov(30, "skip", "A", 1),
            ov(30, "add", "A", 3, value="0050"),
            ov(30, "rename", "X", 0, value="B0-07-SPEC1"),
            ov(30, "ewm_type", "Y", 0, value="0011"),
            ov(30, "block", "Z", 2),
        ])
        by = {loc["code"]: loc for loc in locs}
        self.assertNotIn("B0-07-301A", by)
        self.assertEqual(by["B0-07-303A"]["ewm_type"], "0050")
        self.assertIn("B0-07-SPEC1", by)
        self.assertNotIn("B0-07-300X", by)
        self.assertEqual(by["B0-07-300Y"]["ewm_type"], "0011")
        self.assertTrue(by["B0-07-302Z"]["blocked"])
        self.assertEqual(len(locs), 12)                              # 12 − skip + add

    def test_split_and_unsplit(self):
        c = codes(expand_row(row(n_bays=1, bay_numbers="30"), FLOOR, [ov(30, "split", "A", 0)]))
        self.assertIn("B0-07-300A-1", c)
        self.assertIn("B0-07-300A-2", c)
        self.assertNotIn("B0-07-300A", c)
        c = codes(expand_row(row(n_bays=1, bay_numbers="30"), PICK, [ov(30, "unsplit", "C", 1)]))
        self.assertIn("B0-07-301C", c)
        self.assertNotIn("B0-07-301C-1", c)


class ExpandModelTests(SimpleTestCase):
    def test_duplicates_between_rows(self):
        a, b = row(), row(rack_id="08")
        locs, dups = expand_model([(a, FLOOR, []), (b, FLOOR, [])])
        self.assertEqual(dups, {})
        self.assertEqual(locs[0]["zone"], "B0")
        self.assertEqual(locs[0]["aisle"], "07")
        _, dups = expand_model([(a, FLOOR, []), (row(), FLOOR, [])])
        self.assertEqual(dups["B0-07-300A"], ["B0-07", "B0-07"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sh web/scripts/test.sh wh3d.tests.test_addressing`
Expected: FAIL — `ModuleNotFoundError: No module named 'wh3d.addressing'`

- [ ] **Step 3: Write minimal implementation** — `web/wh3d/addressing.py`:

```python
"""Adresy miejsc paletowych modelu magazynu: szablon gniazda + reguła rzędu + wyjątki.

Czysty Python (bez ORM): funkcje czytają atrybuty obiektów, więc przyjmują zarówno
instancje modeli (BayTemplate / WarehouseModelRack / LocationOverride), jak i proste
obiekty w testach. Konwencja kodu EWM: `{strefa}-{przejście}-{gniazdo:02}{pozycja}{litera}`
+ `-1`/`-2` dla połówek, np. `B0-07-300C-1`.
"""
import re
from collections import defaultdict

CODE_RE = re.compile(r"^([A-Z0-9]+)-([A-Z0-9]+)-(\d{2})(\d)([A-Z])(?:-([12]))?$")
# Kolejność liter od podłogi w górę (B0: A–D/S = poziom 1, X/G/T = 2, Y/U = 3, Z/V = 4, W = 5).
LETTER_ORDER = "ABCDSXGTYUZVW"


def letter_rank(letter):
    """Klucz sortowania liter poziomów: znane litery wg LETTER_ORDER, obce na końcu."""
    i = LETTER_ORDER.find(letter)
    return (i if i >= 0 else len(LETTER_ORDER), letter)


def parse_code(code):
    """Kod EWM → (strefa, przejście, gniazdo, pozycja, litera, połówka) albo None."""
    m = CODE_RE.match((code or "").strip().upper())
    if not m:
        return None
    zone, aisle, bay, pos, letter, half = m.groups()
    return zone, aisle, int(bay), int(pos), letter, int(half or 0)


def make_code(zone, aisle, bay, position, letter, half=0):
    return f"{zone}-{aisle}-{bay:02d}{position}{letter}" + (f"-{half}" if half else "")


def parse_bay_numbers(text):
    """„10-47,50” → [10, …, 47, 50]. Pusty tekst → []. Błędny zapis → ValueError."""
    out = []
    for part in (text or "").replace(" ", "").split(","):
        if not part:
            continue
        lo, sep, hi = part.partition("-")
        if not lo.isdigit() or (sep and not hi.isdigit()):
            raise ValueError(f"Nieprawidłowy zakres gniazd: „{part}”")
        lo, hi = int(lo), int(hi) if sep else int(lo)
        if hi < lo:
            raise ValueError(f"Zakres malejący: „{part}”")
        out.extend(range(lo, hi + 1))
    if len(set(out)) != len(out):
        raise ValueError("Numery gniazd się powtarzają")
    return out


def format_bay_numbers(numbers):
    """[10, …, 47, 50] → „10-47,50” (odwrotność parse_bay_numbers)."""
    parts, nums, i = [], sorted(numbers), 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ",".join(parts)


def row_bay_numbers(row):
    """Numery gniazd rzędu w kolejności fizycznej; pusta reguła = 1..n_bays; nadmiar ucinany."""
    nums = parse_bay_numbers(row.bay_numbers)
    return nums[: row.n_bays] if nums else list(range(1, row.n_bays + 1))


def _bay_locations(row, bay, slot, tpl, loc_ov):
    """Miejsca jednego gniazda (numer `bay`, fizyczny indeks `slot`) wg szablonu i wyjątków miejsca."""
    bay_w = row.bay_width_cm / 100
    k = max(1, tpl.pallets_per_beam)
    pos_w = bay_w / k
    level_at, z = {}, 0.0
    for li, lvl in enumerate(tpl.levels):
        level_at[lvl["letter"]] = (li, z)
        z += lvl["height_mm"] / 1000
    cells = []                                   # (pozycja, litera, połówka, typ EWM)
    for lvl in tpl.levels:
        for position in range(k):
            ov = loc_ov.get((bay, position, lvl["letter"], 0), {})
            split = "split" in ov or (lvl.get("split") and "unsplit" not in ov)
            cells += [(position, lvl["letter"], h, lvl.get("ewm_type", "")) for h in ((1, 2) if split else (0,))]
    cells += [(p, letter, h, ov["add"].value) for (b, p, letter, h), ov in loc_ov.items()
              if b == bay and "add" in ov]
    out = []
    for position, letter, half, ewm_type in cells:
        ov = loc_ov.get((bay, position, letter, half), {})
        if "skip" in ov:
            continue
        li, z_m = level_at.get(letter, (len(tpl.levels), z))
        along = slot * bay_w + position * pos_w + (pos_w / 2 if not half else (half - 0.5) * pos_w / 2)
        out.append({
            "code": ov["rename"].value if "rename" in ov
            else make_code(row.zone, row.rack_id, bay, position, letter, half),
            "bay": bay, "position": position, "level_index": li, "letter": letter, "half": half,
            "ewm_type": ov["ewm_type"].value if "ewm_type" in ov else ewm_type,
            "blocked": "block" in ov,
            # reverse: numeracja od końca rzędu → lustrzane położenie wzdłuż rzędu
            "along_m": round(bay_w * row.n_bays - along if row.reverse else along, 3),
            "z_m": round(z_m, 3),
        })
    return out


def expand_row(row, template, overrides=()):
    """Rząd + szablon domyślny + wyjątki → lista miejsc (dict).

    Klucze: code, bay, position, level_index, letter, half, ewm_type, blocked, along_m, z_m.
    Wyjątki gniazda (letter == ""): `template` (inny szablon), `skip` (gniazdo bez miejsc).
    Wyjątki miejsca: skip / add / rename / ewm_type / block / split / unsplit."""
    bay_ov, loc_ov = defaultdict(dict), defaultdict(dict)
    for ov in overrides:
        if ov.letter:
            loc_ov[(ov.bay, ov.position, ov.letter, ov.half)][ov.action] = ov
        else:
            bay_ov[ov.bay][ov.action] = ov
    out = []
    for slot, bay in enumerate(row_bay_numbers(row)):
        b_ov = bay_ov.get(bay, {})
        tpl = b_ov["template"].template if "template" in b_ov else template
        if "skip" in b_ov or tpl is None:
            continue
        out += _bay_locations(row, bay, slot, tpl, loc_ov)
    return out


def expand_model(rows):
    """[(rząd, szablon, wyjątki), …] → (miejsca z kluczami zone/aisle, {kod: [„B0-07”, …]} duplikatów)."""
    locations, seen = [], defaultdict(list)
    for row, template, overrides in rows:
        for loc in expand_row(row, template, overrides):
            loc["zone"], loc["aisle"] = row.zone, row.rack_id
            locations.append(loc)
            seen[loc["code"]].append(f"{row.zone}-{row.rack_id}")
    return locations, {c: racks for c, racks in seen.items() if len(racks) > 1}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sh web/scripts/test.sh wh3d.tests.test_addressing`
Expected: `Ran 13 tests … OK`

- [ ] **Step 5: Commit**

```bash
git add web/wh3d/addressing.py web/wh3d/tests/test_addressing.py
git commit -m "feat(wh3d): generator adresów z szablonu gniazda, reguły rzędu i wyjątków"
```

---

### Task 2: Modele `BayTemplate`, `LocationOverride`, pola rzędu + migracja

**Files:**
- Modify: `web/wh3d/models.py` (import `re`, `ValidationError`, `parse_bay_numbers`; nowa klasa `BayTemplate` przed sekcją `WarehouseModel`; 3 pola w `WarehouseModelRack`; nowa klasa `LocationOverride` zaraz po `WarehouseModelRack`)
- Create: `web/wh3d/migrations/0003_baytemplate_locationoverride_and_more.py` (generowana)
- Test: `web/wh3d/tests/test_bay_template_model.py`

**Interfaces:**
- Consumes: `wh3d.addressing.parse_bay_numbers`
- Produces:
  - `BayTemplate(name, beam_mm, pallets_per_beam, depth_mm, levels: list[dict], notes, created_at)`; właściwości `level_label` („B C½ D½ X Y Z”), `ewm_types_label` („0052/0010”); `clean()` z polskimi błędami
  - `WarehouseModelRack.template` (FK `BayTemplate`, `SET_NULL`, `related_name="racks"`), `.bay_numbers` (str, walidator), `.reverse` (bool)
  - `LocationOverride(rack FK related_name="overrides", bay, position, letter, half, action, value, template FK PROTECT related_name="bay_overrides")`, `LocationOverride.ACTIONS`
  - `validate_bay_numbers(value)`

- [ ] **Step 1: Write the failing test** — `web/wh3d/tests/test_bay_template_model.py`:

```python
"""Modele części 1: szablon gniazda (walidacja), reguła rzędu, wyjątki (unikalność, ochrona szablonu)."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase

from wh3d.models import BayTemplate, LocationOverride, WarehouseModel, WarehouseModelRack


def levels(*letters, split=()):
    return [{"letter": L, "height_mm": 1000, "ewm_type": "0010", "split": L in split, "max_kg": 1000}
            for L in letters]


class BayTemplateTests(TestCase):
    def test_valid_template_and_labels(self):
        t = BayTemplate(name="3 pal.", pallets_per_beam=3, levels=levels("B", "C", "X", split=("C",)))
        t.full_clean()
        self.assertEqual(t.level_label, "B C½ X")
        self.assertEqual(t.ewm_types_label, "0010")

    def test_invalid_templates_raise_polish_errors(self):
        cases = {
            "co najmniej jeden poziom": BayTemplate(name="a", pallets_per_beam=3, levels=[]),
            "unikalne": BayTemplate(name="b", pallets_per_beam=3, levels=levels("A", "A")),
            "wysokość": BayTemplate(name="c", pallets_per_beam=3,
                                    levels=[{"letter": "A", "height_mm": 0, "ewm_type": "", "split": False}]),
            "palet": BayTemplate(name="d", pallets_per_beam=0, levels=levels("A")),
            "litera": BayTemplate(name="e", pallets_per_beam=1, levels=levels("AB")),
        }
        for fragment, t in cases.items():
            with self.assertRaises(ValidationError, msg=fragment) as cm:
                t.full_clean()
            self.assertIn(fragment, " ".join(cm.exception.messages))


class RackRuleAndOverrideTests(TestCase):
    def setUp(self):
        self.wm = WarehouseModel.objects.create(name="M")
        self.rack = WarehouseModelRack.objects.create(model=self.wm, zone="B0", rack_id="07", n_bays=2)
        self.tpl = BayTemplate.objects.create(name="T", pallets_per_beam=3, levels=levels("A"))

    def test_rack_defaults_and_bay_numbers_validation(self):
        self.assertIsNone(self.rack.template)
        self.assertEqual(self.rack.bay_numbers, "")
        self.assertFalse(self.rack.reverse)
        self.rack.bay_numbers = "10-5"
        with self.assertRaises(ValidationError):
            self.rack.full_clean()
        self.rack.bay_numbers = "10-47,50"
        self.rack.full_clean()

    def test_override_unique(self):
        LocationOverride.objects.create(rack=self.rack, bay=30, letter="A", action="skip")
        with self.assertRaises(IntegrityError):
            LocationOverride.objects.create(rack=self.rack, bay=30, letter="A", action="skip")

    def test_template_used_by_override_is_protected_rack_is_set_null(self):
        self.rack.template = self.tpl
        self.rack.save()
        other = BayTemplate.objects.create(name="U", pallets_per_beam=4, levels=levels("Y"))
        LocationOverride.objects.create(rack=self.rack, bay=29, action="template", template=other)
        with self.assertRaises(ProtectedError):
            other.delete()
        self.tpl.delete()
        self.rack.refresh_from_db()
        self.assertIsNone(self.rack.template)
        self.assertEqual(self.rack.overrides.count(), 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sh web/scripts/test.sh wh3d.tests.test_bay_template_model`
Expected: FAIL — `ImportError: cannot import name 'BayTemplate' from 'wh3d.models'`

- [ ] **Step 3: Write minimal implementation** — w `web/wh3d/models.py`:

Na górze pliku (obok istniejącego `from django.db import models`):

```python
import re

from django.core.exceptions import ValidationError

from .addressing import parse_bay_numbers
```

Przed komentarzem `# ─── Shipment module ───` (tuż przed `class WarehouseModel`) dodaj:

```python
# ─── Szablony gniazd i adresy (edytor układu, część 1) ───────────────────────


def validate_bay_numbers(value):
    """Numeracja gniazd rzędu: zakresy „10-47,50”."""
    try:
        parse_bay_numbers(value)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


class BayTemplate(models.Model):
    """Szablon gniazda (słupa regału): belka, palety na belce, poziomy od podłogi w górę.

    levels: [{"letter": "B", "height_mm": 400, "ewm_type": "0052", "split": false, "max_kg": 1000}, …]
    `split` = miejsce dzielone wszerz na dwie połówki (kody z końcówką -1/-2)."""
    name = models.CharField(max_length=100, verbose_name="Nazwa")
    beam_mm = models.IntegerField(default=2700, verbose_name="Szerokość belki [mm]")
    pallets_per_beam = models.PositiveSmallIntegerField(default=3, verbose_name="Palet na belce")
    depth_mm = models.IntegerField(default=1100, verbose_name="Głębokość [mm]")
    levels = models.JSONField(default=list, blank=True, verbose_name="Poziomy (od dołu)")
    notes = models.CharField(max_length=200, blank=True, default="", verbose_name="Uwagi")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Szablon gniazda"
        verbose_name_plural = "Szablony gniazd"

    def __str__(self):
        return self.name

    @property
    def level_label(self):
        return " ".join(lv["letter"] + ("½" if lv.get("split") else "") for lv in self.levels)

    @property
    def ewm_types_label(self):
        return "/".join(dict.fromkeys(lv["ewm_type"] for lv in self.levels if lv.get("ewm_type")))

    def clean(self):
        errors = []
        if (self.pallets_per_beam or 0) < 1:
            errors.append("Liczba palet na belce musi być ≥ 1.")
        if not self.levels:
            errors.append("Szablon musi mieć co najmniej jeden poziom.")
        letters = [lv.get("letter") for lv in self.levels]
        if len(set(letters)) != len(letters):
            errors.append("Litery poziomów muszą być unikalne.")
        for lv in self.levels:
            if not re.fullmatch(r"[A-Z]", lv.get("letter") or ""):
                errors.append(f"Nieprawidłowa litera poziomu: „{lv.get('letter')}” (jedna wielka litera A–Z).")
            height = lv.get("height_mm")
            if not isinstance(height, int) or height <= 0:
                errors.append(f"Poziom {lv.get('letter')}: wysokość musi być > 0 mm.")
        if errors:
            raise ValidationError(errors)
```

W klasie `WarehouseModelRack`, po polu `angle_deg`, dodaj:

```python
    # Edytor układu, część 1: szablon domyślny gniazd + reguła adresu (numeracja, kierunek).
    template = models.ForeignKey(BayTemplate, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="racks", verbose_name="Szablon gniazda")
    bay_numbers = models.CharField(max_length=200, blank=True, default="", validators=[validate_bay_numbers],
                                   verbose_name="Numeracja gniazd",
                                   help_text="Zakresy, np. 10-47,50. Puste = 1…liczba gniazd.")
    reverse = models.BooleanField(default=False, verbose_name="Numeracja od końca rzędu")
```

Zaraz po klasie `WarehouseModelRack` (przed `class WarehouseHallFeature`) dodaj:

```python
class LocationOverride(models.Model):
    """Wyjątek adresu: nadpisuje wynik szablonu dla jednego miejsca albo całego gniazda (letter = "")."""
    ACTIONS = [
        ("template", "Inny szablon gniazda"),
        ("skip", "Pomiń"),
        ("add", "Dodaj miejsce"),
        ("rename", "Własny adres"),
        ("ewm_type", "Inny typ EWM"),
        ("split", "Podziel na połówki"),
        ("unsplit", "Scal połówki"),
        ("block", "Blokada"),
    ]
    rack = models.ForeignKey(WarehouseModelRack, on_delete=models.CASCADE, related_name="overrides")
    bay = models.IntegerField(verbose_name="Gniazdo (numer z adresu)")
    position = models.PositiveSmallIntegerField(default=0, verbose_name="Pozycja palety")
    letter = models.CharField(max_length=1, blank=True, default="", verbose_name="Litera poziomu")
    half = models.PositiveSmallIntegerField(default=0, verbose_name="Połówka (0/1/2)")
    action = models.CharField(max_length=10, choices=ACTIONS, verbose_name="Wyjątek")
    value = models.CharField(max_length=50, blank=True, default="", verbose_name="Wartość (kod / typ EWM)")
    template = models.ForeignKey(BayTemplate, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="bay_overrides", verbose_name="Szablon (dla wyjątku gniazda)")

    class Meta:
        ordering = ["rack", "bay", "letter", "position", "half"]
        verbose_name = "Wyjątek adresu"
        verbose_name_plural = "Wyjątki adresów"
        constraints = [
            models.UniqueConstraint(fields=["rack", "bay", "position", "letter", "half", "action"],
                                    name="locationoverride_unique"),
        ]

    def __str__(self):
        return f"{self.rack} gn. {self.bay} {self.letter or '*'}: {self.get_action_display()}"
```

Wygeneruj migrację:

```bash
cd web
DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. \
  ../.venv/Scripts/python.exe manage.py makemigrations wh3d
cd ..
```

Expected: `web/wh3d/migrations/0003_baytemplate_…py` z `CreateModel BayTemplate`, `AddField` ×3 na `warehousemodelrack`, `CreateModel LocationOverride`. Sprawdź, że `dependencies = [("wh3d", "0002_design_variant")]` i że NIE ma operacji na innych modelach (jeśli są — przerwij i zgłoś).

- [ ] **Step 4: Run test to verify it passes**

Run: `sh web/scripts/test.sh wh3d.tests.test_bay_template_model wh3d.tests.test_addressing wh3d.tests.test_warehouse_model_view`
Expected: OK (wszystkie)

- [ ] **Step 5: Commit**

```bash
git add web/wh3d/models.py web/wh3d/migrations/0003_*.py web/wh3d/tests/test_bay_template_model.py
git commit -m "feat(wh3d): szablony gniazd, reguła adresu rzędu i wyjątki adresów (modele + migracja)"
```

**PR B** (po Task 1 + Task 2): gałąź `claude/edytor-adresy-modele`, tytuł „feat(wh3d): adresy z szablonów gniazd — generator + modele (edytor układu cz. 1)”.

---

### Task 3: „Wykryj z EWM” — `wh3d/ewm_detect.py`

**Files:**
- Create: `web/wh3d/ewm_detect.py`
- Create: `web/wh3d/tests/data/ewm_b0_sample.csv` (skopiuj z `scratchpad/ewm_b0_sample.csv` — 3 327 kodów B0 z przejść 07, 08, 34, 38, 48, 54; format `code;typ`, 1. linia to komentarz `#`)
- Create: `web/wh3d/tests/ewm_sample.py` (loader próbki, współdzielony przez testy Task 3/4/6)
- Test: `web/wh3d/tests/test_ewm_detect.py`

**Interfaces:**
- Consumes: `wh3d.addressing.format_bay_numbers, letter_rank, parse_code, expand_model`
- Produces:
  - `detect(rows, master, templates=()) -> dict`:
    - `rows`: `[{"zone", "rack_id", "n_bays"}]`; `master`: iterowalne `(kod, typ_ewm, wysokość_mm, udźwig_kg)`; `templates`: obiekty z `pk, name, pallets_per_beam, levels`
    - wynik: `{"templates": [{"key", "pk", "name", "pallets_per_beam", "beam_mm"?, "levels", "bays"}], "rows": [{"zone", "rack_id", "template", "bay_numbers", "overrides", "codes"}], "missing_rows": ["B0-99", …]}`
    - `key` = `"pk:<id>"` (istniejący) albo `"new:<i>"`; `override` = `{"bay", "position", "letter", "half", "action", "value"}` + `"template": key` dla akcji `template`
  - `wh3d/tests/ewm_sample.py`: `SAMPLE_N_BAYS = {"07": 39, "08": 39, "34": 42, "38": 41, "48": 36, "54": 34}`, `load_sample() -> [(kod, typ)]`

- [ ] **Step 1: Write the failing test**

`web/wh3d/tests/ewm_sample.py`:

```python
"""Próbka prawdziwego eksportu EWM (B0, przejścia 07/08/34/38/48/54) dla testów części 1."""
from pathlib import Path

SAMPLE = Path(__file__).with_name("data") / "ewm_b0_sample.csv"
# Liczba gniazd rzędów w modelu z rysunku (regaly_z_rysunku.csv, kolumna n_bays).
SAMPLE_N_BAYS = {"07": 39, "08": 39, "34": 42, "38": 41, "48": 36, "54": 34}


def load_sample():
    """[(kod, typ EWM)] z pliku próbki (pomija komentarz i nagłówek)."""
    lines = [ln for ln in SAMPLE.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    return [tuple(ln.split(";")) for ln in lines[1:]]
```

`web/wh3d/tests/test_ewm_detect.py`:

```python
"""„Wykryj z EWM”: propozycja szablonów, numeracji i wyjątków z kodów; round-trip = te same kody."""
from collections import defaultdict
from types import SimpleNamespace as NS

from django.test import SimpleTestCase

from wh3d.addressing import expand_model
from wh3d.ewm_detect import detect
from wh3d.tests.ewm_sample import SAMPLE_N_BAYS, load_sample


def master_of(pairs):
    return [(code, typ, 0, 0) for code, typ in pairs]


def rows_of(n_bays):
    return [{"zone": "B0", "rack_id": a, "n_bays": n} for a, n in n_bays.items()]


def expand_proposal(prop, n_bays, width_cm=280):
    """Propozycja → obiekty jak z bazy → rozwinięte kody per przejście."""
    tpl = {t["key"]: NS(pallets_per_beam=t["pallets_per_beam"], levels=t["levels"]) for t in prop["templates"]}
    triples = []
    for r in prop["rows"]:
        row = NS(zone=r["zone"], rack_id=r["rack_id"], n_bays=n_bays[r["rack_id"]], bay_width_cm=width_cm,
                 bay_numbers=r["bay_numbers"], reverse=False)
        ovs = [NS(template=tpl.get(o.get("template")), **{k: v for k, v in o.items() if k != "template"})
               for o in r["overrides"]]
        triples.append((row, tpl[r["template"]], ovs))
    locs, dups = expand_model(triples)
    by_aisle = defaultdict(set)
    for loc in locs:
        by_aisle[loc["aisle"]].add(loc["code"])
    return by_aisle, dups


class DetectRealSampleTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pairs = load_sample()
        cls.prop = detect(rows_of(SAMPLE_N_BAYS), master_of(cls.pairs))
        cls.rows = {r["rack_id"]: r for r in cls.prop["rows"]}
        cls.names = {t["key"]: t["name"] for t in cls.prop["templates"]}

    def test_round_trip_reproduces_every_ewm_code(self):
        by_aisle, dups = expand_proposal(self.prop, SAMPLE_N_BAYS)
        ewm = defaultdict(set)
        for code, _ in self.pairs:
            ewm[code[3:5]].add(code)
        self.assertEqual(dups, {})
        for aisle in SAMPLE_N_BAYS:
            self.assertEqual(by_aisle[aisle], ewm[aisle], aisle)

    def test_templates_are_new_and_named_from_letters_and_types(self):
        self.assertEqual(len(self.prop["templates"]), 18)
        self.assertTrue(all(t["pk"] is None for t in self.prop["templates"]))
        self.assertIn("3 pal. · B C½ D½ X Y Z · 0052/0010", self.names.values())
        self.assertIn("4 pal. · Y Z · 0010", self.names.values())       # przejazd nad drogą (gniazdo 29)

    def test_row_defaults_and_numbering(self):
        self.assertEqual(self.names[self.rows["07"]["template"]], "3 pal. · A X Y Z · 0052/0010")
        self.assertEqual(self.rows["07"]["bay_numbers"], "10-48")
        self.assertEqual(self.rows["08"]["bay_numbers"], "10-47,50")    # EWM przeskakuje 48–49
        self.assertEqual(self.rows["38"]["bay_numbers"], "10-28,30-51")  # bez gniazda 29
        self.assertEqual(self.rows["54"]["bay_numbers"], "29-30,33-64")  # bez 31–32 (n_bays = 34)
        self.assertEqual(self.names[self.rows["48"]["template"]], "3 pal. · B C D X Y Z · 0010/0011")

    def test_overrides_are_bay_templates_plus_single_type_exception(self):
        extra = [o for o in self.rows["08"]["overrides"] if o["action"] != "template"]
        self.assertEqual(extra, [{"bay": 22, "position": 1, "letter": "A", "half": 0,
                                  "action": "ewm_type", "value": "0050"}])
        bay_tpl = [o for o in self.rows["07"]["overrides"] if o["action"] == "template"]
        self.assertEqual(len(bay_tpl), 22)
        self.assertTrue(all(o["letter"] == "" and o["template"].startswith("new:") for o in bay_tpl))

    def test_physical_bays_without_codes_become_bay_skips(self):
        prop = detect(rows_of({"54": 36}), master_of(self.pairs))
        row = prop["rows"][0]
        self.assertEqual(row["bay_numbers"], "29-64")
        skips = [o["bay"] for o in row["overrides"] if o["action"] == "skip" and o["letter"] == ""]
        self.assertEqual(skips, [31, 32])
        by_aisle, _ = expand_proposal(prop, {"54": 36})
        self.assertEqual(by_aisle["54"], {c for c, _ in self.pairs if c.startswith("B0-54-")})

    def test_existing_template_is_reused(self):
        levels = [{"letter": "A", "height_mm": 1500, "ewm_type": "0052", "split": False, "max_kg": 0}] + [
            {"letter": L, "height_mm": 1800, "ewm_type": "0010", "split": False, "max_kg": 0} for L in "XYZ"]
        mine = NS(pk=5, name="Mój paletowy", pallets_per_beam=3, levels=levels)
        prop = detect(rows_of({"07": 39}), master_of(self.pairs), [mine])
        self.assertEqual(prop["rows"][0]["template"], "pk:5")
        self.assertEqual(sum(1 for t in prop["templates"] if t["name"] == "3 pal. · A X Y Z · 0052/0010"), 0)

    def test_rows_without_codes_are_reported(self):
        prop = detect(rows_of({"07": 39, "99": 5}), master_of(self.pairs))
        self.assertEqual(prop["missing_rows"], ["B0-99"])


class DetectIrregularBayTests(SimpleTestCase):
    def test_irregular_bay_gets_nearest_template_plus_skip_and_add(self):
        pairs = []
        for bay in (10, 11, 12):
            pairs += [(f"B0-01-{bay}{p}{L}", "0010") for p in range(3) for L in "AX"]
        pairs += [("B0-01-130A", "0010"), ("B0-01-131A", "0010"), ("B0-01-130B", "0010")]
        pairs += [(f"B0-01-13{p}X", "0010") for p in range(3)]
        prop = detect(rows_of({"01": 4}), master_of(pairs))
        row = prop["rows"][0]
        self.assertEqual(len(prop["templates"]), 1)
        loc_ov = sorted((o["action"], o["bay"], o["position"], o["letter"]) for o in row["overrides"])
        self.assertEqual(loc_ov, [("add", 13, 0, "B"), ("skip", 13, 2, "A")])
        by_aisle, _ = expand_proposal(prop, {"01": 4})
        self.assertEqual(by_aisle["01"], {c for c, _ in pairs})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sh web/scripts/test.sh wh3d.tests.test_ewm_detect`
Expected: FAIL — `ModuleNotFoundError: No module named 'wh3d.ewm_detect'`

- [ ] **Step 3: Write minimal implementation** — `web/wh3d/ewm_detect.py`:

```python
"""„Wykryj z EWM”: kody lokalizacji z mastera → propozycja szablonów gniazd, reguł rzędów
i wyjątków, która po rozwinięciu (addressing.expand_row) odtwarza DOKŁADNIE te same kody.

Czysty Python (bez ORM) — zapis propozycji robi wh3d.ewm_service.apply_proposal.
Wzór gniazda = zbiór (pozycja, litera, połówka) + typ EWM litery. Wzór „regularny”
(pełna siatka pozycji 0..k-1 × litery, połówki per litera) staje się szablonem;
nieregularne gniazdo dostaje najbliższy szablon + wyjątki skip/add.
"""
from collections import Counter, defaultdict
from statistics import median

from .addressing import format_bay_numbers, letter_rank, parse_code

BEAM_MM = {2: 1825, 3: 2700, 4: 3600}   # typowe belki; inne k → k × 900 mm (do poprawy w formularzu)
DEFAULT_HEIGHT_MM = 1000                  # eksport EWM nie niesie wysokości → wartość do poprawy


def _grid(k, letters):
    """k pozycji × [(litera, split)] → zbiór komórek (pozycja, litera, połówka)."""
    return {(p, letter, h) for letter, split in letters for p in range(k)
            for h in ((1, 2) if split else (0,))}


def _shape(cells, types):
    """Komórki gniazda → (sygnatura obrysu, czy siatka regularna).
    Sygnatura = (k, ((litera, split, typ), …)) — ten sam kształt co _template_sig."""
    k = max(p for p, _, _ in cells) + 1
    split = defaultdict(bool)
    for _, letter, h in cells:
        split[letter] |= bool(h)
    letters = sorted(split, key=letter_rank)
    sig = (k, tuple((L, split[L], types[L]) for L in letters))
    return sig, set(cells) == _grid(k, [(L, split[L]) for L in letters])


def _template_sig(k, levels):
    return k, tuple(sorted(((lv["letter"], bool(lv.get("split")), lv.get("ewm_type", "")) for lv in levels),
                           key=lambda x: letter_rank(x[0])))


def _new_template(tpls, sig):
    tpls.setdefault(sig, {"key": "", "pk": None, "pallets_per_beam": sig[0], "bays": 0, "_new": True,
                          "_heights": defaultdict(list), "_kg": defaultdict(list)})


def _distance(cells, sig):
    """Liczba różnic gniazda od szablonu: brakujące + nadmiarowe komórki + inne typy EWM."""
    k, levels = sig
    grid = _grid(k, [(L, s) for L, s, _ in levels])
    ltype = {L: t for L, _, t in levels}
    return len(grid ^ set(cells)) + sum(1 for c in grid & set(cells) if cells[c] != ltype[c[1]])


def detect(rows, master, templates=()):
    """rows: [{"zone", "rack_id", "n_bays"}]; master: [(kod, typ_ewm, wysokość_mm, udźwig_kg)];
    templates: istniejące szablony (atrybuty pk, name, pallets_per_beam, levels).

    → {"templates": [{key, pk, name, pallets_per_beam, beam_mm, levels, bays}],
       "rows": [{zone, rack_id, template, bay_numbers, overrides, codes}],
       "missing_rows": ["B0-99", …]}   (rzędy bez żadnego kodu w masterze)"""
    wanted = {(r["zone"], r["rack_id"]): r for r in rows}
    loc = defaultdict(lambda: defaultdict(dict))            # (zone, rack) → bay → komórka → typ
    stats = defaultdict(lambda: ([], []))                    # (zone, rack, bay, litera) → (wys., kg)
    for code, ewm_type, height, max_kg in master:
        p = parse_code(code)
        if not p or (p[0], p[1]) not in wanted:
            continue
        zone, aisle, bay, pos, letter, half = p
        loc[(zone, aisle)][bay][(pos, letter, half)] = ewm_type or ""
        hs, ws = stats[(zone, aisle, bay, letter)]
        if height:
            hs.append(height)
        if max_kg:
            ws.append(max_kg)

    # 1) szablony: istniejące + wzory regularnych gniazd
    tpls = {}                                                # sygnatura → propozycja szablonu
    for t in templates:
        tpls.setdefault(_template_sig(t.pallets_per_beam, t.levels),
                        {"key": f"pk:{t.pk}", "pk": t.pk, "name": t.name, "pallets_per_beam": t.pallets_per_beam,
                         "levels": t.levels, "bays": 0, "_new": False})
    bay_info = {}                                            # (zone, rack, bay) → (komórki, sygn.|None, obrys)
    for (zone, aisle), bays in loc.items():
        for bay, cells in bays.items():
            types = {L: Counter(t for c, t in cells.items() if c[1] == L).most_common(1)[0][0]
                     for L in {c[1] for c in cells}}
            sig, regular = _shape(cells, types)
            if regular:
                _new_template(tpls, sig)
            bay_info[(zone, aisle, bay)] = (cells, sig if regular else None, sig)

    # 2) gniazdo → szablon (regularne po sygnaturze, nieregularne: najbliższy)
    freq = Counter(sig for _, sig, _ in bay_info.values() if sig)
    choice = {}
    for key, (cells, sig, hull) in sorted(bay_info.items()):
        if sig is None:
            if not tpls:                      # brak jakiegokolwiek wzoru regularnego → obrys gniazda
                _new_template(tpls, hull)
            sig = min(tpls, key=lambda s: (_distance(cells, s), -freq[s], repr(s)))
        choice[key] = sig
        t = tpls[sig]
        t["bays"] += 1
        if t["_new"]:
            for L, _, _ in sig[1]:
                hs, ws = stats[(key[0], key[1], key[2], L)]
                t["_heights"][L] += hs
                t["_kg"][L] += ws

    new = sorted(((s, t) for s, t in tpls.items() if t["_new"]), key=lambda st: -st[1]["bays"])
    for i, ((k, levels), t) in enumerate(new):
        t["key"] = f"new:{i}"
        t["beam_mm"] = BEAM_MM.get(k, k * 900)
        t["levels"] = [{"letter": L, "split": s, "ewm_type": typ,
                        "height_mm": int(median(t["_heights"][L])) if t["_heights"][L] else DEFAULT_HEIGHT_MM,
                        "max_kg": int(median(t["_kg"][L])) if t["_kg"][L] else 0}
                       for L, s, typ in levels]
        types = "/".join(dict.fromkeys(typ for _, _, typ in levels if typ))
        t["name"] = (f"{k} pal. · " + " ".join(L + ("½" if s else "") for L, s, _ in levels)
                     + (f" · {types}" if types else ""))

    # 3) rzędy: szablon domyślny, numeracja, wyjątki
    out_rows, missing = [], []
    for (zone, aisle), row in sorted(wanted.items()):
        bays = loc.get((zone, aisle))
        if not bays:
            missing.append(f"{zone}-{aisle}")
            continue
        order = sorted(bays)
        default = Counter(choice[(zone, aisle, b)] for b in order).most_common(1)[0][0]
        overrides = []
        n, span = row["n_bays"], order[-1] - order[0] + 1
        if n >= span:                         # fizyczne gniazda bez adresów → skip całego gniazda
            numbers = list(range(order[0], order[0] + n))
            overrides += [{"bay": b, "position": 0, "letter": "", "half": 0, "action": "skip", "value": ""}
                          for b in numbers if b not in bays]
        else:                                 # przerwy w numeracji EWM → numeracja z przerwami
            numbers = order
        for b in order:
            sig = choice[(zone, aisle, b)]
            if sig != default:
                overrides.append({"bay": b, "position": 0, "letter": "", "half": 0,
                                  "action": "template", "value": "", "template": tpls[sig]["key"]})
            k, levels = sig
            grid = _grid(k, [(L, s) for L, s, _ in levels])
            ltype = {L: typ for L, _, typ in levels}
            cells = bays[b]
            for pos, L, h in sorted(grid - set(cells)):
                overrides.append({"bay": b, "position": pos, "letter": L, "half": h, "action": "skip", "value": ""})
            for (pos, L, h), typ in sorted(cells.items()):
                if (pos, L, h) not in grid:
                    overrides.append({"bay": b, "position": pos, "letter": L, "half": h,
                                      "action": "add", "value": typ})
                elif typ != ltype[L]:
                    overrides.append({"bay": b, "position": pos, "letter": L, "half": h,
                                      "action": "ewm_type", "value": typ})
        out_rows.append({"zone": zone, "rack_id": aisle, "template": tpls[default]["key"],
                         "bay_numbers": format_bay_numbers(numbers), "overrides": overrides,
                         "codes": sum(len(c) for c in bays.values())})

    used = {tpls[s]["key"] for s in choice.values()}
    templates_out = [{k: v for k, v in t.items() if not k.startswith("_")}
                     for t in tpls.values() if t["key"] in used]
    templates_out.sort(key=lambda t: (t["pk"] is None, -t["bays"]))
    return {"templates": templates_out, "rows": out_rows, "missing_rows": missing}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sh web/scripts/test.sh wh3d.tests.test_ewm_detect`
Expected: `Ran 8 tests … OK`

- [ ] **Step 5: Commit**

```bash
git add web/wh3d/ewm_detect.py web/wh3d/tests/test_ewm_detect.py web/wh3d/tests/ewm_sample.py web/wh3d/tests/data/ewm_b0_sample.csv
git commit -m "feat(wh3d): „Wykryj z EWM” — szablony, numeracja i wyjątki z kodów mastera (round-trip)"
```

---

### Task 4: Raport zgodności + warstwa ORM (`ewm_compliance.py`, `ewm_service.py`)

**Files:**
- Create: `web/wh3d/ewm_compliance.py`
- Create: `web/wh3d/ewm_service.py`
- Test: `web/wh3d/tests/test_ewm_service.py`

**Interfaces:**
- Consumes: `addressing.parse_code, expand_model`; `ewm_detect.detect`; modele z Task 2; `tests/ewm_sample.py` z Task 3
- Produces:
  - `compliance(rows, locations, duplicates, ewm_codes) -> {"aisles": [{zone, aisle, status, plan, ewm, matched, total, pct, plan_only, ewm_only, duplicates}], "summary": {plan, ewm, matched, pct, unparsed, ok, aisles}}`; `status ∈ {"ok", "diff", "no_template", "no_row"}`
  - `ewm_service.active_master() -> WarehouseLocationMasterBatch | None`
  - `ewm_service.master_rows(batch, zones) -> [(kod, typ, wys., kg)]`
  - `ewm_service.detect_for_model(wm, batch) -> dict` (wynik `detect`)
  - `ewm_service.apply_proposal(wm, proposal) -> (nowe_szablony:int, rzędy:int, wyjątki:int)`
  - `ewm_service.compliance_for_model(wm, batch) -> dict` (wynik `compliance`)

- [ ] **Step 1: Write the failing test** — `web/wh3d/tests/test_ewm_service.py`:

```python
"""Warstwa ORM części 1: zapis „Wykryj z EWM” + raport zgodności na prawdziwej próbce EWM."""
from django.test import SimpleTestCase, TestCase

from wh3d import ewm_service
from wh3d.ewm_compliance import compliance
from wh3d.models import (
    BayTemplate, LocationOverride, WarehouseLocationMaster, WarehouseLocationMasterBatch, WarehouseModel,
    WarehouseModelRack,
)
from wh3d.tests.ewm_sample import SAMPLE_N_BAYS, load_sample


def make_model_and_master(extra_codes=()):
    wm = WarehouseModel.objects.create(name="Logistyczna")
    for aisle, n in SAMPLE_N_BAYS.items():
        WarehouseModelRack.objects.create(model=wm, zone="B0", rack_id=aisle, n_bays=n, bay_width_cm=280)
    batch = WarehouseLocationMasterBatch.objects.create(name="EWM", is_active=True)
    pairs = load_sample() + [(c, "0010") for c in extra_codes]
    WarehouseLocationMaster.objects.bulk_create(
        [WarehouseLocationMaster(batch=batch, location_code=c, warehouse_type=t) for c, t in pairs])
    return wm, batch


class ServiceTests(TestCase):
    def test_detect_apply_and_compliance_is_100_percent(self):
        wm, batch = make_model_and_master(extra_codes=["B0-60-100A", "C9-01-100A"])
        created, rows, overrides = ewm_service.apply_proposal(wm, ewm_service.detect_for_model(wm, batch))
        self.assertEqual((created, rows), (18, 6))
        self.assertEqual(LocationOverride.objects.count(), overrides)
        rack = wm.racks.get(rack_id="08")
        self.assertEqual(rack.bay_numbers, "10-47,50")
        self.assertEqual(rack.template.name, "3 pal. · A X Y Z · 0052/0010")

        report = ewm_service.compliance_for_model(wm, batch)
        status = {a["aisle"]: a["status"] for a in report["aisles"]}
        self.assertEqual(status, {"07": "ok", "08": "ok", "34": "ok", "38": "ok", "48": "ok", "54": "ok",
                                  "60": "no_row"})                   # strefa C9 poza modelem — pominięta
        self.assertEqual(report["summary"]["ok"], 6)
        self.assertEqual(report["summary"]["matched"], 3327)

    def test_second_detect_reuses_templates_and_replaces_overrides(self):
        wm, batch = make_model_and_master()
        first = ewm_service.apply_proposal(wm, ewm_service.detect_for_model(wm, batch))
        again = ewm_service.detect_for_model(wm, batch)
        self.assertTrue(all(t["key"].startswith("pk:") for t in again["templates"]))
        second = ewm_service.apply_proposal(wm, again)
        self.assertEqual(second[0], 0)
        self.assertEqual(second[2], first[2])
        self.assertEqual(BayTemplate.objects.count(), 18)
        self.assertEqual(LocationOverride.objects.count(), first[2])

    def test_rack_without_template_and_missing_master(self):
        wm, batch = make_model_and_master()
        report = ewm_service.compliance_for_model(wm, batch)
        self.assertEqual({a["status"] for a in report["aisles"]}, {"no_template"})
        report = ewm_service.compliance_for_model(wm, None)
        self.assertEqual(report["summary"]["ewm"], 0)

    def test_master_rows_filters_by_zone_and_active_master(self):
        wm, batch = make_model_and_master(extra_codes=["C9-01-100A"])
        WarehouseLocationMasterBatch.objects.create(name="stary", is_active=False)
        self.assertEqual(ewm_service.active_master(), batch)
        codes = [r[0] for r in ewm_service.master_rows(batch, {"B0"})]
        self.assertNotIn("C9-01-100A", codes)
        self.assertEqual(len(codes), 3327)
        self.assertEqual(ewm_service.master_rows(batch, set()), [])


class CompliancePureTests(SimpleTestCase):
    def test_statuses_and_percent(self):
        rows = [{"zone": "B0", "rack_id": "01", "has_template": True},
                {"zone": "B0", "rack_id": "02", "has_template": True}]
        locs = [{"zone": "B0", "aisle": "01", "code": c} for c in ("B0-01-100A", "B0-01-101A")]
        locs += [{"zone": "B0", "aisle": "02", "code": "B0-02-100A"}]
        dups = {"B0-02-100A": ["B0-02", "B0-02"]}
        ewm = ["B0-01-100A", "B0-01-102A", "B0-02-100A", "B0-03-100A", "B0-XX"]
        rep = compliance(rows, locs, dups, ewm)
        a = {x["aisle"]: x for x in rep["aisles"]}
        self.assertEqual(a["01"]["status"], "diff")
        self.assertEqual(a["01"]["plan_only"], ["B0-01-101A"])
        self.assertEqual(a["01"]["ewm_only"], ["B0-01-102A"])
        self.assertEqual(a["01"]["pct"], 33.3)
        self.assertEqual(a["02"]["status"], "diff")                  # duplikat
        self.assertEqual(a["02"]["duplicates"], ["B0-02-100A"])
        self.assertEqual(a["03"]["status"], "no_row")
        self.assertEqual(rep["summary"]["unparsed"], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sh web/scripts/test.sh wh3d.tests.test_ewm_service`
Expected: FAIL — `ImportError: cannot import name 'ewm_service' from 'wh3d'`

- [ ] **Step 3: Write minimal implementation**

`web/wh3d/ewm_compliance.py`:

```python
"""Raport zgodności modelu z EWM: kody z planu (expand_model) vs kody z mastera, per przejście.

Czysty Python (bez ORM). Status przejścia:
  ok          — każdy kod planu jest w EWM i odwrotnie, bez duplikatów
  diff        — rozbieżności (tylko plan / tylko EWM / duplikaty)
  no_template — rząd jest na planie, ale bez szablonu (nie generuje miejsc)
  no_row      — przejście jest w EWM, ale nie ma go na planie
"""
from collections import defaultdict

from .addressing import parse_code


def compliance(rows, locations, duplicates, ewm_codes):
    """rows: [{"zone", "rack_id", "has_template"}]; locations/duplicates: wynik expand_model;
    ewm_codes: kody z mastera (brane tylko ze stref obecnych w modelu)."""
    zones = {r["zone"] for r in rows}
    plan, ewm, unparsed = defaultdict(set), defaultdict(set), 0
    for loc in locations:
        plan[(loc["zone"], loc["aisle"])].add(loc["code"])
    for code in ewm_codes:
        p = parse_code(code)
        if p is None:
            unparsed += code.split("-", 1)[0] in zones
            continue
        if p[0] in zones:
            ewm[(p[0], p[1])].add(code.strip().upper())
    has_tpl = {(r["zone"], r["rack_id"]): r["has_template"] for r in rows}
    aisles = []
    for key in sorted(set(plan) | set(ewm) | set(has_tpl)):
        P, E = plan.get(key, set()), ewm.get(key, set())
        dup = sorted(c for c in P if c in duplicates)
        if key not in has_tpl:
            status = "no_row"
        elif not has_tpl[key]:
            status = "no_template"
        elif P == E and not dup:
            status = "ok"
        else:
            status = "diff"
        total = len(P | E)
        aisles.append({"zone": key[0], "aisle": key[1], "status": status,
                       "plan": len(P), "ewm": len(E), "matched": len(P & E), "total": total,
                       "pct": round(100 * len(P & E) / total, 1) if total else 100.0,
                       "plan_only": sorted(P - E), "ewm_only": sorted(E - P), "duplicates": dup})
    matched, total = sum(a["matched"] for a in aisles), sum(a["total"] for a in aisles)
    return {"aisles": aisles,
            "summary": {"plan": sum(a["plan"] for a in aisles), "ewm": sum(a["ewm"] for a in aisles),
                        "matched": matched, "pct": round(100 * matched / total, 1) if total else 100.0,
                        "unparsed": unparsed, "ok": sum(a["status"] == "ok" for a in aisles),
                        "aisles": len(aisles)}}
```

`web/wh3d/ewm_service.py`:

```python
"""Warstwa ORM nad czystymi modułami adresowania: master EWM, plan modelu, zapis „Wykryj z EWM”."""
from django.db import transaction
from django.db.models import Prefetch, Q

from .addressing import expand_model
from .ewm_compliance import compliance
from .ewm_detect import detect
from .models import (
    BayTemplate, LocationOverride, WarehouseLocationMaster, WarehouseLocationMasterBatch, WarehouseModelRack,
)


def active_master():
    """Aktywny (najnowszy) import mastera lokalizacji albo None."""
    return WarehouseLocationMasterBatch.objects.filter(is_active=True).order_by("-uploaded_at").first()


def master_rows(batch, zones):
    """[(kod, typ EWM, wysokość mm, udźwig kg)] z mastera — tylko kody stref modelu."""
    if batch is None or not zones:
        return []
    prefix = Q()
    for zone in zones:
        prefix |= Q(location_code__startswith=f"{zone}-")
    return list(WarehouseLocationMaster.objects.filter(prefix, batch=batch)
                .values_list("location_code", "warehouse_type", "height_mm", "max_weight_kg"))


def detect_for_model(wm, batch):
    """Propozycja „Wykryj z EWM” dla rzędów modelu (nic nie zapisuje)."""
    racks = list(wm.racks.all())
    rows = [{"zone": r.zone, "rack_id": r.rack_id, "n_bays": r.n_bays} for r in racks]
    return detect(rows, master_rows(batch, {r.zone for r in racks}), list(BayTemplate.objects.all()))


@transaction.atomic
def apply_proposal(wm, proposal):
    """Zapis propozycji: nowe szablony, szablon domyślny + numeracja rzędów, wyjątki (ZASTĘPUJĄ
    dotychczasowe wyjątki wykrytych rzędów). → (nowe szablony, rzędy, wyjątki)."""
    by_key, created = {}, 0
    for t in proposal["templates"]:
        if t["pk"]:
            by_key[t["key"]] = BayTemplate.objects.get(pk=t["pk"])
        else:
            by_key[t["key"]] = BayTemplate.objects.create(
                name=t["name"], beam_mm=t["beam_mm"], pallets_per_beam=t["pallets_per_beam"], levels=t["levels"])
            created += 1
    racks = {(r.zone, r.rack_id): r for r in wm.racks.select_for_update()}
    touched, new_overrides = [], []
    for row in proposal["rows"]:
        rack = racks[(row["zone"], row["rack_id"])]
        rack.template = by_key[row["template"]]
        rack.bay_numbers = row["bay_numbers"]
        touched.append(rack)
        for o in row["overrides"]:
            fields = {k: v for k, v in o.items() if k != "template"}
            new_overrides.append(LocationOverride(rack=rack, template=by_key.get(o.get("template")), **fields))
    if touched:
        WarehouseModelRack.objects.bulk_update(touched, ["template", "bay_numbers"])
    LocationOverride.objects.filter(rack__in=touched).delete()
    LocationOverride.objects.bulk_create(new_overrides)
    return created, len(touched), len(new_overrides)


def plan_for_model(wm):
    """Rozwinięty plan modelu → (rzędy, miejsca, duplikaty)."""
    racks = list(wm.racks.select_related("template").prefetch_related(
        Prefetch("overrides", queryset=LocationOverride.objects.select_related("template"))))
    locations, duplicates = expand_model([(r, r.template, list(r.overrides.all())) for r in racks])
    return racks, locations, duplicates


def compliance_for_model(wm, batch):
    """Raport zgodności planu modelu z aktywnym masterem (batch=None → brak kodów EWM)."""
    racks, locations, duplicates = plan_for_model(wm)
    rows = [{"zone": r.zone, "rack_id": r.rack_id, "has_template": r.template_id is not None} for r in racks]
    codes = [row[0] for row in master_rows(batch, {r.zone for r in racks})]
    return compliance(rows, locations, duplicates, codes)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sh web/scripts/test.sh wh3d.tests.test_ewm_service wh3d.tests.test_ewm_detect ui.tests.test_wh3d_boundary`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add web/wh3d/ewm_compliance.py web/wh3d/ewm_service.py web/wh3d/tests/test_ewm_service.py
git commit -m "feat(wh3d): raport zgodności z EWM + zapis propozycji „Wykryj z EWM” (warstwa ORM)"
```

**PR C** (po Task 3 + Task 4): gałąź `claude/edytor-wykryj-ewm`, tytuł „feat(wh3d): Wykryj z EWM + raport zgodności — logika (edytor układu cz. 1)”.

---

### Task 5: Ekran „Szablony gniazd” + 3 kolumny w edycji współrzędnych

**Files:**
- Create: `web/wh3d/views/bay_templates.py`
- Create: `web/wh3d/templates/ui/warehouse_model/bay_templates.html`
- Create: `web/wh3d/templates/ui/warehouse_model/bay_template_form.html`
- Modify: `web/wh3d/views/__init__.py` (dopisz `from .bay_templates import *  # noqa: F401,F403`)
- Modify: `web/wh3d/urls.py` (4 ścieżki w sekcji „Warehouse model builder”, PRZED `magazyn/model/<int:pk>/…`)
- Modify: `web/wh3d/views/warehouse_model.py` (`warehouse_model_coords`: zapis 3 pól + lista szablonów w kontekście)
- Modify: `web/wh3d/templates/ui/warehouse_model/coords.html` (3 kolumny, `colspan="13"`)
- Modify: `web/wh3d/templates/ui/warehouse_model/list.html` (przycisk „Szablony gniazd”)
- Test: `web/wh3d/tests/test_bay_template_views.py`

**Interfaces:**
- Consumes: `BayTemplate`, `WarehouseModelRack.template/bay_numbers/reverse` (Task 2), `parse_bay_numbers` (Task 1)
- Produces: URL-e `ui:bay_template_list`, `ui:bay_template_new`, `ui:bay_template_edit` (pk), `ui:bay_template_delete` (pk, POST)

- [ ] **Step 1: Write the failing test** — `web/wh3d/tests/test_bay_template_views.py`:

```python
"""Ekran szablonów gniazd (CRUD, role) + kolumny szablon/numeracja/kierunek w edycji współrzędnych."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from wh3d.models import BayTemplate, LocationOverride, WarehouseModel, WarehouseModelRack


def level_post(rows):
    data = {}
    for i, (letter, height, typ, split, kg) in enumerate(rows):
        data.update({f"lvl-{i}-letter": letter, f"lvl-{i}-height": height, f"lvl-{i}-type": typ,
                     f"lvl-{i}-split": split, f"lvl-{i}-kg": kg})
    return data


class BayTemplateViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(username="adm", password="x")
        self.viewer = User.objects.create_user(username="podglad", password="x")
        self.viewer.groups.add(Group.objects.get_or_create(name="Podgląd")[0])

    def test_list_for_viewer_without_edit_buttons(self):
        BayTemplate.objects.create(name="3 pal. · A X", pallets_per_beam=3,
                                   levels=[{"letter": "A", "height_mm": 1500, "ewm_type": "0052", "split": False}])
        self.client.force_login(self.viewer)
        r = self.client.get(reverse("ui:bay_template_list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "3 pal. · A X")
        self.assertNotContains(r, reverse("ui:bay_template_new"))
        self.assertEqual(self.client.get(reverse("ui:bay_template_new")).status_code, 403)

    def test_create_with_split_level(self):
        self.client.force_login(self.admin)
        post = {"name": "Kompletacja", "pallets_per_beam": "3", "beam_mm": "2700", "depth_mm": "1100", "notes": ""}
        post.update(level_post([("b", "400", "0052", "0", "300"), ("C", "400", "0052", "1", "300"),
                                ("", "", "", "0", "")]))
        r = self.client.post(reverse("ui:bay_template_new"), post)
        self.assertRedirects(r, reverse("ui:bay_template_list"))
        t = BayTemplate.objects.get(name="Kompletacja")
        self.assertEqual(t.level_label, "B C½")
        self.assertEqual(t.levels[1], {"letter": "C", "height_mm": 400, "ewm_type": "0052", "split": True,
                                       "max_kg": 300})

    def test_invalid_levels_show_error_and_save_nothing(self):
        self.client.force_login(self.admin)
        post = {"name": "Zły", "pallets_per_beam": "3", "beam_mm": "2700", "depth_mm": "1100"}
        post.update(level_post([("A", "400", "", "0", ""), ("A", "400", "", "0", "")]))
        r = self.client.post(reverse("ui:bay_template_new"), post)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "unikalne")
        self.assertFalse(BayTemplate.objects.filter(name="Zły").exists())

    def test_delete_protected_template_keeps_it(self):
        t = BayTemplate.objects.create(name="Przejazd", pallets_per_beam=4,
                                       levels=[{"letter": "Y", "height_mm": 1800, "ewm_type": "", "split": False}])
        wm = WarehouseModel.objects.create(name="M")
        rack = WarehouseModelRack.objects.create(model=wm, zone="B0", rack_id="07")
        LocationOverride.objects.create(rack=rack, bay=29, action="template", template=t)
        self.client.force_login(self.admin)
        r = self.client.post(reverse("ui:bay_template_delete", args=[t.pk]), follow=True)
        self.assertContains(r, "jest użyty")
        self.assertTrue(BayTemplate.objects.filter(pk=t.pk).exists())


class CoordsRuleColumnsTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username="adm", password="x")
        self.client.force_login(self.admin)
        self.wm = WarehouseModel.objects.create(name="M")
        self.rack = WarehouseModelRack.objects.create(model=self.wm, zone="B0", rack_id="07", n_bays=39)
        self.tpl = BayTemplate.objects.create(name="T", pallets_per_beam=3,
                                              levels=[{"letter": "A", "height_mm": 1500, "ewm_type": "", "split": False}])

    def post(self, **extra):
        p = f"rack_{self.rack.pk}_"
        data = {p + "x_m": "1", p + "y_m": "2", p + "angle_deg": "0", p + "bay_width_cm": "280",
                p + "depth_cm": "103", p + "level_height_cm": "200"}
        data.update({p + k: v for k, v in extra.items()})
        return self.client.post(reverse("ui:warehouse_model_coords", args=[self.wm.pk]), data, follow=True)

    def test_get_shows_template_select(self):
        r = self.client.get(reverse("ui:warehouse_model_coords", args=[self.wm.pk]))
        self.assertContains(r, f'name="rack_{self.rack.pk}_template"')
        self.assertContains(r, f'name="rack_{self.rack.pk}_bay_numbers"')

    def test_saves_template_numbering_and_reverse(self):
        self.post(template=str(self.tpl.pk), bay_numbers="10-47,50", reverse="1")
        self.rack.refresh_from_db()
        self.assertEqual((self.rack.template, self.rack.bay_numbers, self.rack.reverse), (self.tpl, "10-47,50", True))

    def test_invalid_numbering_keeps_old_value_and_warns(self):
        self.rack.bay_numbers = "10-48"
        self.rack.save()
        r = self.post(template="", bay_numbers="48-10")
        self.rack.refresh_from_db()
        self.assertEqual(self.rack.bay_numbers, "10-48")
        self.assertIsNone(self.rack.template)
        self.assertContains(r, "Zakres malejący")

    def test_old_form_without_new_fields_keeps_rule(self):
        self.rack.template, self.rack.bay_numbers, self.rack.reverse = self.tpl, "10-48", True
        self.rack.save()
        self.post()
        self.rack.refresh_from_db()
        self.assertEqual((self.rack.template, self.rack.bay_numbers, self.rack.reverse), (self.tpl, "10-48", True))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sh web/scripts/test.sh wh3d.tests.test_bay_template_views`
Expected: FAIL — `NoReverseMatch: Reverse for 'bay_template_list' not found`

- [ ] **Step 3: Write minimal implementation**

`web/wh3d/views/bay_templates.py`:

```python
"""Szablony gniazd (słup regału: palety na belce, poziomy od podłogi) — lista, formularz, usuwanie."""
from django.core.exceptions import ValidationError
from django.db.models import Count, ProtectedError

from ui.views.core import _md_role, _planner, get_object_or_404, messages, redirect, render, require_POST
from wh3d.models import BayTemplate, WarehouseRackType

EMPTY_LEVEL_ROWS = 3   # puste wiersze na nowe poziomy w formularzu


def _int(raw):
    try:
        return int(float(str(raw).replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def _levels_from_post(post):
    """Wiersze tabeli poziomów (lvl-<i>-letter/height/type/split/kg) → lista poziomów; pusta litera = pomiń."""
    levels, i = [], 0
    while f"lvl-{i}-letter" in post:
        letter = post.get(f"lvl-{i}-letter", "").strip().upper()
        if letter:
            levels.append({"letter": letter, "height_mm": _int(post.get(f"lvl-{i}-height")),
                           "ewm_type": post.get(f"lvl-{i}-type", "").strip()[:20],
                           "split": post.get(f"lvl-{i}-split") == "1",
                           "max_kg": _int(post.get(f"lvl-{i}-kg"))})
        i += 1
    return levels


@_planner
def bay_template_list(request):
    templates = BayTemplate.objects.annotate(n_racks=Count("racks", distinct=True),
                                             n_bays=Count("bay_overrides", distinct=True))
    return render(request, "ui/warehouse_model/bay_templates.html", {"templates": templates})


@_md_role
def bay_template_form(request, pk=None):
    obj = get_object_or_404(BayTemplate, pk=pk) if pk else BayTemplate()
    if request.method == "POST":
        obj.name = request.POST.get("name", "").strip()[:100]
        obj.pallets_per_beam = max(0, _int(request.POST.get("pallets_per_beam")))
        obj.beam_mm = _int(request.POST.get("beam_mm"))
        obj.depth_mm = _int(request.POST.get("depth_mm"))
        obj.notes = request.POST.get("notes", "").strip()[:200]
        obj.levels = _levels_from_post(request.POST)
        try:
            obj.full_clean()
        except ValidationError as exc:
            for msg in exc.messages:
                messages.error(request, msg)
        else:
            obj.save()
            messages.success(request, f"Szablon „{obj.name}” zapisany.")
            return redirect("ui:bay_template_list")
    return render(request, "ui/warehouse_model/bay_template_form.html", {
        "obj": obj,
        "rows": list(obj.levels) + [{}] * EMPTY_LEVEL_ROWS,
        "ewm_types": WarehouseRackType.objects.values_list("code", flat=True),
    })


@require_POST
@_md_role
def bay_template_delete(request, pk):
    obj = get_object_or_404(BayTemplate, pk=pk)
    try:
        obj.delete()
    except ProtectedError:
        messages.error(request, f"Szablon „{obj.name}” jest użyty jako wyjątek gniazda — "
                                "najpierw zmień te gniazda (albo ponów „Wykryj z EWM”).")
    else:
        messages.success(request, f"Szablon „{obj.name}” usunięty.")
    return redirect("ui:bay_template_list")


__all__ = ["bay_template_list", "bay_template_form", "bay_template_delete"]
```

`web/wh3d/urls.py` — w sekcji `# ── Warehouse model builder ──`, zaraz po `magazyn/model/z-mapy/`:

```python
    path("magazyn/model/szablony/", wh3d_views.bay_template_list, name="bay_template_list"),
    path("magazyn/model/szablony/nowy/", wh3d_views.bay_template_form, name="bay_template_new"),
    path("magazyn/model/szablony/<int:pk>/", wh3d_views.bay_template_form, name="bay_template_edit"),
    path("magazyn/model/szablony/<int:pk>/usun/", wh3d_views.bay_template_delete, name="bay_template_delete"),
```

`web/wh3d/views/__init__.py` — dopisz na końcu:

```python
from .bay_templates import *  # noqa: F401,F403
```

`web/wh3d/templates/ui/warehouse_model/bay_templates.html`:

```html
{% extends "ui/base.html" %}
{% block title %}Szablony gniazd — {{ app_name }}{% endblock %}
{% block nav_warehouse_map %}topnav__link--active{% endblock %}

{% block content %}
<nav class="breadcrumb">
  <a href="{% url 'ui:warehouse_model_list' %}">Modele magazynu</a>
  <span class="breadcrumb__sep">›</span>
  <span>Szablony gniazd</span>
</nav>

<div class="page-header">
  <div>
    <div class="page-header__title">Szablony gniazd</div>
    <div class="page-header__sub">Słup regału: ile palet stoi na belce i jakie poziomy są od podłogi w górę — z szablonów powstają adresy miejsc</div>
  </div>
  {% if can_write_products %}
  <div class="page-header__actions">
    <a href="{% url 'ui:bay_template_new' %}" class="btn btn-primary">
      <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
      Nowy szablon
    </a>
  </div>
  {% endif %}
</div>

<div class="card">
  {% if templates %}
  <div class="table-wrap">
    <table class="table">
      <thead>
        <tr>
          <th>Nazwa</th><th>Palet na belce</th><th>Belka [mm]</th><th>Poziomy od dołu</th>
          <th>Typy EWM</th><th>Użycie</th><th style="text-align:right">Akcje</th>
        </tr>
      </thead>
      <tbody>
        {% for t in templates %}
        <tr>
          <td>
            <span class="font-semibold">{{ t.name }}</span>
            {% if t.notes %}<div class="text-sm text-muted">{{ t.notes }}</div>{% endif %}
          </td>
          <td>{{ t.pallets_per_beam }}</td>
          <td>{{ t.beam_mm }}</td>
          <td><code>{{ t.level_label }}</code></td>
          <td class="text-sm">{{ t.ewm_types_label|default:"—" }}</td>
          <td class="text-sm text-muted">{{ t.n_racks }} rzędów (domyślny) · {{ t.n_bays }} gniazd (wyjątek)</td>
          <td style="text-align:right;white-space:nowrap">
            {% if can_write_products %}
            <a href="{% url 'ui:bay_template_edit' t.pk %}" class="btn btn-secondary btn-sm">Edytuj</a>
            <form method="post" action="{% url 'ui:bay_template_delete' t.pk %}" style="display:inline"
                  onsubmit="return confirm('Usunąć szablon „{{ t.name|escapejs }}”?')">
              {% csrf_token %}
              <button type="submit" class="btn btn-ghost btn-sm" style="color:var(--red)">Usuń</button>
            </form>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="card-body text-muted">
    Brak szablonów. Utwórz pierwszy albo otwórz model magazynu i użyj „Wykryj z EWM” — szablony powstaną z kodów lokalizacji.
  </div>
  {% endif %}
</div>
{% endblock %}
```

`web/wh3d/templates/ui/warehouse_model/bay_template_form.html`:

```html
{% extends "ui/base.html" %}
{% block title %}{% if obj.pk %}Edytuj szablon{% else %}Nowy szablon gniazda{% endif %} — {{ app_name }}{% endblock %}
{% block nav_warehouse_map %}topnav__link--active{% endblock %}

{% block extra_head %}
<style>
  .levels-table input, .levels-table select { width: 100%; padding: 5px 8px; font-size: 12px; }
  .levels-table td { vertical-align: middle; }
  .levels-table .col-letter { width: 70px; }
</style>
{% endblock %}

{% block content %}
<nav class="breadcrumb">
  <a href="{% url 'ui:warehouse_model_list' %}">Modele magazynu</a>
  <span class="breadcrumb__sep">›</span>
  <a href="{% url 'ui:bay_template_list' %}">Szablony gniazd</a>
  <span class="breadcrumb__sep">›</span>
  <span>{% if obj.pk %}{{ obj.name }}{% else %}Nowy szablon{% endif %}</span>
</nav>

<div class="page-header">
  <div>
    <div class="page-header__title">{% if obj.pk %}Edytuj szablon „{{ obj.name }}”{% else %}Nowy szablon gniazda{% endif %}</div>
    <div class="page-header__sub">Poziomy wpisuj od podłogi w górę. Pusta litera = wiersz pominięty.</div>
  </div>
</div>

<form method="post" class="card">
  {% csrf_token %}
  <div class="card-body" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px">
    <label class="form-group" style="grid-column:span 2">Nazwa
      <input type="text" name="name" value="{{ obj.name }}" class="form-control" required maxlength="100">
    </label>
    <label class="form-group">Palet na belce
      <input type="number" name="pallets_per_beam" value="{{ obj.pallets_per_beam }}" class="form-control" min="1" step="1">
    </label>
    <label class="form-group">Szerokość belki [mm]
      <input type="number" name="beam_mm" value="{{ obj.beam_mm }}" class="form-control" min="1" step="1">
    </label>
    <label class="form-group">Głębokość [mm]
      <input type="number" name="depth_mm" value="{{ obj.depth_mm }}" class="form-control" min="1" step="1">
    </label>
    <label class="form-group" style="grid-column:1/-1">Uwagi
      <input type="text" name="notes" value="{{ obj.notes }}" class="form-control" maxlength="200">
    </label>
  </div>

  <div class="table-wrap">
    <table class="table levels-table">
      <thead>
        <tr><th class="col-letter">Litera</th><th>Wysokość [mm]</th><th>Typ EWM</th><th>Połówki (-1/-2)</th><th>Udźwig [kg]</th></tr>
      </thead>
      <tbody>
        {% for lv in rows %}
        <tr>
          <td class="col-letter"><input type="text" name="lvl-{{ forloop.counter0 }}-letter" value="{{ lv.letter|default:'' }}" maxlength="1" style="text-transform:uppercase"></td>
          <td><input type="number" name="lvl-{{ forloop.counter0 }}-height" value="{{ lv.height_mm|default:'' }}" min="1" step="1"></td>
          <td><input type="text" name="lvl-{{ forloop.counter0 }}-type" value="{{ lv.ewm_type|default:'' }}" list="ewm-types" maxlength="20"></td>
          <td>
            <select name="lvl-{{ forloop.counter0 }}-split">
              <option value="0">nie</option>
              <option value="1"{% if lv.split %} selected{% endif %}>tak — miejsce dzielone na dwie połówki</option>
            </select>
          </td>
          <td><input type="number" name="lvl-{{ forloop.counter0 }}-kg" value="{{ lv.max_kg|default:'' }}" min="0" step="1"></td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  <datalist id="ewm-types">{% for code in ewm_types %}<option value="{{ code }}">{% endfor %}</datalist>

  <div class="card-footer" style="display:flex;gap:10px">
    <button type="submit" class="btn btn-primary">Zapisz szablon</button>
    <a href="{% url 'ui:bay_template_list' %}" class="btn btn-ghost">Anuluj</a>
    <span class="text-sm text-muted" style="margin-left:auto">Potrzebujesz więcej poziomów? Zapisz — pojawią się kolejne puste wiersze.</span>
  </div>
</form>
{% endblock %}
```

`web/wh3d/views/warehouse_model.py` — w `warehouse_model_coords`:
1. dopisz import na górze pliku: `from wh3d.addressing import parse_bay_numbers` i `from wh3d.models import BayTemplate`;
2. zastąp całe ciało funkcji:

```python
@_md_role
def warehouse_model_coords(request, pk):
    from django.db import transaction
    wm = get_object_or_404(WarehouseModel, pk=pk)
    racks = wm.racks.order_by("zone", "rack_id")
    templates = list(BayTemplate.objects.all())
    if request.method == "POST":
        tpl_ids = {t.pk for t in templates}
        to_update, bad_numbers = [], []
        for rack in racks:
            prefix = f"rack_{rack.pk}_"
            try:
                rack.x_m = float(request.POST.get(prefix + "x_m") or 0)
                rack.y_m = float(request.POST.get(prefix + "y_m") or 0)
                rack.angle_deg = float(request.POST.get(prefix + "angle_deg") or 0)
                # max(1, ...) — jawne "0"/wartość ujemna daje zdegenerowaną geometrię (NaN/znika
                # w renderze three.js). `or default` chroni tylko puste. Jak w warehouse_rack_generator.
                rack.bay_width_cm = max(1, int(request.POST.get(prefix + "bay_width_cm") or 100))
                rack.depth_cm = max(1, int(request.POST.get(prefix + "depth_cm") or 80))
                rack.level_height_cm = max(1, int(request.POST.get(prefix + "level_height_cm") or 200))
            except (ValueError, TypeError):
                continue
            # Reguła adresu (edytor układu cz. 1) — tylko gdy formularz ją przysłał (stare
            # formularze/skrypty bez tych pól nie kasują szablonu ani numeracji).
            if prefix + "bay_numbers" in request.POST:
                tpl = request.POST.get(prefix + "template") or ""
                rack.template_id = int(tpl) if tpl.isdigit() and int(tpl) in tpl_ids else None
                rack.reverse = bool(request.POST.get(prefix + "reverse"))
                numbers = request.POST.get(prefix + "bay_numbers", "").strip()
                try:
                    parse_bay_numbers(numbers)
                    rack.bay_numbers = numbers
                except ValueError as exc:
                    bad_numbers.append(f"{rack}: {exc}")
            to_update.append(rack)
        if to_update:
            with transaction.atomic():
                WarehouseModelRack.objects.bulk_update(
                    to_update,
                    ["x_m", "y_m", "angle_deg", "bay_width_cm", "depth_cm", "level_height_cm",
                     "template", "bay_numbers", "reverse"],
                )
        messages.success(request, "Współrzędne zapisane.")
        for msg in bad_numbers[:10]:
            messages.warning(request, f"Numeracja gniazd nie zmieniona — {msg}")
        return redirect("ui:warehouse_model_view", pk=wm.pk)
    return render(request, "ui/warehouse_model/coords.html", {"wm": wm, "racks": racks, "templates": templates})
```

`web/wh3d/templates/ui/warehouse_model/coords.html`:
- w `<thead>` po `<th>Wys.&nbsp;poziomu&nbsp;[cm]</th>` dodaj `<th>Szablon&nbsp;gniazda</th><th>Numeracja&nbsp;gniazd</th><th>Od&nbsp;końca</th>`;
- `colspan="10"` → `colspan="13"`;
- w wierszu regału, po `<td>` z `level_height_cm`, dodaj:

```html
            <td>
              <select name="rack_{{ rack.pk }}_template" style="min-width:170px;padding:5px 8px;font-size:12px">
                <option value="">— brak —</option>
                {% for t in templates %}
                <option value="{{ t.pk }}"{% if t.pk == rack.template_id %} selected{% endif %}>{{ t.name }}</option>
                {% endfor %}
              </select>
            </td>
            <td>
              <input type="text" name="rack_{{ rack.pk }}_bay_numbers" value="{{ rack.bay_numbers }}"
                     placeholder="1-{{ rack.n_bays }}" style="width:110px;padding:5px 8px;font-size:12px"
                     title="Zakresy numerów gniazd, np. 10-47,50">
            </td>
            <td style="text-align:center">
              <input type="checkbox" name="rack_{{ rack.pk }}_reverse" value="1"{% if rack.reverse %} checked{% endif %}
                     title="Numeracja gniazd od końca rzędu">
            </td>
```

- w karcie „Wskazówki” dopisz kafelek:

```html
      <div>
        <div class="font-semibold" style="margin-bottom:4px">Szablon i numeracja gniazd</div>
        <div class="text-muted">Szablon = domyślny słup regału rzędu. Numeracja: zakresy numerów kolejnych
          gniazd, np. <code>10-47,50</code> (pusta = 1…liczba gniazd). „Od końca” odwraca kierunek.</div>
      </div>
```

`web/wh3d/templates/ui/warehouse_model/list.html` — w `page-header__actions`, przed linkiem „Warianty projektu”:

```html
    <a href="{% url 'ui:bay_template_list' %}" class="btn btn-secondary"
       title="Słupy regałów (palety na belce, poziomy) — z nich powstają adresy miejsc">
      Szablony gniazd
    </a>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sh web/scripts/test.sh wh3d.tests.test_bay_template_views wh3d.tests.test_warehouse_builder wh3d.tests.test_warehouse_model_view wh3d.tests.test_warehouse_model_paste ui.tests.test_views_package ui.tests.test_wh3d_boundary`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add web/wh3d/views/bay_templates.py web/wh3d/views/__init__.py web/wh3d/urls.py web/wh3d/views/warehouse_model.py \
  web/wh3d/templates/ui/warehouse_model/bay_templates.html web/wh3d/templates/ui/warehouse_model/bay_template_form.html \
  web/wh3d/templates/ui/warehouse_model/coords.html web/wh3d/templates/ui/warehouse_model/list.html \
  web/wh3d/tests/test_bay_template_views.py
git diff --cached --stat
git commit -m "feat(wh3d): ekran szablonów gniazd + szablon/numeracja/kierunek w edycji współrzędnych"
```

**PR D**: gałąź `claude/edytor-szablony-ui`, tytuł „feat(wh3d): szablony gniazd — ekran + reguła adresu w edycji współrzędnych (edytor układu cz. 1)”.

---

### Task 6: Ekrany „Wykryj z EWM” i „Zgodność z EWM” (+ XLSX)

**Files:**
- Create: `web/wh3d/views/warehouse_model_ewm.py`
- Create: `web/wh3d/templates/ui/warehouse_model/ewm_detect.html`
- Create: `web/wh3d/templates/ui/warehouse_model/ewm_compliance.html`
- Modify: `web/wh3d/views/__init__.py` (`from .warehouse_model_ewm import *  # noqa: F401,F403`)
- Modify: `web/wh3d/urls.py` (3 ścieżki pod `magazyn/model/<int:pk>/…`)
- Modify: `web/wh3d/templates/ui/warehouse_model/view.html` (2 przyciski w `page-header__actions`, przed „Edytuj współrzędne”)
- Test: `web/wh3d/tests/test_ewm_views.py`

**Interfaces:**
- Consumes: `ewm_service.active_master, detect_for_model, apply_proposal, compliance_for_model` (Task 4); `ui.views.core.xlsx._make_xlsx_response, _finalize_xlsx`
- Produces: URL-e `ui:warehouse_model_detect` (pk, GET), `ui:warehouse_model_detect_save` (pk, POST), `ui:warehouse_model_compliance` (pk, GET; `?format=xlsx` → plik)

- [ ] **Step 1: Write the failing test** — `web/wh3d/tests/test_ewm_views.py`:

```python
"""Ekrany „Wykryj z EWM” (podgląd → zapis) i „Zgodność z EWM” (+ XLSX), z rolami."""
import io

import openpyxl
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from wh3d.models import BayTemplate, WarehouseLocationMasterBatch
from wh3d.tests.test_ewm_service import make_model_and_master


class EwmViewsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(username="adm", password="x")
        self.viewer = User.objects.create_user(username="podglad", password="x")
        self.viewer.groups.add(Group.objects.get_or_create(name="Podgląd")[0])
        self.wm, self.batch = make_model_and_master(extra_codes=["B0-60-100A"])

    def test_detect_preview_lists_templates_and_rows_without_saving(self):
        self.client.force_login(self.viewer)
        r = self.client.get(reverse("ui:warehouse_model_detect", args=[self.wm.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "3 pal. · B C½ D½ X Y Z · 0052/0010")
        self.assertContains(r, "10-47,50")
        self.assertNotContains(r, reverse("ui:warehouse_model_detect_save", args=[self.wm.pk]))  # brak roli MD
        self.assertEqual(BayTemplate.objects.count(), 0)

    def test_viewer_cannot_save(self):
        self.client.force_login(self.viewer)
        r = self.client.post(reverse("ui:warehouse_model_detect_save", args=[self.wm.pk]))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(BayTemplate.objects.count(), 0)

    def test_save_then_compliance_shows_100_percent(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("ui:warehouse_model_detect_save", args=[self.wm.pk]), follow=True)
        self.assertRedirects(r, reverse("ui:warehouse_model_compliance", args=[self.wm.pk]))
        self.assertEqual(BayTemplate.objects.count(), 18)
        self.assertContains(r, "Zgodne")
        self.assertContains(r, "Brak rzędu na planie")              # przejście 60 tylko w EWM
        self.assertContains(r, "6 / 7")                              # zgodne przejścia / wszystkie

    def test_compliance_xlsx(self):
        self.client.force_login(self.viewer)
        r = self.client.get(reverse("ui:warehouse_model_compliance", args=[self.wm.pk]) + "?format=xlsx")
        self.assertEqual(r["Content-Type"],
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Zgodność", "Rozbieżności"])
        self.assertEqual(wb["Zgodność"]["A1"].value, "Strefa")

    def test_no_active_master_disables_detect(self):
        WarehouseLocationMasterBatch.objects.update(is_active=False)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("ui:warehouse_model_detect", args=[self.wm.pk]))
        self.assertContains(r, "Brak aktywnego mastera lokalizacji")
        r = self.client.post(reverse("ui:warehouse_model_detect_save", args=[self.wm.pk]), follow=True)
        self.assertContains(r, "Brak aktywnego mastera lokalizacji")
        self.assertEqual(BayTemplate.objects.count(), 0)

    def test_model_view_links(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("ui:warehouse_model_view", args=[self.wm.pk]))
        self.assertContains(r, reverse("ui:warehouse_model_detect", args=[self.wm.pk]))
        self.assertContains(r, reverse("ui:warehouse_model_compliance", args=[self.wm.pk]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sh web/scripts/test.sh wh3d.tests.test_ewm_views`
Expected: FAIL — `NoReverseMatch: Reverse for 'warehouse_model_detect' not found`

- [ ] **Step 3: Write minimal implementation**

`web/wh3d/views/warehouse_model_ewm.py`:

```python
"""„Wykryj z EWM” (podgląd propozycji → zapis) i raport zgodności modelu z EWM (+ XLSX)."""
from ui.views.core import (
    _md_role, _planner, get_object_or_404, messages, redirect, render, require_POST, WarehouseModel,
)
from ui.views.core.xlsx import _finalize_xlsx, _make_xlsx_response
from wh3d.ewm_service import active_master, apply_proposal, compliance_for_model, detect_for_model

NO_MASTER = ("Brak aktywnego mastera lokalizacji — wgraj eksport EWM (Magazyn 3D → Master lokalizacji), "
             "potem wróć do „Wykryj z EWM”.")
STATUS = {"ok": ("Zgodne", "badge-green"), "diff": ("Rozbieżności", "badge-red"),
          "no_template": ("Rząd bez szablonu", "badge-yellow"), "no_row": ("Brak rzędu na planie", "badge-gray")}
LIST_LIMIT = 100   # kodów na listę w HTML; pełne listy w XLSX


@_planner
def warehouse_model_detect(request, pk):
    wm = get_object_or_404(WarehouseModel, pk=pk)
    batch = active_master()
    ctx = {"wm": wm, "batch": batch, "no_master": NO_MASTER}
    if batch:
        proposal = detect_for_model(wm, batch)
        names = {t["key"]: t["name"] for t in proposal["templates"]}
        ctx.update({
            "proposal": proposal,
            "new_templates": [t for t in proposal["templates"] if not t["pk"]],
            "old_templates": [t for t in proposal["templates"] if t["pk"]],
            "rows": [{**r, "template_name": names[r["template"]],
                      "bay_ov": sum(1 for o in r["overrides"] if not o["letter"]),
                      "loc_ov": sum(1 for o in r["overrides"] if o["letter"])} for r in proposal["rows"]],
            "total_overrides": sum(len(r["overrides"]) for r in proposal["rows"]),
        })
    return render(request, "ui/warehouse_model/ewm_detect.html", ctx)


@require_POST
@_md_role
def warehouse_model_detect_save(request, pk):
    wm = get_object_or_404(WarehouseModel, pk=pk)
    batch = active_master()
    if not batch:
        messages.error(request, NO_MASTER)
        return redirect("ui:warehouse_model_detect", pk=wm.pk)
    created, rows, overrides = apply_proposal(wm, detect_for_model(wm, batch))
    messages.success(request, f"Zapisano „Wykryj z EWM”: {rows} rzędów, {created} nowych szablonów, "
                              f"{overrides} wyjątków.")
    return redirect("ui:warehouse_model_compliance", pk=wm.pk)


@_planner
def warehouse_model_compliance(request, pk):
    wm = get_object_or_404(WarehouseModel, pk=pk)
    batch = active_master()
    report = compliance_for_model(wm, batch)
    if request.GET.get("format") == "xlsx":
        return _compliance_xlsx(wm, report)
    for a in report["aisles"]:
        a["label"], a["badge"] = STATUS[a["status"]]
    return render(request, "ui/warehouse_model/ewm_compliance.html", {
        "wm": wm, "batch": batch, "report": report, "no_master": NO_MASTER, "limit": LIST_LIMIT})


def _compliance_xlsx(wm, report):
    wb, ws, response = _make_xlsx_response(f"zgodnosc_ewm_model_{wm.pk}.xlsx")
    ws.title = "Zgodność"
    ws.append(["Strefa", "Przejście", "Status", "Na planie", "W EWM", "Zgodne", "Zgodność %",
               "Tylko plan", "Tylko EWM", "Duplikaty"])
    for a in report["aisles"]:
        ws.append([a["zone"], a["aisle"], STATUS[a["status"]][0], a["plan"], a["ewm"], a["matched"], a["pct"],
                   len(a["plan_only"]), len(a["ewm_only"]), len(a["duplicates"])])
    diff = wb.create_sheet("Rozbieżności")
    diff.append(["Strefa", "Przejście", "Kod", "Rodzaj"])
    for a in report["aisles"]:
        for kind, key in (("na planie, brak w EWM", "plan_only"), ("w EWM, brak na planie", "ewm_only"),
                          ("duplikat", "duplicates")):
            for code in a[key]:
                diff.append([a["zone"], a["aisle"], code, kind])
    return _finalize_xlsx(wb, ws, response)


__all__ = ["warehouse_model_detect", "warehouse_model_detect_save", "warehouse_model_compliance"]
```

`web/wh3d/urls.py` — w sekcji modeli, obok `magazyn/model/<int:pk>/coords/`:

```python
    path("magazyn/model/<int:pk>/wykryj-ewm/", wh3d_views.warehouse_model_detect, name="warehouse_model_detect"),
    path("magazyn/model/<int:pk>/wykryj-ewm/zapisz/", wh3d_views.warehouse_model_detect_save,
         name="warehouse_model_detect_save"),
    path("magazyn/model/<int:pk>/zgodnosc-ewm/", wh3d_views.warehouse_model_compliance,
         name="warehouse_model_compliance"),
```

`web/wh3d/views/__init__.py` — dopisz:

```python
from .warehouse_model_ewm import *  # noqa: F401,F403
```

`web/wh3d/templates/ui/warehouse_model/ewm_detect.html`:

```html
{% extends "ui/base.html" %}
{% block title %}Wykryj z EWM — {{ wm.name }} — {{ app_name }}{% endblock %}
{% block nav_warehouse_map %}topnav__link--active{% endblock %}

{% block content %}
<nav class="breadcrumb">
  <a href="{% url 'ui:warehouse_model_list' %}">Modele magazynu</a>
  <span class="breadcrumb__sep">›</span>
  <a href="{% url 'ui:warehouse_model_view' wm.pk %}">{{ wm.name }}</a>
  <span class="breadcrumb__sep">›</span>
  <span>Wykryj z EWM</span>
</nav>

<div class="page-header">
  <div>
    <div class="page-header__title">Wykryj z EWM — {{ wm.name }}</div>
    <div class="page-header__sub">Propozycja szablonów gniazd, numeracji i wyjątków z kodów lokalizacji{% if batch %} (master „{{ batch.name }}”){% endif %}. Nic nie jest zapisane, dopóki nie klikniesz „Zapisz”.</div>
  </div>
  <div class="page-header__actions">
    <a href="{% url 'ui:warehouse_model_compliance' wm.pk %}" class="btn btn-secondary">Zgodność z EWM</a>
    <a href="{% url 'ui:warehouse_model_view' wm.pk %}" class="btn btn-ghost">Wróć do modelu</a>
  </div>
</div>

{% if not batch %}
<div class="alert alert-warning">{{ no_master }}</div>
{% else %}
<div class="stats-grid">
  <div class="stat-card"><div><div class="stat-value">{{ rows|length }}</div><div class="stat-label">rzędów z kodami EWM</div></div></div>
  <div class="stat-card"><div><div class="stat-value">{{ new_templates|length }}</div><div class="stat-label">nowych szablonów</div></div></div>
  <div class="stat-card"><div><div class="stat-value">{{ old_templates|length }}</div><div class="stat-label">istniejących szablonów użytych</div></div></div>
  <div class="stat-card"><div><div class="stat-value">{{ total_overrides }}</div><div class="stat-label">wyjątków (gniazd + miejsc)</div></div></div>
</div>

{% if proposal.missing_rows %}
<div class="alert alert-info" style="margin-bottom:16px">
  Rzędy bez żadnego kodu w masterze (zostają bez zmian): {{ proposal.missing_rows|join:", " }}
</div>
{% endif %}

<div class="card" style="margin-bottom:20px">
  <div class="card-header"><span class="card-header__title">Szablony gniazd</span>
    <span class="text-sm text-muted">wysokości poziomów do poprawy w „Szablony gniazd”, jeśli master ich nie ma</span></div>
  <div class="table-wrap">
    <table class="table">
      <thead><tr><th>Szablon</th><th></th><th>Palet</th><th>Poziomy od dołu</th><th>Gniazd</th></tr></thead>
      <tbody>
        {% for t in proposal.templates %}
        <tr>
          <td class="font-semibold">{{ t.name }}</td>
          <td>{% if t.pk %}<span class="badge badge-gray">istniejący</span>{% else %}<span class="badge badge-blue">nowy</span>{% endif %}</td>
          <td>{{ t.pallets_per_beam }}</td>
          <td><code>{% for lv in t.levels %}{{ lv.letter }}{% if lv.split %}½{% endif %} {% endfor %}</code></td>
          <td>{{ t.bays }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<div class="card">
  <div class="card-header"><span class="card-header__title">Rzędy</span></div>
  <div class="table-wrap">
    <table class="table">
      <thead><tr><th>Rząd</th><th>Szablon domyślny</th><th>Numeracja gniazd</th><th>Wyjątki gniazd</th><th>Wyjątki miejsc</th><th>Kodów EWM</th></tr></thead>
      <tbody>
        {% for r in rows %}
        <tr>
          <td class="font-semibold">{{ r.zone }}-{{ r.rack_id }}</td>
          <td>{{ r.template_name }}</td>
          <td><code>{{ r.bay_numbers }}</code></td>
          <td>{{ r.bay_ov }}</td>
          <td>{{ r.loc_ov }}</td>
          <td>{{ r.codes }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% if can_write_products %}
  <div class="card-footer" style="display:flex;gap:10px;align-items:center">
    <form method="post" action="{% url 'ui:warehouse_model_detect_save' wm.pk %}">
      {% csrf_token %}
      <button type="submit" class="btn btn-primary">Zapisz</button>
    </form>
    <span class="text-sm text-muted">Zapis tworzy nowe szablony, ustawia szablon i numerację rzędów oraz zastępuje wyjątki tych rzędów.</span>
  </div>
  {% endif %}
</div>
{% endif %}
{% endblock %}
```

`web/wh3d/templates/ui/warehouse_model/ewm_compliance.html`:

```html
{% extends "ui/base.html" %}
{% block title %}Zgodność z EWM — {{ wm.name }} — {{ app_name }}{% endblock %}
{% block nav_warehouse_map %}topnav__link--active{% endblock %}

{% block extra_head %}
<style>
  .codes { font-family: var(--font-mono, monospace); font-size: 11px; line-height: 1.7; color: var(--gray-700); }
  details summary { cursor: pointer; font-size: 12px; color: var(--blue); }
</style>
{% endblock %}

{% block content %}
<nav class="breadcrumb">
  <a href="{% url 'ui:warehouse_model_list' %}">Modele magazynu</a>
  <span class="breadcrumb__sep">›</span>
  <a href="{% url 'ui:warehouse_model_view' wm.pk %}">{{ wm.name }}</a>
  <span class="breadcrumb__sep">›</span>
  <span>Zgodność z EWM</span>
</nav>

<div class="page-header">
  <div>
    <div class="page-header__title">Zgodność z EWM — {{ wm.name }}</div>
    <div class="page-header__sub">Kody generowane z planu (szablony + numeracja + wyjątki) porównane z masterem lokalizacji{% if batch %} „{{ batch.name }}”{% endif %}</div>
  </div>
  <div class="page-header__actions">
    <a href="?format=xlsx" class="btn btn-secondary">Pobierz XLSX</a>
    <a href="{% url 'ui:warehouse_model_detect' wm.pk %}" class="btn btn-secondary">Wykryj z EWM</a>
    <a href="{% url 'ui:warehouse_model_view' wm.pk %}" class="btn btn-ghost">Wróć do modelu</a>
  </div>
</div>

{% if not batch %}<div class="alert alert-warning" style="margin-bottom:16px">{{ no_master }}</div>{% endif %}

<div class="stats-grid">
  <div class="stat-card"><div><div class="stat-value">{{ report.summary.pct }}%</div><div class="stat-label">zgodność kodów (plan ∪ EWM)</div></div></div>
  <div class="stat-card"><div><div class="stat-value">{{ report.summary.ok }} / {{ report.summary.aisles }}</div><div class="stat-label">przejść zgodnych</div></div></div>
  <div class="stat-card"><div><div class="stat-value">{{ report.summary.plan }}</div><div class="stat-label">miejsc na planie</div></div></div>
  <div class="stat-card"><div><div class="stat-value">{{ report.summary.ewm }}</div><div class="stat-label">kodów w EWM (strefy modelu)</div></div></div>
</div>

<div class="card">
  <div class="table-wrap">
    <table class="table">
      <thead><tr><th>Przejście</th><th>Status</th><th>Na planie</th><th>W EWM</th><th>Zgodne</th><th>%</th><th>Rozbieżności</th></tr></thead>
      <tbody>
        {% for a in report.aisles %}
        <tr>
          <td class="font-semibold">{{ a.zone }}-{{ a.aisle }}</td>
          <td><span class="badge {{ a.badge }}">{{ a.label }}</span></td>
          <td>{{ a.plan }}</td>
          <td>{{ a.ewm }}</td>
          <td>{{ a.matched }}</td>
          <td>{{ a.pct }}</td>
          <td>
            {% if a.plan_only %}<details><summary>na planie, brak w EWM ({{ a.plan_only|length }})</summary>
              <div class="codes">{{ a.plan_only|slice:":100"|join:" · " }}{% if a.plan_only|length > limit %} … (pełna lista w XLSX){% endif %}</div></details>{% endif %}
            {% if a.ewm_only %}<details><summary>w EWM, brak na planie ({{ a.ewm_only|length }})</summary>
              <div class="codes">{{ a.ewm_only|slice:":100"|join:" · " }}{% if a.ewm_only|length > limit %} … (pełna lista w XLSX){% endif %}</div></details>{% endif %}
            {% if a.duplicates %}<details><summary>duplikaty ({{ a.duplicates|length }})</summary>
              <div class="codes">{{ a.duplicates|slice:":100"|join:" · " }}</div></details>{% endif %}
            {% if a.status == "ok" %}<span class="text-muted text-sm">—</span>{% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endblock %}
```

`web/wh3d/templates/ui/warehouse_model/view.html` — w `page-header__actions`, przed linkiem „Edytuj współrzędne”:

```html
    <a href="{% url 'ui:warehouse_model_detect' wm.pk %}" class="btn btn-secondary"
       title="Szablony gniazd, numeracja i wyjątki z kodów lokalizacji EWM (podgląd przed zapisem)">
      Wykryj z EWM
    </a>
    <a href="{% url 'ui:warehouse_model_compliance' wm.pk %}" class="btn btn-secondary"
       title="Kody z planu vs master EWM, per przejście">
      Zgodność z EWM
    </a>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sh web/scripts/test.sh wh3d.tests.test_ewm_views wh3d.tests.test_warehouse_model_view ui.tests.test_views_package ui.tests.test_wh3d_boundary`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add web/wh3d/views/warehouse_model_ewm.py web/wh3d/views/__init__.py web/wh3d/urls.py \
  web/wh3d/templates/ui/warehouse_model/ewm_detect.html web/wh3d/templates/ui/warehouse_model/ewm_compliance.html \
  web/wh3d/templates/ui/warehouse_model/view.html web/wh3d/tests/test_ewm_views.py
git diff --cached --stat
git commit -m "feat(wh3d): ekrany „Wykryj z EWM” (podgląd → zapis) i „Zgodność z EWM” + XLSX"
```

**PR E**: gałąź `claude/edytor-zgodnosc-ui`, tytuł „feat(wh3d): Wykryj z EWM + Zgodność z EWM — ekrany (edytor układu cz. 1)”.

---

## Sprawdzian końcowy (po PR E na prodzie)

1. Lokalnie, na pełnym eksporcie (`Lokalizacje_EWM.xlsx`) i modelu z `regaly_z_rysunku.csv`: skrypt w scratchpadzie tworzy model (upload geometrii), master (upload XLSX przez `warehouse_master_upload`), woła `detect_for_model` → `apply_proposal` → `compliance_for_model`; oczekiwane: przejścia B0 01–54 `ok` (54/54), 55–82 `no_row`.
2. Na produkcji (groove.example.com): model hali Logistyczna → „Wykryj z EWM” → „Zapisz” → „Zgodność z EWM”: 01–54 = „Zgodne”.
3. Raport (tabela + XLSX) i wyniki testów pokazane użytkownikowi.
