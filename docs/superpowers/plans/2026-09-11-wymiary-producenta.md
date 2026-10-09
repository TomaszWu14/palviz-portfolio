# Moduł „Wymiary producenta" — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import producer-declared carton dimensions (58 supplier `.xlsx`), compare each REF against our master-data carton dims, flag discrepancies on a report screen, and auto-create deduplicated Tasks for real mismatches.

**Architecture:** New `ui` sub-feature. A pure `compare_dims()` seam does the tolerance/orientation logic (unit-tested with no DB/HTTP). Two models (`ProducerCartonBatch` + `ProducerCartonDim`) store the imported producer side. A multi-file import view parses the shared `dane opakowań` template positionally, resolves REF→Product, and raises dedup Tasks. A report view joins active producer rows to `Product.latest_instruction()` (fallback `MaterialReference`) and renders OK / mismatch / no-data badges. Reuses packspec import + notifications task engine.

**Tech Stack:** Django 5.2, Python 3.11, openpyxl (hard dep), existing `ui.product_codes.resolve_product_code`, `ui.notifications` task engine, `ui.models.packspec.ImportRun`.

## Global Constraints

- Django app = `ui`; all URLs in the `ui:` namespace (reverse as `ui:<name>`).
- Role guard `@_master_data` from `ui.roles` on every view; superuser always passes.
- Every new view/model re-exported via the package `__all__` / star-export; run `manage.py check` after models + URLs.
- Files < 500 lines; feature modules focused.
- Dimensions in **cm**. Producer carton dims = 3 numeric columns; box/pouch strings never compared.
- Tolerance per axis = `max(1.0 cm, 5% * ours_axis)`; sorted-triple (set) comparison.
- Tasks: category `carton_dim_mismatch`, `dedup_key=f"cartondim:{supplier}:{ref_code}"`, priority high, recipients `owner_users()`; only real mismatches, dedup on reimport.
- UI Polish-first, GROOVE design tokens (`var(--…)`), no default templates.
- CI env for tests: `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=..` run from `web/`.

---

### Task 1: Models + migration (producer side storage)

**Files:**
- Create: `web/ui/models/producer_dims.py`
- Modify: `web/ui/models/__init__.py` (re-export)
- Modify: `web/ui/models/packspec.py:53-70` (add `ImportRun` KIND)
- Modify: `web/ui/models/tasks_users.py:10-16` (add `Task` CATEGORY choice)
- Create (generated): `web/ui/migrations/0191_producer_carton_dims.py`

**Interfaces:**
- Produces: `ProducerCartonBatch` (fields: `supplier`, `source_filename`, `uploaded_at`, `uploaded_by`, `is_active`, `row_count`); `ProducerCartonDim` (fields: `batch` FK, `supplier`, `ref_code`, `product` FK nullable, `carton_l/w/h` FloatField null, `qty_in_carton` int null, `gross_kg/net_kg` Float null, `box_size_raw/pouch_size_raw/description` Char). Both re-exported from `ui.models`.

- [ ] **Step 1: Write the model module**

Create `web/ui/models/producer_dims.py`:

```python
from django.conf import settings
from django.db import models


class ProducerCartonBatch(models.Model):
    """Jedna partia importu = jeden plik dostawcy. Ponowny import dostawcy dezaktywuje
    poprzednią aktywną partię (wzorzec packspec)."""
    supplier = models.CharField(max_length=120, db_index=True, verbose_name="Dostawca")
    source_filename = models.CharField(max_length=255, blank=True, verbose_name="Plik źródłowy")
    uploaded_at = models.DateTimeField(auto_now_add=True, db_index=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL)
    is_active = models.BooleanField(default=True, db_index=True)
    row_count = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Partia wymiarów producenta"
        verbose_name_plural = "Partie wymiarów producenta"
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.supplier} ({self.uploaded_at:%Y-%m-%d %H:%M})"


class ProducerCartonDim(models.Model):
    """Deklarowane przez producenta wymiary kartonu dla jednego REF (jeden wiersz pliku)."""
    batch = models.ForeignKey(ProducerCartonBatch, on_delete=models.CASCADE,
                              related_name="rows")
    supplier = models.CharField(max_length=120, db_index=True)   # denorm: filtr/dedup
    ref_code = models.CharField(max_length=100, db_index=True, verbose_name="REF")
    product = models.ForeignKey("ui.Product", null=True, blank=True,
                                on_delete=models.SET_NULL)
    carton_l = models.FloatField(null=True, blank=True, verbose_name="L kartonu [cm]")
    carton_w = models.FloatField(null=True, blank=True, verbose_name="W kartonu [cm]")
    carton_h = models.FloatField(null=True, blank=True, verbose_name="H kartonu [cm]")
    qty_in_carton = models.IntegerField(null=True, blank=True)
    gross_kg = models.FloatField(null=True, blank=True)
    net_kg = models.FloatField(null=True, blank=True)
    box_size_raw = models.CharField(max_length=120, blank=True)      # podgląd, nieporównywane
    pouch_size_raw = models.CharField(max_length=120, blank=True)    # podgląd, nieporównywane
    description = models.CharField(max_length=250, blank=True)

    class Meta:
        verbose_name = "Wymiar kartonu producenta"
        verbose_name_plural = "Wymiary kartonu producenta"
        indexes = [models.Index(fields=["supplier", "ref_code"])]

    def __str__(self):
        return f"{self.supplier}/{self.ref_code}"
```

