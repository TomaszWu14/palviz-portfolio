# Optymalizacja kartonów — Projekty A/B: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Formalny „Projekt A/B" w module carton_opt: A = zamrożony snapshot wymiarów + wgrany render, B = ręcznie projektowane wymiary, porównanie metryk na żywo + 3D side-by-side. Wynik = dokumentacja (master data nietykana).

**Architecture:** Nowy model `PackagingRedesign` w `web/ui/models.py`; widoki w istniejącym `web/ui/views/carton_opt.py` (reuse `_variant_fill` i dekoratorów `@module_required("carton_opt")`/`@_optimizer`); dwa szablony w `web/ui/templates/ui/carton_opt/`; metryki na żywo przez endpoint JSON (układ palety liczy `PalletCalculator` po stronie serwera); 3D przez istniejący `renderPalVizLevel` (wspiera `glb_url`).

**Tech Stack:** Django 5 (unittest runner), palletizer (framework-free), three.js vendored (`palviz-three.js`).

## Global Constraints

- Polish-first UI: wszystkie etykiety/komunikaty/verbose_name po polsku (CLAUDE.md).
- Komentarze szablonów `{# #}` MUSZĄ być jednolinijkowe (guard-test w CI łapie wielolinijkowe).
- Nowe widoki: dopisać do `__all__` modułu i do `web/ui/urls.py` (namespace `ui:`); inaczej nieosiągalne.
- Migracje: `makemigrations` po zmianie modeli; nigdy nie edytować już zmerdżowanych.
- Testy uruchamiane z `web/`: `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test <moduł> -v 1`.
- Wersja A niemutowalna po utworzeniu (snapshot JSON, nie żywe odczyty master daty).
- Status `accepted` blokuje edycję B i podmianę renderu.
- Render A: tylko `.glb` / `.png` / `.jpg`, limit 8 MB (jak `_ART_MAX_BYTES`).
- Branch roboczy: `claude/carton-opt-ab-redesign` (spec już na nim scommitowany).

---

### Task 1: Model `PackagingRedesign` + migracja

**Files:**
- Modify: `web/ui/models.py` (po klasie `CartonAlternative`, ~linia 1880)
- Create: `web/ui/migrations/` (via makemigrations)
- Test: `web/ui/tests/test_carton_opt_redesign.py`

**Interfaces:**
- Produces: model `ui.models.PackagingRedesign` z polami jak niżej oraz classmethod `PackagingRedesign.create_for(product, scope, user)` budującym snapshot A; helper `a` (property zwracająca dict snapshotu).

- [ ] **Step 1: Failing test — snapshot A zamrożony**

```python
# web/ui/tests/test_carton_opt_redesign.py
"""Projekty A/B (carton_opt): snapshot A zamrożony, metryki, uprawnienia, blokada po akceptacji."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import PackagingRedesign, PalletizationInstruction, Product
from ui.roles import GROUP_OPTIMIZER, GROUP_VIEWER


def _product_with_instr(code="RD-1"):
    p = Product.objects.create(code=code, name="X", unit_length_cm=10,
                               unit_width_cm=8, unit_height_cm=5)
    PalletizationInstruction.objects.create(
        product=p, version=1, is_active=True, unit_weight=0.5, pcs_per_carton=24,
        units_per_piece=1, carton_l=40, carton_w=30, carton_h=25,
        pallet_code="EU", pallet_length_cm=120, pallet_width_cm=80,
        pallet_base_height_cm=15, max_height_total_cm=200, demand_pcs=1000)
    return p


class SnapshotTests(TestCase):
    def test_snapshot_frozen_against_master_data_change(self):
        p = _product_with_instr()
        rd = PackagingRedesign.create_for(p, scope="oba", user=None)
        self.assertEqual(rd.a["carton_l"], 40)
        self.assertEqual(rd.a["unit_l"], 10)
        self.assertEqual(rd.a["pcs_per_carton"], 24)
        # Zmiana master daty PO utworzeniu nie zmienia A.
        instr = p.latest_instruction()
        instr.carton_l = 99
        instr.save()
        p.unit_length_cm = 77
        p.save()
        rd.refresh_from_db()
        self.assertEqual(rd.a["carton_l"], 40)
        self.assertEqual(rd.a["unit_l"], 10)
```

- [ ] **Step 2: Run — expect FAIL** (`ImportError: cannot import name 'PackagingRedesign'`)

Run (z `web/`): `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests.test_carton_opt_redesign -v 1`

- [ ] **Step 3: Model** — w `web/ui/models.py`, bezpośrednio po klasie `CartonAlternative`:

```python
class PackagingRedesign(models.Model):
    """Projekt A/B przeprojektowania opakowania (moduł carton_opt): A = zamrożony
    snapshot stanu obecnego (wymiary + render wgrany przez użytkownika), B = docelowe
    wymiary projektowane ręcznie. Wynik = dokumentacja/porównanie — master data
    nie jest zmieniana automatycznie (wdrożenie fizyczne to osobny, ręczny krok)."""
    SCOPE = [("op", "Opakowanie (OP)"), ("karton", "Karton"), ("oba", "OP + karton")]
    STATUS = [("draft", "Szkic"), ("accepted", "Zaakceptowany"), ("rejected", "Odrzucony")]
    product = models.ForeignKey(Product, on_delete=models.CASCADE,
                                related_name="redesigns", verbose_name="Indeks")
    scope = models.CharField(max_length=8, choices=SCOPE, verbose_name="Zakres")
    # Wersja A — snapshot master daty z chwili utworzenia; NIGDY nie czytana na żywo.
    a_snapshot = models.JSONField(verbose_name="Wersja A (snapshot)")
    a_render = models.FileField(upload_to="redesigns/", blank=True, null=True,
                                validators=[FileExtensionValidator(["glb", "png", "jpg", "jpeg"])],
                                verbose_name="Render wersji A")
    # Wersja B — edytowalna do akceptacji; tylko pola objęte zakresem.
    b_op_l = models.FloatField(null=True, blank=True, verbose_name="B: L opakowania [cm]")
    b_op_w = models.FloatField(null=True, blank=True, verbose_name="B: W opakowania [cm]")
    b_op_h = models.FloatField(null=True, blank=True, verbose_name="B: H opakowania [cm]")
    b_carton_l = models.FloatField(null=True, blank=True, verbose_name="B: L kartonu [cm]")
    b_carton_w = models.FloatField(null=True, blank=True, verbose_name="B: W kartonu [cm]")
    b_carton_h = models.FloatField(null=True, blank=True, verbose_name="B: H kartonu [cm]")
    b_pcs_per_carton = models.PositiveIntegerField(null=True, blank=True,
                                                   verbose_name="B: szt/karton")
    b_units_per_pack = models.PositiveIntegerField(null=True, blank=True,
                                                   verbose_name="B: szt/OP")
    annual_volume_pcs = models.PositiveIntegerField(null=True, blank=True,
                                                    verbose_name="Wolumen roczny [szt]")
    status = models.CharField(max_length=10, choices=STATUS, default="draft",
                              db_index=True, verbose_name="Status")
    notes = models.TextField(max_length=1000, blank=True, verbose_name="Notatki")
    created_by = models.ForeignKey("auth.User", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="redesigns")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Projekt A/B opakowania"
        verbose_name_plural = "Projekty A/B opakowań"

    def __str__(self):
        return f"A/B {self.product.code} ({self.get_scope_display()}, {self.status})"

    @property
    def a(self):
        return self.a_snapshot or {}

    @classmethod
    def create_for(cls, product, scope, user):
        """Załóż projekt: snapshot A z bieżącej master daty (jedyny moment odczytu)."""
        instr = product.latest_instruction()
        snap = {
            "unit_l": product.unit_length_cm, "unit_w": product.unit_width_cm,
            "unit_h": product.unit_height_cm,
            "carton_l": getattr(instr, "carton_l", None),
            "carton_w": getattr(instr, "carton_w", None),
            "carton_h": getattr(instr, "carton_h", None),
            "pcs_per_carton": getattr(instr, "pcs_per_carton", None),
            "units_per_piece": getattr(instr, "units_per_piece", None),
        }
        return cls.objects.create(product=product, scope=scope,
                                  a_snapshot=snap, created_by=user)
```

- [ ] **Step 4: Migracja**

Run (z `web/`): `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py makemigrations ui`
Expected: `+ Create model PackagingRedesign`

- [ ] **Step 5: Run test — PASS**, potem commit

```bash
git add web/ui/models.py web/ui/migrations/ web/ui/tests/test_carton_opt_redesign.py
git commit -m "feat(carton-opt): model PackagingRedesign — projekt A/B z zamrożonym snapshotem"
```

---

### Task 2: Metryki A|B|Δ + endpoint JSON

**Files:**
- Modify: `web/ui/views/carton_opt.py` (na końcu pliku, przed `__all__` jeśli jest)
- Modify: `web/ui/urls.py` (po linii 409, blok `optymalizacja/`)
- Test: `web/ui/tests/test_carton_opt_redesign.py` (dopisz klasę)

**Interfaces:**
- Consumes: `_variant_fill(l, w, h, instr)` → `{fill, cartons_per_pallet, fits, error}` (istnieje, linia ~141); `PackagingRedesign` z Task 1.
- Produces: `redesign_metrics(rd, b=None)` → dict `{"a": {...}, "b": {...}, "delta": {...}}`, gdzie każda strona ma klucze `fill`, `cartons_per_pallet`, `pcs_per_pallet`, `carton_m3`, `pallets_per_year`; endpoint `ui:carton_opt_redesign_metrics` (GET, `?pk=<id>&b_carton_l=...&b_pcs_per_carton=...`) zwracający ten dict jako JSON.

- [ ] **Step 1: Failing test**

```python
class MetricsTests(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_user(username="opt", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_OPTIMIZER)[0])
        self.client.force_login(u)
        self.p = _product_with_instr("RD-M")
        self.rd = PackagingRedesign.objects.filter(product=self.p).first() \
            or PackagingRedesign.create_for(self.p, scope="karton", user=None)
        self.rd.annual_volume_pcs = 100_000
        self.rd.save()

    def test_metrics_endpoint_b_better(self):
        # B: niższy karton (40×30×20, 24 szt) → więcej warstw → więcej kartonów/paletę.
        r = self.client.get(reverse("ui:carton_opt_redesign_metrics"),
                            {"pk": self.rd.pk, "b_carton_l": 40, "b_carton_w": 30,
                             "b_carton_h": 20, "b_pcs_per_carton": 24})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertGreater(d["b"]["cartons_per_pallet"], d["a"]["cartons_per_pallet"])
        self.assertGreater(d["a"]["pallets_per_year"], d["b"]["pallets_per_year"])
        self.assertAlmostEqual(d["a"]["carton_m3"], 0.03, places=3)   # 40×30×25 cm
        self.assertIn("fill", d["delta"])
```