- [ ] **Step 2: Re-export from the models package**

In `web/ui/models/__init__.py`, add alongside the other `from .X import *` lines:

```python
from .producer_dims import *  # noqa: F401,F403
```

If `producer_dims.py` needs an `__all__`, add at its top-level end:

```python
__all__ = ["ProducerCartonBatch", "ProducerCartonDim"]
```

- [ ] **Step 3: Register the ImportRun kind and Task category**

In `web/ui/models/packspec.py`, inside `ImportRun.KINDS` (after the `("packspec", …)` line):

```python
        ("producer_dims", "Wymiary producenta"),
```

In `web/ui/models/tasks_users.py`, inside `Task.CATEGORY` list (after `("stock_discrepancy", "Niezgodność stocku")`):

```python
        ("carton_dim_mismatch", "Rozjazd wymiarów kartonu"),
```

- [ ] **Step 4: Make the migration**

Run: `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py makemigrations ui`
Expected: creates `ui/migrations/0191_*.py` with `ProducerCartonBatch`, `ProducerCartonDim`, and `AlterField` for the two choices. If the tail number differs from 0191 (concurrent migration merged), keep whatever `makemigrations` picks — do not hand-number.

- [ ] **Step 5: Run system check**

Run: `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py check`
Expected: `System check identified no issues`.

- [ ] **Step 6: Commit**

```bash
git add web/ui/models/producer_dims.py web/ui/models/__init__.py web/ui/models/packspec.py web/ui/models/tasks_users.py web/ui/migrations/
git commit -m "feat(producer-dims): modele ProducerCartonBatch/Dim + kind/category"
```

---

### Task 2: `compare_dims()` seam + `ours_dims()` + parsing helper (pure, unit-tested)

**Files:**
- Create: `web/ui/producer_dims.py`
- Create: `web/ui/tests/test_producer_dims.py`

**Interfaces:**
- Consumes: `ProducerCartonDim`, `Product.latest_instruction()`, `MaterialReference` (Task 1 / existing).
- Produces:
  - `compare_dims(producer_lwh, ours_lwh, *, min_cm=1.0, pct=0.05) -> tuple[str, float | None]` — verdict in `{"ok","mismatch","no_data"}`, plus max axis delta cm (None when no_data). `*_lwh` are 3-tuples of floats or None.
  - `ours_dims(product) -> tuple[float, float, float] | None` — carton L/W/H from `latest_instruction()` (fallback `MaterialReference`), None when unavailable.
  - `parse_dim(cell) -> float | None` — numeric cell or None for text/`-`//`/blank.
  - `supplier_from_filename(name) -> str` — strips `packaging size`, extension, separators.

- [ ] **Step 1: Write the failing tests**

Create `web/ui/tests/test_producer_dims.py`:

```python
from django.test import SimpleTestCase

from ui.producer_dims import compare_dims, parse_dim, supplier_from_filename


class CompareDimsTests(SimpleTestCase):
    def test_equal_after_transposition_is_ok(self):
        # sorted-triple: axis order irrelevant
        v, delta = compare_dims((53.5, 41, 37), (37, 53.5, 41))
        self.assertEqual(v, "ok")
        self.assertEqual(delta, 0.0)

    def test_beyond_tolerance_is_mismatch(self):
        v, delta = compare_dims((60, 40, 30), (60, 40, 36))   # 6 cm on one axis
        self.assertEqual(v, "mismatch")
        self.assertAlmostEqual(delta, 6.0)

    def test_small_box_protected_by_1cm_floor(self):
        # 8 cm axis, +0.6 cm < max(1.0, 5%*8=0.4) = 1.0 → ok
        v, _ = compare_dims((8, 8, 5.5), (8.6, 8, 5.5))
        self.assertEqual(v, "ok")

    def test_large_carton_within_percent_is_ok(self):
        # 60 cm axis, +2 cm < max(1.0, 5%*60=3.0) = 3.0 → ok
        v, _ = compare_dims((60, 40, 30), (62, 40, 30))
        self.assertEqual(v, "ok")

    def test_missing_side_is_no_data(self):
        self.assertEqual(compare_dims((None, 40, 30), (60, 40, 30))[0], "no_data")
        self.assertEqual(compare_dims((60, 40, 30), None)[0], "no_data")

    def test_parse_dim(self):
        self.assertEqual(parse_dim(43.5), 43.5)
        self.assertEqual(parse_dim("52"), 52.0)
        self.assertIsNone(parse_dim("-"))
        self.assertIsNone(parse_dim(None))

    def test_supplier_from_filename(self):
        self.assertEqual(supplier_from_filename("INTCO GLOVES packaging size.xlsx"),
                         "INTCO GLOVES")
        self.assertEqual(supplier_from_filename("packaging size - BAIHE.xlsx"), "BAIHE")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests.test_producer_dims -v 1`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.producer_dims'` (or import error).

- [ ] **Step 3: Write the implementation**

Create `web/ui/producer_dims.py`:

```python
"""Rdzeń modułu „Wymiary producenta": czyste porównanie wymiarów kartonu
(producent vs nasza master data), parsowanie komórek i etykieta dostawcy.
Bez Django ORM w części porównawczej — testowane bezpośrednio przez interfejs."""
import re


def parse_dim(cell):
    """Komórka wymiaru → float w cm albo None (tekst, '-', '/', puste)."""
    if cell is None:
        return None
    if isinstance(cell, (int, float)):
        return float(cell)
    s = str(cell).strip().replace(",", ".")
    m = re.match(r"^\d+(\.\d+)?$", s)
    return float(s) if m else None


def compare_dims(producer_lwh, ours_lwh, *, min_cm=1.0, pct=0.05):
    """(verdict, max_delta_cm). Sortuje oba tryplety malejąco i porównuje pozycyjnie
    (który bok to 'długość' jest umowne). Tolerancja per oś = max(min_cm, pct*nasz).
    Brak którejkolwiek strony → 'no_data'."""
    if not producer_lwh or not ours_lwh:
        return "no_data", None
    if any(v is None for v in producer_lwh) or any(v is None for v in ours_lwh):
        return "no_data", None
    p = sorted((float(x) for x in producer_lwh), reverse=True)
    o = sorted((float(x) for x in ours_lwh), reverse=True)
    max_delta = 0.0
    verdict = "ok"
    for pv, ov in zip(p, o):
        delta = abs(pv - ov)
        max_delta = max(max_delta, delta)
        if delta > max(min_cm, pct * ov):
            verdict = "mismatch"
    return verdict, round(max_delta, 2)


def ours_dims(product):
    """Nasze wymiary kartonu (L,W,H cm) dla produktu: instrukcja paletyzacji, fallback
    MaterialReference (mirror SAP MARM). None gdy brak. Zwraca surowe osie — porządek
    nieistotny (compare_dims i tak sortuje)."""
    if product is None:
        return None
    instr = product.latest_instruction()
    if instr and instr.carton_l and instr.carton_w and instr.carton_h:
        return (float(instr.carton_l), float(instr.carton_w), float(instr.carton_h))
    from ui.models import MaterialReference
    ref = MaterialReference.objects.filter(code__iexact=product.code).first()
    if ref and ref.length_cm and ref.width_cm and ref.height_cm:
        return (ref.length_cm, ref.width_cm, ref.height_cm)
    return None


def supplier_from_filename(name):
    """'INTCO GLOVES packaging size.xlsx' → 'INTCO GLOVES';
    'packaging size - BAIHE.xlsx' → 'BAIHE'."""
    base = re.sub(r"\.(xlsx|xls|csv)$", "", name or "", flags=re.I)
    base = re.sub(r"packaging size", "", base, flags=re.I)
    base = base.strip(" -–—_")
    return base or "—"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests.test_producer_dims -v 1`
Expected: PASS (all `CompareDimsTests`).

- [ ] **Step 5: Commit**

```bash
git add web/ui/producer_dims.py web/ui/tests/test_producer_dims.py
git commit -m "feat(producer-dims): rdzeń compare_dims/ours_dims/parse + testy"
```

---

### Task 3: Import view (multi-file) + auto-Task on mismatch