- [ ] **Step 2: Run — FAIL** (`NoReverseMatch: carton_opt_redesign_metrics`)

- [ ] **Step 3: Implementacja** — w `web/ui/views/carton_opt.py` (import `JsonResponse` i `PackagingRedesign` przychodzą z `from .core import *` / `..models`; sprawdź nagłówek pliku i dopisz `PackagingRedesign` do importu z `..models`):

```python
# ── Projekty A/B (redesign opakowań) — metryki A|B|Δ ────────────────────────────

def _side_metrics(instr, carton_l, carton_w, carton_h, pcs_per_carton, annual):
    """Metryki jednej strony (A albo B) na wspólnej konfiguracji palety instrukcji."""
    if not (carton_l and carton_w and carton_h):
        return {"fill": None, "cartons_per_pallet": None, "pcs_per_pallet": None,
                "carton_m3": None, "pallets_per_year": None, "error": "Brak wymiarów"}
    vf = _variant_fill(carton_l, carton_w, carton_h, instr)
    cpp = vf["cartons_per_pallet"]
    pcs = pcs_per_carton or (instr.pcs_per_carton if instr else None)
    pcs_pp = (cpp * pcs) if (cpp and pcs) else None
    m3 = round(carton_l * carton_w * carton_h / 1_000_000, 5)
    per_year = (-(-annual // pcs_pp)) if (annual and pcs_pp) else None   # ceil-div
    # ponytail: 33 palety/auto na sztywno — konfigurowalna stała, gdy ktoś poprosi.
    trucks = (-(-per_year // 33)) if per_year else None
    return {"fill": vf["fill"], "cartons_per_pallet": cpp, "pcs_per_pallet": pcs_pp,
            "carton_m3": m3, "pallets_per_year": per_year,
            "trucks_per_year": trucks, "error": vf["error"]}


def redesign_metrics(rd, b=None):
    """A|B|Δ dla projektu; `b` = dict nadpisujący pola B (podgląd na żywo bez zapisu)."""
    instr = rd.product.latest_instruction()
    a = rd.a
    b = b or {}
    def _bv(key, fallback):
        v = b.get(key)
        return v if v not in (None, "") else fallback
    # Zakres „op": karton zostaje z A (spec) — wymiary B kartonu ignorowane.
    if rd.scope == "op":
        b_cl, b_cw, b_ch = a.get("carton_l"), a.get("carton_w"), a.get("carton_h")
    else:
        b_cl = _bv("b_carton_l", rd.b_carton_l)
        b_cw = _bv("b_carton_w", rd.b_carton_w)
        b_ch = _bv("b_carton_h", rd.b_carton_h)
    b_pcs = _bv("b_pcs_per_carton", rd.b_pcs_per_carton)
    annual = rd.annual_volume_pcs
    side_a = _side_metrics(instr, a.get("carton_l"), a.get("carton_w"),
                           a.get("carton_h"), a.get("pcs_per_carton"), annual)
    side_b = _side_metrics(instr, b_cl, b_cw, b_ch, b_pcs, annual)
    delta = {k: (round(side_b[k] - side_a[k], 3)
                 if isinstance(side_a.get(k), (int, float))
                 and isinstance(side_b.get(k), (int, float)) else None)
             for k in ("fill", "cartons_per_pallet", "pcs_per_pallet",
                       "carton_m3", "pallets_per_year", "trucks_per_year")}
    return {"a": side_a, "b": side_b, "delta": delta}


@module_required("carton_opt")
def carton_opt_redesign_metrics(request):
    """Podgląd metryk na żywo (GET, bez zapisu) — B z parametrów zapytania."""
    rd = get_object_or_404(PackagingRedesign, pk=request.GET.get("pk"))
    def _f(name):
        try:
            return float(request.GET[name])
        except (KeyError, ValueError):
            return None
    b = {"b_carton_l": _f("b_carton_l"), "b_carton_w": _f("b_carton_w"),
         "b_carton_h": _f("b_carton_h"),
         "b_pcs_per_carton": int(_f("b_pcs_per_carton") or 0) or None}
    return JsonResponse(redesign_metrics(rd, b))
```

URL w `web/ui/urls.py` (za linią `carton_opt_set_status`):

```python
    path("optymalizacja/ab/metryki/", views.carton_opt_redesign_metrics,
         name="carton_opt_redesign_metrics"),
```

- [ ] **Step 4: Run test — PASS** (upewnij się, że `carton_opt_redesign_metrics` jest eksportowane: moduł nie ma `__all__`, więc `from .carton_opt import *` łapie funkcje bez podkreślenia — nazwa jest bez `_`, OK)

- [ ] **Step 5: Commit**

```bash
git add web/ui/views/carton_opt.py web/ui/urls.py web/ui/tests/test_carton_opt_redesign.py
git commit -m "feat(carton-opt): metryki A|B|delta + endpoint JSON podgladu na zywo"
```

---

### Task 3: Widoki — lista, tworzenie, edycja B, render, akceptacja

**Files:**
- Modify: `web/ui/views/carton_opt.py`
- Modify: `web/ui/urls.py`
- Test: `web/ui/tests/test_carton_opt_redesign.py` (dopisz klasę)

**Interfaces:**
- Consumes: `PackagingRedesign` (Task 1), `redesign_metrics` (Task 2), dekoratory `@module_required("carton_opt")` / `@_optimizer`, `_ART_MAX_BYTES` (z `.core`).
- Produces: widoki `carton_opt_redesigns` (lista GET), `carton_opt_redesign_new` (POST: `product_id` albo `ref_code`, `scope`), `carton_opt_redesign_detail` (GET pk), `carton_opt_redesign_save` (POST pk: pola B + notes + annual_volume_pcs + opcjonalny plik `a_render` + akcja `action` ∈ save/accept/reject); URL-e `ui:carton_opt_redesigns`, `ui:carton_opt_redesign_new`, `ui:carton_opt_redesign_detail`, `ui:carton_opt_redesign_save`.

- [ ] **Step 1: Failing testy**

```python
class ViewTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.opt = U.objects.create_user(username="opt2", password="x")
        self.opt.groups.add(Group.objects.get_or_create(name=GROUP_OPTIMIZER)[0])
        self.viewer = U.objects.create_user(username="view", password="x")
        self.viewer.groups.add(Group.objects.get_or_create(name=GROUP_VIEWER)[0])
        self.p = _product_with_instr("RD-V")
        self.client.force_login(self.opt)

    def _create(self):
        self.client.post(reverse("ui:carton_opt_redesign_new"),
                         {"ref_code": "RD-V", "scope": "karton"})
        return PackagingRedesign.objects.get(product=self.p)

    def test_create_save_accept_locks_b(self):
        rd = self._create()
        self.assertEqual(rd.a["carton_l"], 40)              # snapshot przy tworzeniu
        url = reverse("ui:carton_opt_redesign_save", args=[rd.pk])
        self.client.post(url, {"action": "save", "b_carton_l": 40, "b_carton_w": 30,
                               "b_carton_h": 20, "b_pcs_per_carton": 24})
        rd.refresh_from_db()
        self.assertEqual(rd.b_carton_h, 20)
        self.client.post(url, {"action": "accept"})
        rd.refresh_from_db()
        self.assertEqual(rd.status, "accepted")
        # Po akceptacji edycja B odbita.
        self.client.post(url, {"action": "save", "b_carton_h": 99})
        rd.refresh_from_db()
        self.assertEqual(rd.b_carton_h, 20)

    def test_viewer_cannot_write(self):
        rd = self._create()
        self.client.force_login(self.viewer)
        r = self.client.post(reverse("ui:carton_opt_redesign_save", args=[rd.pk]),
                             {"action": "save", "b_carton_h": 10})
        self.assertIn(r.status_code, (302, 403))
        rd.refresh_from_db()
        self.assertIsNone(rd.b_carton_h)

    def test_render_upload_validates_extension(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        rd = self._create()
        url = reverse("ui:carton_opt_redesign_save", args=[rd.pk])
        self.client.post(url, {"action": "save",
                               "a_render": SimpleUploadedFile("x.exe", b"MZ")})
        rd.refresh_from_db()
        self.assertFalse(rd.a_render)
        self.client.post(url, {"action": "save",
                               "a_render": SimpleUploadedFile("a.glb", b"glTF\x02\x00")})
        rd.refresh_from_db()
        self.assertTrue(rd.a_render.name.endswith(".glb"))
```

- [ ] **Step 2: Run — FAIL** (`NoReverseMatch`)

- [ ] **Step 3: Widoki** — dopisz w `web/ui/views/carton_opt.py`:

```python
@module_required("carton_opt")
def carton_opt_redesigns(request):
    """Lista projektów A/B + formularz założenia nowego."""
    rows = []
    for rd in PackagingRedesign.objects.select_related("product")[:200]:
        m = redesign_metrics(rd)
        rows.append({"rd": rd, "fill_a": m["a"]["fill"], "fill_b": m["b"]["fill"]})
    return render(request, "ui/carton_opt/redesigns.html", {"rows": rows})


@_optimizer
@require_POST
def carton_opt_redesign_new(request):
    """Załóż projekt A/B: indeks po product_id (ze skrzynki) albo ref_code (z listy)."""
    from .. import product_codes
    pid = request.POST.get("product_id")
    product = (Product.objects.filter(pk=pid).first() if (pid or "").isdigit()
               else product_codes.resolve_product_code(
                   (request.POST.get("ref_code") or "").strip()))
    scope = request.POST.get("scope") or "oba"
    if product is None or scope not in dict(PackagingRedesign.SCOPE):
        messages.error(request, "Podaj istniejący indeks i zakres projektu.")
        return redirect("ui:carton_opt_redesigns")
    rd = PackagingRedesign.create_for(product, scope=scope, user=request.user)
    messages.success(request, f"Projekt A/B dla {product.code} założony — wersja A zamrożona.")
    return redirect("ui:carton_opt_redesign_detail", pk=rd.pk)


@module_required("carton_opt")
def carton_opt_redesign_detail(request, pk):
    rd = get_object_or_404(PackagingRedesign.objects.select_related("product"), pk=pk)
    return render(request, "ui/carton_opt/redesign_detail.html", {
        "rd": rd, "metrics": redesign_metrics(rd),
        "locked": rd.status == "accepted",
    })


@_optimizer
@require_POST
def carton_opt_redesign_save(request, pk):
    """Zapis B / akceptacja / odrzucenie. Po akceptacji B i render są tylko-do-odczytu."""
    rd = get_object_or_404(PackagingRedesign, pk=pk)
    action = request.POST.get("action") or "save"
    if action == "reject":
        rd.status = "rejected"
        rd.save(update_fields=["status"])
        messages.info(request, "Projekt odrzucony.")
        return redirect("ui:carton_opt_redesign_detail", pk=pk)
    if rd.status == "accepted":
        messages.error(request, "Projekt zaakceptowany — wersja B jest zablokowana.")
        return redirect("ui:carton_opt_redesign_detail", pk=pk)
    if action == "accept":
        rd.status = "accepted"
        rd.save(update_fields=["status"])
        messages.success(request, "Projekt zaakceptowany — B i render zablokowane.")
        return redirect("ui:carton_opt_redesign_detail", pk=pk)
    # action == "save": pola B (tylko podane), notatki, wolumen, render.
    def _f(name):
        raw = (request.POST.get(name) or "").replace(",", ".")
        try:
            v = float(raw)
            return v if v > 0 else None
        except ValueError:
            return None
    for f in ("b_op_l", "b_op_w", "b_op_h", "b_carton_l", "b_carton_w", "b_carton_h"):
        if f in request.POST:
            setattr(rd, f, _f(f))
    for f in ("b_pcs_per_carton", "b_units_per_pack", "annual_volume_pcs"):
        if f in request.POST:
            v = _f(f)
            setattr(rd, f, int(v) if v else None)
    if "notes" in request.POST:
        rd.notes = (request.POST.get("notes") or "")[:1000]
    up = request.FILES.get("a_render")
    if up is not None:
        name = (up.name or "").lower()
        if up.size > _ART_MAX_BYTES:
            messages.error(request, "Render za duży (max 8 MB).")
            return redirect("ui:carton_opt_redesign_detail", pk=pk)
        if not name.endswith((".glb", ".png", ".jpg", ".jpeg")):
            messages.error(request, "Render: dozwolone .glb / PNG / JPG.")
            return redirect("ui:carton_opt_redesign_detail", pk=pk)
        rd.a_render = up
    rd.save()
    messages.success(request, "Szkic zapisany.")
    return redirect("ui:carton_opt_redesign_detail", pk=pk)
```