**Files:**
- Create: `web/ui/views/producer_dims.py`
- Modify: `web/ui/views/__init__.py` (star-export)
- Modify: `web/ui/urls.py` (two URLs)
- Modify: `web/ui/notifications.py:164` (add `category=` param to `_raise_task`)
- Modify: `web/ui/tests/test_producer_dims.py` (append import/task tests)

**Interfaces:**
- Consumes: `compare_dims`, `ours_dims`, `parse_dim`, `supplier_from_filename` (Task 2); `resolve_product_code`; `ImportRun.record`; `owner_users`, `_raise_task`.
- Produces: view `producer_dims_upload(request)` (POST, `@_master_data`); helper `_ingest_file(f, user) -> ProducerCartonBatch`; helper `_raise_mismatch_tasks(batch) -> int`. Report view added in Task 4.

- [ ] **Step 1: Add `category` param to the shared task creator**

In `web/ui/notifications.py`, change the `_raise_task` signature + create call:

```python
def _raise_task(title, description, source_ref, dedup_key, url, recipients, hu=None,
                category="stock_discrepancy"):
```

and in its `Task.objects.create(...)` replace `category="stock_discrepancy",` with `category=category,`. (Backward-compatible: existing callers keep the default.)

- [ ] **Step 2: Write the failing import + task tests**

Append to `web/ui/tests/test_producer_dims.py`:

```python
import io
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
import openpyxl

from ui.models import (ProducerCartonBatch, ProducerCartonDim, Product,
                       PalletizationInstruction, Task)
from ui.roles import GROUP_MASTER_DATA
from ui.views.producer_dims import _ingest_file, _raise_mismatch_tasks


def _mk_xlsx(rows):
    """Buduje plik jak szablon 'dane opakowań': 3 wiersze nagłówka (REF w kol B wiersza 3),
    dane od wiersza 4. rows = [(ref, l, w, h), ...]."""
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "dane opakowań"
    ws.append([None] * 14)                                   # r1
    ws.append([None, None, None, None, None, None, None, None, None, None, None, None, None, None])  # r2
    hdr = [None] * 14; hdr[1] = "REF"; ws.append(hdr)        # r3 (idx2): kol B = REF
    for i, (ref, l, w, h) in enumerate(rows, 1):
        row = [None] * 14
        row[0], row[1] = i, ref
        row[8], row[9], row[10] = l, w, h
        ws.append(row)
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    buf.name = "TESTSUP packaging size.xlsx"
    return buf


class ProducerDimsImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("md", "m@m.pl", "x")
        p = Product.objects.create(code="AF-6090", name="Podkład")
        PalletizationInstruction.objects.create(
            product=p, version=1, is_active=True, variant="A",
            carton_l=53, carton_w=41, carton_h=37,
            unit_weight=1.0, pcs_per_carton=10, carton_tare=0.5)

    def test_ingest_creates_batch_rows_and_resolves_product(self):
        f = _mk_xlsx([("AF-6090", 53, 41, 37), ("UNKNOWN-REF", 20, 20, 20)])
        batch = _ingest_file(f, self.user)
        self.assertEqual(batch.supplier, "TESTSUP")
        self.assertEqual(batch.row_count, 2)
        got = {r.ref_code: r for r in ProducerCartonDim.objects.all()}
        self.assertIsNotNone(got["AF-6090"].product)
        self.assertIsNone(got["UNKNOWN-REF"].product)

    def test_reimport_deactivates_previous_batch(self):
        _ingest_file(_mk_xlsx([("AF-6090", 53, 41, 37)]), self.user)
        _ingest_file(_mk_xlsx([("AF-6090", 53, 41, 37)]), self.user)
        active = ProducerCartonBatch.objects.filter(supplier="TESTSUP", is_active=True)
        self.assertEqual(active.count(), 1)

    def test_bad_header_rejected(self):
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "dane opakowań"
        ws.append(["nonsense"]); ws.append([None]); ws.append([None]); ws.append([1])
        buf = io.BytesIO(); wb.save(buf); buf.seek(0); buf.name = "X packaging size.xlsx"
        with self.assertRaises(ValueError):
            _ingest_file(buf, self.user)

    def test_mismatch_creates_one_deduped_task(self):
        # producent 53x41x50 vs nasze 53x41x37 → rozjazd 13 cm
        batch = _ingest_file(_mk_xlsx([("AF-6090", 53, 41, 50)]), self.user)
        n = _raise_mismatch_tasks(batch)
        self.assertEqual(n, 1)
        self.assertEqual(Task.objects.filter(category="carton_dim_mismatch").count(), 1)
        # reimport → dedup, brak nowego taska
        batch2 = _ingest_file(_mk_xlsx([("AF-6090", 53, 41, 50)]), self.user)
        self.assertEqual(_raise_mismatch_tasks(batch2), 0)

    def test_ok_and_nodata_create_no_task(self):
        batch = _ingest_file(_mk_xlsx([("AF-6090", 53, 41, 37),      # ok
                                       ("UNKNOWN-REF", 20, 20, 20)]),  # no_data (brak product)
                             self.user)
        self.assertEqual(_raise_mismatch_tasks(batch), 0)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests.test_producer_dims -v 1`
Expected: FAIL — `ImportError: cannot import name '_ingest_file'`.

- [ ] **Step 4: Write the view module**

Create `web/ui/views/producer_dims.py`:

```python
"""Moduł „Wymiary producenta": import deklarowanych wymiarów kartonu od producentów
i porównanie z naszą master data. Ekran w Data Center, rola Master Data."""
import openpyxl
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from ..models import (ProducerCartonBatch, ProducerCartonDim, ImportRun)
from ..producer_dims import (compare_dims, ours_dims, parse_dim, supplier_from_filename)
from ..roles import _master_data
from .. import product_codes

_SHEET = "dane opakowań"
# Pozycje kolumn w szablonie (0-indeks): B=REF, C=opis, D=pouch, G=box, I/J/K=carton L/W/H,
# L=qty/karton, M=gross, N=net. Dane od wiersza 5 (3 wiersze nagłówka).
_C = {"ref": 1, "desc": 2, "pouch": 3, "box": 6, "l": 8, "w": 9, "h": 10,
      "qty": 11, "gross": 12, "net": 13}


def _cell(row, idx):
    return row[idx] if idx < len(row) else None


def _to_int(v):
    f = parse_dim(v)
    return int(f) if f is not None else None


def _ingest_file(f, user):
    """Parsuje jeden plik dostawcy → nowa aktywna ProducerCartonBatch (+ wiersze).
    Dezaktywuje poprzednią aktywną partię tego dostawcy. Rzuca ValueError przy złym
    nagłówku (kol B wiersza 3 ≠ 'REF')."""
    wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
    ws = wb[_SHEET] if _SHEET in wb.sheetnames else wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if len(rows) < 4:
        raise ValueError("Plik nie ma danych (brak wierszy pod nagłówkiem).")
    hdr = rows[2]                                    # wiersz 3 = subnagłówek z 'REF' w kol B
    if str(_cell(hdr, _C["ref"]) or "").strip().lower() != "ref":
        raise ValueError("Nie rozpoznano szablonu — kol. B wiersza 3 nie zawiera 'REF'.")
    supplier = supplier_from_filename(getattr(f, "name", ""))
    with transaction.atomic():
        (ProducerCartonBatch.objects
         .filter(supplier=supplier, is_active=True).update(is_active=False))
        batch = ProducerCartonBatch.objects.create(
            supplier=supplier, source_filename=getattr(f, "name", "")[:255],
            uploaded_by=user, is_active=True)
        objs = []
        for row in rows[3:]:                          # dane od wiersza 4 (idx 3)
            ref = str(_cell(row, _C["ref"]) or "").strip()
            if not ref:
                continue
            product = product_codes.resolve_product_code(ref)
            objs.append(ProducerCartonDim(
                batch=batch, supplier=supplier, ref_code=ref[:100], product=product,
                carton_l=parse_dim(_cell(row, _C["l"])),
                carton_w=parse_dim(_cell(row, _C["w"])),
                carton_h=parse_dim(_cell(row, _C["h"])),
                qty_in_carton=_to_int(_cell(row, _C["qty"])),
                gross_kg=parse_dim(_cell(row, _C["gross"])),
                net_kg=parse_dim(_cell(row, _C["net"])),
                box_size_raw=str(_cell(row, _C["box"]) or "")[:120],
                pouch_size_raw=str(_cell(row, _C["pouch"]) or "")[:120],
                description=str(_cell(row, _C["desc"]) or "")[:250]))
        ProducerCartonDim.objects.bulk_create(objs)
        batch.row_count = len(objs)
        batch.save(update_fields=["row_count"])
    ImportRun.record("producer_dims", rows=len(objs), label=supplier, user=user)
    return batch


def _raise_mismatch_tasks(batch):
    """Dla realnych rozjazdów partii twórz zdedublowane Taski (kategoria
    carton_dim_mismatch, dedup po dostawca+REF). Zwraca liczbę NOWYCH tasków."""
    from django.urls import reverse
    from ..notifications import owner_users, _raise_task
    recipients = owner_users()
    n = 0
    rows = (batch.rows.select_related("product")
            .prefetch_related("product__instructions"))
    for r in rows:
        verdict, delta = compare_dims(
            (r.carton_l, r.carton_w, r.carton_h), ours_dims(r.product))
        if verdict != "mismatch":
            continue
        created = _raise_task(
            title=f"Rozjazd wymiarów kartonu: {r.ref_code} ({batch.supplier})",
            description=(f"Producent {batch.supplier} podaje inny wymiar kartonu niż "
                        f"master data (Δ {delta} cm). Zweryfikuj REF {r.ref_code}."),
            source_ref=r.ref_code, dedup_key=f"cartondim:{batch.supplier}:{r.ref_code}",
            url=reverse("ui:producer_dims"), recipients=recipients,
            category="carton_dim_mismatch")
        if created:
            n += 1
    return n


@_master_data
@require_POST
def producer_dims_upload(request):
    """Multi-upload plików dostawców. Każdy plik = partia; po imporcie auto-Taski."""
    files = request.FILES.getlist("files") or ([request.FILES["file"]]
                                               if request.FILES.get("file") else [])
    if not files:
        messages.error(request, "Nie wybrano plików.")
        return redirect("ui:producer_dims")
    ok, tasks, errors = 0, 0, 0
    for f in files:
        if f.size > 20 * 1024 * 1024:
            messages.error(request, f"{f.name}: plik zbyt duży (max 20 MB).")
            errors += 1
            continue
        try:
            batch = _ingest_file(f, request.user)
            tasks += _raise_mismatch_tasks(batch)
            ok += 1
        except Exception as exc:                      # zły szablon/odczyt — pomiń plik
            messages.error(request, f"{f.name}: {exc}")
            errors += 1
    if ok:
        messages.success(request, f"Zaimportowano plików: {ok}. Nowych zadań: {tasks}.")
    return redirect("ui:producer_dims")
```