URL-e (obok metryk):

```python
    path("optymalizacja/ab/", views.carton_opt_redesigns, name="carton_opt_redesigns"),
    path("optymalizacja/ab/nowy/", views.carton_opt_redesign_new,
         name="carton_opt_redesign_new"),
    path("optymalizacja/ab/<int:pk>/", views.carton_opt_redesign_detail,
         name="carton_opt_redesign_detail"),
    path("optymalizacja/ab/<int:pk>/zapisz/", views.carton_opt_redesign_save,
         name="carton_opt_redesign_save"),
```

Import modelu: w nagłówku `carton_opt.py` dopisz `PackagingRedesign` do istniejącego `from ..models import ...`.

- [ ] **Step 4: Run testy Task 3 — PASS** (detail zwróci 500 dopóki nie ma szablonu — testy Task 3 nie GET-ują detail; szablony w Task 4)

- [ ] **Step 5: Commit**

```bash
git add web/ui/views/carton_opt.py web/ui/urls.py web/ui/tests/test_carton_opt_redesign.py
git commit -m "feat(carton-opt): widoki projektow A/B — lista, tworzenie, zapis B, akceptacja, render"
```

---

### Task 4: Szablony — lista + ekran A|B z metrykami na żywo i 3D

**Files:**
- Create: `web/ui/templates/ui/carton_opt/redesigns.html`
- Create: `web/ui/templates/ui/carton_opt/redesign_detail.html`
- Test: `web/ui/tests/test_carton_opt_redesign.py` (dopisz smoke)

**Interfaces:**
- Consumes: kontekst z Task 3 (`rows` na liście; `rd`, `metrics`, `locked` na detail); `renderPalVizLevel(canvas, data)` z `/static/ui/js/palviz-three.js` (moduł ES; data: `{type:"box", l,w,h, label, color, glb_url?}`); endpoint `ui:carton_opt_redesign_metrics`.
- Produces: strony pod `ui:carton_opt_redesigns` i `ui:carton_opt_redesign_detail`.

- [ ] **Step 1: Failing smoke testy**

```python
class TemplateSmokeTests(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_user(username="opt3", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_OPTIMIZER)[0])
        self.client.force_login(u)
        self.p = _product_with_instr("RD-T")
        self.rd = PackagingRedesign.create_for(self.p, scope="oba", user=u)

    def test_list_and_detail_render(self):
        r = self.client.get(reverse("ui:carton_opt_redesigns"))
        self.assertContains(r, "RD-T")
        r = self.client.get(reverse("ui:carton_opt_redesign_detail", args=[self.rd.pk]))
        self.assertContains(r, "WERSJA OBECNA")
        self.assertContains(r, "canvas-a")
        self.assertContains(r, "canvas-b")
        self.assertNotContains(r, "{# ")          # guard: komentarze jednolinijkowe
```

- [ ] **Step 2: Run — FAIL** (`TemplateDoesNotExist`)

- [ ] **Step 3: `redesigns.html`** (wzoruj strukturę na `variants.html` — `{% extends "ui/base.html" %}`):