- [ ] **Step 5: Add the `category` verify + star-export + URLs**

In `web/ui/views/__init__.py`, add alongside other feature imports:

```python
from .producer_dims import *  # noqa: F401,F403
```

Add `__all__` at the end of `web/ui/views/producer_dims.py`:

```python
__all__ = ["producer_dims_upload", "producer_dims_list"]
```

(`producer_dims_list` is added in Task 4 — declare it now so the star-export is stable; if running Task 3 in isolation, temporarily list only `producer_dims_upload`.)

In `web/ui/urls.py`, add inside `urlpatterns`:

```python
    path("wymiary-producenta/", views.producer_dims_list, name="producer_dims"),
    path("wymiary-producenta/upload/", views.producer_dims_upload, name="producer_dims_upload"),
```

(Note: `producer_dims_list` is defined in Task 4. Do Tasks 3 and 4 back-to-back, or comment the list URL until Task 4.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests.test_producer_dims -v 1`
Expected: PASS (import + task tests). If `producer_dims_list` URL/view not yet present, temporarily comment its URL line.

- [ ] **Step 7: Commit**

```bash
git add web/ui/views/producer_dims.py web/ui/views/__init__.py web/ui/urls.py web/ui/notifications.py web/ui/tests/test_producer_dims.py
git commit -m "feat(producer-dims): import multi-file + auto-Taski rozjazdów (dedup)"
```

---

### Task 4: Report view + template + Data Center link

**Files:**
- Modify: `web/ui/views/producer_dims.py` (add `producer_dims_list`)
- Create: `web/ui/templates/ui/producer_dims/list.html`
- Modify: Data Center template (add link) — locate with grep in Step 4
- Modify: `web/ui/tests/test_producer_dims.py` (append report test)

**Interfaces:**
- Consumes: `ProducerCartonDim`, `compare_dims`, `ours_dims` (Tasks 1–2); Django `Paginator`.
- Produces: view `producer_dims_list(request)` (`@_master_data`) rendering `ui/producer_dims/list.html` with context `{rows, suppliers, f_supplier, f_status, counts, page_obj}`.

- [ ] **Step 1: Write the failing report test**

Append to `web/ui/tests/test_producer_dims.py` (inside a new `TestCase`, reusing the `_mk_xlsx` helper and `setUp` pattern — create the product+instruction as in `ProducerDimsImportTests`):

```python
class ProducerDimsReportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("md2", "m2@m.pl", "x")
        p = Product.objects.create(code="AF-6090", name="Podkład")
        PalletizationInstruction.objects.create(
            product=p, version=1, is_active=True, variant="A",
            carton_l=53, carton_w=41, carton_h=37,
            unit_weight=1.0, pcs_per_carton=10, carton_tare=0.5)
        _ingest_file(_mk_xlsx([("AF-6090", 53, 41, 50)]), self.user)   # mismatch row
        self.client.force_login(self.user)

    def test_report_renders_with_verdict_badge(self):
        from django.urls import reverse
        resp = self.client.get(reverse("ui:producer_dims"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["counts"]["mismatch"], 1)
        self.assertContains(resp, "AF-6090")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests.test_producer_dims.ProducerDimsReportTests -v 1`
Expected: FAIL — `producer_dims_list` not defined / template missing.

- [ ] **Step 3: Add the report view**

Insert into `web/ui/views/producer_dims.py` (before `__all__`):

```python
from django.core.paginator import Paginator


@_master_data
def producer_dims_list(request):
    """Raport: aktywne wiersze producentów vs nasza master data, z odznaką werdyktu."""
    f_supplier = (request.GET.get("supplier") or "").strip()
    f_status = (request.GET.get("status") or "").strip()
    qs = (ProducerCartonDim.objects.filter(batch__is_active=True)
          .select_related("product").prefetch_related("product__instructions")
          .order_by("supplier", "ref_code"))
    if f_supplier:
        qs = qs.filter(supplier=f_supplier)
    counts = {"ok": 0, "mismatch": 0, "no_data": 0}
    rows = []
    for r in qs:
        ours = ours_dims(r.product)
        verdict, delta = compare_dims((r.carton_l, r.carton_w, r.carton_h), ours)
        counts[verdict] += 1
        if f_status and verdict != f_status:
            continue
        rows.append({"r": r, "ours": ours, "verdict": verdict, "delta": delta})
    suppliers = (ProducerCartonDim.objects.filter(batch__is_active=True)
                 .values_list("supplier", flat=True).distinct().order_by("supplier"))
    page_obj = Paginator(rows, 100).get_page(request.GET.get("page"))
    return render(request, "ui/producer_dims/list.html", {
        "page_obj": page_obj, "counts": counts, "suppliers": suppliers,
        "f_supplier": f_supplier, "f_status": f_status})
```

- [ ] **Step 4: Create the template**

Create `web/ui/templates/ui/producer_dims/list.html` (extends the app base; Polish-first, GROOVE tokens). Locate the base template + a page-header/panel example first:

Run: `grep -rl "page-header" web/ui/templates/ui/packspec/ web/ui/templates/ui/ | head -3`

Then mirror that structure. Minimum content:

```html
{% extends "ui/base.html" %}
{% block content %}
<div class="page-header">
  <h1>Wymiary producenta</h1>
  <p class="text-muted">Porównanie wymiarów kartonu deklarowanych przez producentów z naszą master data.</p>
</div>

<form method="post" action="{% url 'ui:producer_dims_upload' %}" enctype="multipart/form-data" class="panel" style="margin-bottom:1rem">
  {% csrf_token %}
  <input type="file" name="files" multiple accept=".xlsx,.xls,.csv" required>
  <button type="submit" class="btn btn-primary">Wgraj pliki dostawców</button>
</form>

<div class="panel" style="display:flex;gap:1rem;margin-bottom:1rem">
  <span class="badge badge-green">OK: {{ counts.ok }}</span>
  <span class="badge" style="background:var(--red-solid);color:#fff">Rozjazdy: {{ counts.mismatch }}</span>
  <span class="badge badge-gray">Brak danych: {{ counts.no_data }}</span>
</div>

<form method="get" class="panel" style="display:flex;gap:1rem;margin-bottom:1rem">
  <select name="supplier" onchange="this.form.submit()">
    <option value="">— dostawca —</option>
    {% for s in suppliers %}<option value="{{ s }}" {% if s == f_supplier %}selected{% endif %}>{{ s }}</option>{% endfor %}
  </select>
  <select name="status" onchange="this.form.submit()">
    <option value="">— status —</option>
    <option value="mismatch" {% if f_status == 'mismatch' %}selected{% endif %}>Rozjazd</option>
    <option value="ok" {% if f_status == 'ok' %}selected{% endif %}>OK</option>
    <option value="no_data" {% if f_status == 'no_data' %}selected{% endif %}>Brak danych</option>
  </select>
</form>

<table class="table">
  <thead><tr>
    <th>REF</th><th>Dostawca</th><th>Producent L×W×H</th><th>Nasze L×W×H</th>
    <th class="text-right">Δ cm</th><th>Status</th>
  </tr></thead>
  <tbody>
  {% for it in page_obj %}
    <tr>
      <td class="font-semibold">{{ it.r.ref_code }}</td>
      <td>{{ it.r.supplier }}</td>
      <td>{{ it.r.carton_l|default:"—" }}×{{ it.r.carton_w|default:"—" }}×{{ it.r.carton_h|default:"—" }}</td>
      <td>{% if it.ours %}{{ it.ours.0 }}×{{ it.ours.1 }}×{{ it.ours.2 }}{% else %}—{% endif %}</td>
      <td class="text-right">{{ it.delta|default:"—" }}</td>
      <td>
        {% if it.verdict == 'ok' %}<span class="badge badge-green">OK</span>
        {% elif it.verdict == 'mismatch' %}<span class="badge" style="background:var(--red-solid);color:#fff">rozjazd</span>
        {% else %}<span class="badge badge-gray">brak danych</span>{% endif %}
      </td>
    </tr>
  {% empty %}
    <tr><td colspan="6" class="text-muted">Brak danych — wgraj pliki dostawców.</td></tr>
  {% endfor %}
  </tbody>
</table>

{% if page_obj.has_other_pages %}
<div class="pagination">
  {% if page_obj.has_previous %}<a href="?page={{ page_obj.previous_page_number }}&supplier={{ f_supplier }}&status={{ f_status }}">‹</a>{% endif %}
  <span>{{ page_obj.number }}/{{ page_obj.paginator.num_pages }}</span>
  {% if page_obj.has_next %}<a href="?page={{ page_obj.next_page_number }}&supplier={{ f_supplier }}&status={{ f_status }}">›</a>{% endif %}
</div>
{% endif %}
{% endblock %}
```

Verify `{% extends %}` target and CSS classes against a sibling template (grep from Step 4); adjust `badge`/`panel`/`table` class names to whatever the repo actually uses (check `web/ui/templates/ui/packspec/list.html`).

- [ ] **Step 5: Add the Data Center link**

Run: `grep -rn "url 'ui:packspec_list'\|Data Center\|data_center" web/ui/templates/ui/ | grep -i "href\|url" | head`
Add a link/tile to the module near the packspec link in the Data Center template found above:

```html
<a href="{% url 'ui:producer_dims' %}" class="tile">Wymiary producenta</a>
```

- [ ] **Step 6: Run report test + system check**

Run: `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py check`
Expected: no issues.
Run: `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests.test_producer_dims -v 1`
Expected: PASS (all classes).

- [ ] **Step 7: Full suite + commit**

Run: `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests --parallel auto`
Expected: OK.

```bash
git add web/ui/views/producer_dims.py web/ui/templates/ui/producer_dims/ web/ui/tests/test_producer_dims.py web/ui/templates/ui/
git commit -m "feat(producer-dims): raport z odznakami + link w Data Center"
```

---

## Self-Review

**Spec coverage:**
- Model (batch+row, reactivation) → Task 1. ✓
- `compare_dims` sorted-triple + tolerance max(1cm,5%) + no_data → Task 2. ✓
- `ours_dims` instruction→MARM fallback → Task 2. ✓
- Multi-file positional import, header sanity-check, resolve_product_code, ImportRun.record → Task 3. ✓
- Auto-Task on import, dedup REF+supplier, only mismatch, owner_users → Task 3. ✓
- Report page with badges + filters + pagination → Task 4. ✓
- Data Center link, `ui:` URLs, `@_master_data`, `__all__` → Tasks 3–4. ✓
- YAGNI (no NAKE_KAR import, box/pouch stored not compared, no XLSX export/write-back) → respected. ✓

**Placeholder scan:** none — all steps carry real code/commands.

**Type consistency:** `compare_dims(producer_lwh, ours_lwh) -> (verdict, delta)`, `ours_dims(product) -> (l,w,h)|None`, `_ingest_file(f, user) -> batch`, `_raise_mismatch_tasks(batch) -> int`, `_raise_task(..., category=...)` — consistent across Tasks 2–4. `carton_dim_mismatch` category and `producer_dims` kind consistent between Task 1 (register) and Task 3 (use).

**Note on Task 3/4 coupling:** `__all__` and the `ui:producer_dims` URL reference `producer_dims_list` (Task 4). Execute Tasks 3 and 4 back-to-back; if isolating Task 3, temporarily drop the list name from `__all__` and comment the list URL (noted inline).