```html
{% extends "ui/base.html" %}
{% block title %}Projekty A/B — Optymalizacja kartonów — {{ app_name }}{% endblock %}
{% block content %}
<nav class="breadcrumb"><a href="{% url 'ui:carton_opt_inbox' %}">Optymalizacja kartonów</a> › <span>Projekty A/B</span></nav>
<div class="page-header">
  <div>
    <div class="page-header__title">Projekty A/B opakowań</div>
    <div class="page-header__sub">A = stan obecny (zamrożony) · B = wersja docelowa</div>
  </div>
</div>
{# Nowy projekt: indeks + zakres. #}
<div class="card" style="margin-bottom:14px;padding:14px 18px">
  <form method="post" action="{% url 'ui:carton_opt_redesign_new' %}"
        style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    {% csrf_token %}
    <input type="text" name="ref_code" required placeholder="Indeks (np. DMOM10001)"
           style="flex:1 1 200px;min-width:170px;font-family:monospace">
    <select name="scope" style="width:auto">
      <option value="oba">OP + karton</option>
      <option value="op">Opakowanie (OP)</option>
      <option value="karton">Karton</option>
    </select>
    <button type="submit" class="btn btn-primary">+ Nowy projekt</button>
  </form>
</div>
<div class="card">
  <div class="table-wrap"><table class="table">
    <thead><tr><th>Indeks</th><th>Zakres</th><th>Status</th>
      <th class="text-right">Wypełnienie A → B</th><th>Data</th></tr></thead>
    <tbody>
    {% for r in rows %}
      <tr>
        <td><a href="{% url 'ui:carton_opt_redesign_detail' r.rd.pk %}"
               class="font-semibold" style="font-family:monospace">{{ r.rd.product.code }}</a></td>
        <td>{{ r.rd.get_scope_display }}</td>
        <td><span class="badge {% if r.rd.status == 'accepted' %}badge-green{% elif r.rd.status == 'rejected' %}badge-red{% else %}badge-gray{% endif %}">{{ r.rd.get_status_display }}</span></td>
        <td class="text-right">{{ r.fill_a|default:"—" }}% → {{ r.fill_b|default:"—" }}%</td>
        <td class="text-muted">{{ r.rd.created_at|date:"Y-m-d" }}</td>
      </tr>
    {% empty %}
      <tr><td colspan="5"><div class="empty-state" style="padding:24px">
        <div class="empty-state__title">Brak projektów</div></div></td></tr>
    {% endfor %}
    </tbody>
  </table></div>
</div>
{% endblock %}
```

- [ ] **Step 4: `redesign_detail.html`** — dwie kolumny + metryki na żywo + 3D:

```html
{% extends "ui/base.html" %}
{% block title %}A/B {{ rd.product.code }} — {{ app_name }}{% endblock %}
{% block content %}
<nav class="breadcrumb"><a href="{% url 'ui:carton_opt_redesigns' %}">Projekty A/B</a> › <span>{{ rd.product.code }}</span></nav>
<div class="page-header">
  <div>
    <div class="page-header__title" style="font-family:monospace">{{ rd.product.code }}</div>
    <div class="page-header__sub">{{ rd.product.name }} · zakres: {{ rd.get_scope_display }} ·
      <span class="badge {% if locked %}badge-green{% else %}badge-gray{% endif %}">{{ rd.get_status_display }}</span></div>
  </div>
</div>

<form method="post" action="{% url 'ui:carton_opt_redesign_save' rd.pk %}"
      enctype="multipart/form-data" id="rd-form">
{% csrf_token %}
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
  {# ── Kolumna A: stan obecny, zamrożony. #}
  <div class="card" style="padding:16px 18px">
    <span class="badge badge-gray">WERSJA OBECNA — niezmienna</span>
    <div style="width:100%;height:220px;border-radius:8px;overflow:hidden;margin:10px 0;background:radial-gradient(120% 120% at 50% 34%, #3d4d6b 0%, #16203a 55%, #0b1220 100%)">
      <canvas id="canvas-a" width="400" height="220" style="width:100%;height:100%"></canvas>
    </div>
    <table class="table">
      <tr><td>Karton</td><td class="text-right" style="font-family:monospace">{{ rd.a.carton_l|default:"—" }}×{{ rd.a.carton_w|default:"—" }}×{{ rd.a.carton_h|default:"—" }} cm</td></tr>
      <tr><td>OP</td><td class="text-right" style="font-family:monospace">{{ rd.a.unit_l|default:"—" }}×{{ rd.a.unit_w|default:"—" }}×{{ rd.a.unit_h|default:"—" }} cm</td></tr>
      <tr><td>Szt/karton</td><td class="text-right">{{ rd.a.pcs_per_carton|default:"—" }}</td></tr>
    </table>
    {% if not locked %}
    <div class="form-group" style="margin-top:8px">
      <label>Render wersji A (.glb / PNG / JPG)</label>
      <input type="file" name="a_render" accept=".glb,image/png,image/jpeg">
      {% if rd.a_render %}<span class="help-text">Wgrany: {{ rd.a_render.name }}</span>{% endif %}
    </div>
    {% endif %}
  </div>
  {# ── Kolumna B: docelowa, edytowalna do akceptacji. #}
  <div class="card" style="padding:16px 18px">
    <span class="badge badge-blue">WERSJA DOCELOWA (B)</span>
    <div style="width:100%;height:220px;border-radius:8px;overflow:hidden;margin:10px 0;background:radial-gradient(120% 120% at 50% 34%, #3d4d6b 0%, #16203a 55%, #0b1220 100%)">
      <canvas id="canvas-b" width="400" height="220" style="width:100%;height:100%"></canvas>
    </div>
    {% if rd.scope != 'op' %}
    <div style="display:flex;gap:8px">
      <div class="form-group"><label>Karton L</label><input type="number" step="0.1" name="b_carton_l" value="{{ rd.b_carton_l|default_if_none:'' }}" {% if locked %}disabled{% endif %} class="rd-b"></div>
      <div class="form-group"><label>W</label><input type="number" step="0.1" name="b_carton_w" value="{{ rd.b_carton_w|default_if_none:'' }}" {% if locked %}disabled{% endif %} class="rd-b"></div>
      <div class="form-group"><label>H</label><input type="number" step="0.1" name="b_carton_h" value="{{ rd.b_carton_h|default_if_none:'' }}" {% if locked %}disabled{% endif %} class="rd-b"></div>
    </div>
    <div class="form-group"><label>Szt/karton (B)</label><input type="number" name="b_pcs_per_carton" value="{{ rd.b_pcs_per_carton|default_if_none:'' }}" {% if locked %}disabled{% endif %} class="rd-b"></div>
    {% endif %}
    {% if rd.scope != 'karton' %}
    <div style="display:flex;gap:8px">
      <div class="form-group"><label>OP L</label><input type="number" step="0.1" name="b_op_l" value="{{ rd.b_op_l|default_if_none:'' }}" {% if locked %}disabled{% endif %}></div>
      <div class="form-group"><label>W</label><input type="number" step="0.1" name="b_op_w" value="{{ rd.b_op_w|default_if_none:'' }}" {% if locked %}disabled{% endif %}></div>
      <div class="form-group"><label>H</label><input type="number" step="0.1" name="b_op_h" value="{{ rd.b_op_h|default_if_none:'' }}" {% if locked %}disabled{% endif %}></div>
    </div>
    <div class="form-group"><label>Szt/OP (B)</label><input type="number" name="b_units_per_pack" value="{{ rd.b_units_per_pack|default_if_none:'' }}" {% if locked %}disabled{% endif %}></div>
    {% endif %}
    <div class="form-group"><label>Wolumen roczny [szt]</label><input type="number" name="annual_volume_pcs" value="{{ rd.annual_volume_pcs|default_if_none:'' }}" {% if locked %}disabled{% endif %} class="rd-b"></div>
  </div>
</div>

{# ── Pasek porównania A | B | Δ (aktualizowany na żywo endpointem metryk). #}
<div class="card" style="margin-top:16px;padding:14px 18px">
  <div class="table-wrap"><table class="table" id="rd-metrics">
    <thead><tr><th>Metryka</th><th class="text-right">A</th><th class="text-right">B</th><th class="text-right">Δ</th></tr></thead>
    <tbody>
      <tr data-k="fill"><td>Wypełnienie palety %</td><td class="text-right" data-side="a"></td><td class="text-right" data-side="b"></td><td class="text-right" data-side="delta"></td></tr>
      <tr data-k="cartons_per_pallet"><td>Kartonów / paletę</td><td class="text-right" data-side="a"></td><td class="text-right" data-side="b"></td><td class="text-right" data-side="delta"></td></tr>
      <tr data-k="pcs_per_pallet"><td>Szt / paletę</td><td class="text-right" data-side="a"></td><td class="text-right" data-side="b"></td><td class="text-right" data-side="delta"></td></tr>
      <tr data-k="carton_m3"><td>m³ kartonu</td><td class="text-right" data-side="a"></td><td class="text-right" data-side="b"></td><td class="text-right" data-side="delta"></td></tr>
      <tr data-k="pallets_per_year"><td>Palet / rok</td><td class="text-right" data-side="a"></td><td class="text-right" data-side="b"></td><td class="text-right" data-side="delta"></td></tr>
      <tr data-k="trucks_per_year"><td>Kursów / rok (33 pal./auto)</td><td class="text-right" data-side="a"></td><td class="text-right" data-side="b"></td><td class="text-right" data-side="delta"></td></tr>
    </tbody>
  </table></div>
</div>

<div class="form-group" style="margin-top:12px"><label>Notatki</label>
  <textarea name="notes" rows="2" maxlength="1000" {% if locked %}disabled{% endif %}>{{ rd.notes }}</textarea></div>
<div style="display:flex;gap:10px;margin-top:8px">
  {% if not locked %}
  <button type="submit" name="action" value="save" class="btn btn-secondary">Zapisz szkic</button>
  <button type="submit" name="action" value="accept" class="btn btn-primary"
          onclick="return confirm('Zaakceptować projekt? Wersja B zostanie zablokowana.')">Akceptuj</button>
  <button type="submit" name="action" value="reject" class="btn btn-secondary">Odrzuć</button>
  {% endif %}
</div>
</form>

{{ metrics|json_script:"rd-initial" }}
{{ rd.a_snapshot|json_script:"rd-a" }}
<script type="importmap">{ "imports": { "three": "/static/ui/vendor/three.module.js" } }</script>
<script type="module">
import { renderPalVizLevel } from "/static/ui/js/palviz-three.js";
const a = JSON.parse(document.getElementById("rd-a").textContent);
const glb = "{{ rd.a_render.url|default:'' }}";
const isGlb = glb.toLowerCase().endsWith(".glb");
// A: wgrany .glb zastępuje bryłę; inaczej bryła ze snapshotu (frameK jak zoom MATinfo).
let viewerA = null, viewerB = null;
function boxData(l, w, h, label, extra) {
  return Object.assign({type: "box", l: l || 10, w: w || 10, h: h || 10,
                        label: label, color: "#DCC4A0", frameK: 2.6, noRuler: true}, extra || {});
}
function drawA() {
  const cv = document.getElementById("canvas-a");
  viewerA = renderPalVizLevel(cv, boxData(a.carton_l, a.carton_w, a.carton_h, "A",
                                          isGlb ? {glb_url: glb} : {}));
}
function drawB() {
  const cv = document.getElementById("canvas-b");
  if (viewerB && viewerB.destroy) { try { viewerB.destroy(); } catch (e) {} }
  const v = n => parseFloat((document.querySelector(`[name=${n}]`) || {}).value) || null;
  viewerB = renderPalVizLevel(cv, boxData(v("b_carton_l") || a.carton_l,
                                          v("b_carton_w") || a.carton_w,
                                          v("b_carton_h") || a.carton_h, "B"));
}
// Metryki na żywo: debounce 400 ms → endpoint JSON (silnik liczy po stronie serwera).
const table = document.getElementById("rd-metrics");
function paint(d) {
  table.querySelectorAll("tr[data-k]").forEach(tr => {
    const k = tr.dataset.k;
    tr.querySelector('[data-side=a]').textContent = d.a[k] ?? "—";
    tr.querySelector('[data-side=b]').textContent = d.b[k] ?? "—";
    const dl = d.delta[k];
    const cell = tr.querySelector('[data-side=delta]');
    cell.textContent = dl == null ? "—" : (dl > 0 ? "+" + dl : dl);
    // Δ zielone gdy B lepsze: dla m³ i palet/rok mniej = lepiej.
    const lowerBetter = (k === "carton_m3" || k === "pallets_per_year" || k === "trucks_per_year");
    cell.style.color = dl == null ? "" : ((lowerBetter ? dl < 0 : dl > 0) ? "var(--green)" : (dl === 0 ? "" : "var(--red)"));
  });
}
paint(JSON.parse(document.getElementById("rd-initial").textContent));
let t = null;
document.querySelectorAll("input.rd-b").forEach(el => el.addEventListener("input", () => {
  clearTimeout(t);
  t = setTimeout(() => {
    const v = n => (document.querySelector(`[name=${n}]`) || {}).value || "";
    const q = new URLSearchParams({pk: "{{ rd.pk }}", b_carton_l: v("b_carton_l"),
      b_carton_w: v("b_carton_w"), b_carton_h: v("b_carton_h"),
      b_pcs_per_carton: v("b_pcs_per_carton")});
    fetch("{% url 'ui:carton_opt_redesign_metrics' %}?" + q)
      .then(r => r.json()).then(paint).catch(() => {});
    drawB();
  }, 400);
}));
drawA(); drawB();
</script>
{% endblock %}
```

UWAGA implementacyjna: dwuklik-zoom na canvasach pominięty w v1 (frameK 2.6 kadruje całość); dołożyć mechanizm z hierarchii, gdy użytkownik poprosi.

- [ ] **Step 5: Run smoke — PASS**, potem commit

```bash
git add web/ui/templates/ui/carton_opt/ web/ui/tests/test_carton_opt_redesign.py
git commit -m "feat(carton-opt): ekran projektu A/B — dwie kolumny, metryki na zywo, 3D side-by-side"
```

---

### Task 5: Wpięcie — skrzynka, subnav, pełna suita, PR

**Files:**
- Modify: `web/ui/templates/ui/carton_opt/inbox.html` (przycisk przy zgłoszeniu)
- Modify: `web/ui/templates/ui/carton_opt/dashboard.html`, `inbox.html`, `variants.html`, `redesigns.html` (wspólny pasek zakładek — jeśli moduł nie ma wspólnego include, dodaj prosty rząd linków w nagłówku każdej strony)
- Test: całość

**Interfaces:**
- Consumes: `ui:carton_opt_redesign_new` (POST `product_id`, `scope`).

- [ ] **Step 1: Przycisk w skrzynce** — w `inbox.html` przy każdym zgłoszeniu (obok zmiany statusu; znajdź pętlę po zgłoszeniach i dodaj):

```html
{% if issue.product_id %}
<form method="post" action="{% url 'ui:carton_opt_redesign_new' %}" style="display:inline">
  {% csrf_token %}
  <input type="hidden" name="product_id" value="{{ issue.product_id }}">
  <input type="hidden" name="scope" value="oba">
  <button type="submit" class="btn btn-secondary btn-sm">Utwórz projekt A/B</button>
</form>
{% endif %}
```

- [ ] **Step 2: Zakładki modułu** — w nagłówku (page-header__actions) każdej z 4 stron carton_opt linki: Skrzynka (`ui:carton_opt_inbox`) · Pilność (`ui:carton_opt_dashboard`) · Warianty (`ui:carton_opt_variants`) · **Projekty A/B** (`ui:carton_opt_redesigns`); aktywna pogrubiona. Jeśli strony już mają wzajemne linki — tylko dopisz Projekty A/B.

- [ ] **Step 3: Pełna suita**

Run (z `web/`): `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests.test_carton_opt_redesign ui.tests.test_carton_opt ui.tests.test_notif_comment -v 1`
Expected: OK. Potem `python manage.py check` → no issues.

- [ ] **Step 4: Commit + PR**

```bash
git add web/ui/templates/ui/carton_opt/
git commit -m "feat(carton-opt): wpiecie projektow A/B — przycisk w skrzynce + zakladki modulu"
git push -u origin claude/carton-opt-ab-redesign
gh pr create --base main --title "feat(carton-opt): Projekty A/B — redesign opakowan (A zamrozone + B docelowe, metryki, 3D)" --body "Wg spec docs/superpowers/specs/2026-08-20-carton-opt-ab-redesign-design.md"
```
