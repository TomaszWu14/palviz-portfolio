"""Moduł „Optymalizacja kartonów" — centrum zarządzania wypełnieniem palet.

Faza 1: skrzynka odbiorcza zgłoszeń ze skanera (PHV → PackagingIssue) o kartonach do
optymalizacji — niewypełniony / dopasować do palety / za ciężki. Operator (rola
Optymalizacja kartonów) widzi listę wg pilności, filtruje po statusie i przesuwa
status open → w przeglądzie → rozwiązane. Zgłoszenia powstają już w phv.py; tu jest
tylko ich zarządzanie (rola-scoped: wszystkie zgłoszenia, nie tylko własne).
"""
from .core import (
    module_required, get_object_or_404, JsonResponse, render, _optimizer, require_POST,
    messages, redirect, _safe_next, _build_pallet, CartonVariant, Dimensions,
    PalletCalculator, _eval_layouts, PALLET_BASE_HEIGHT_CM, _ART_MAX_BYTES
)
import json
from django.template.loader import render_to_string
from django.urls import reverse
from ..models import (Product, PackagingRedesign)

# Limit listy projektów A/B: metryki liczone per wiersz są drogie; przy przekroczeniu
# operator dostaje jawne ostrzeżenie zamiast cichego obcięcia.
REDESIGNS_LIST_LIMIT = 200

# Pilność zgłoszenia po typie — „dopasuj do palety" i „za ciężki" są operacyjnie
# pilniejsze niż samo niedopełnienie. Steruje sortowaniem i kolorem w skrzynce.
from .carton_opt_issues import _vol_fill  # noqa: F401
from .carton_opt_variants import _pallets_per_truck, _variant_fill  # noqa: F401

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
    trucks = (-(-per_year // _pallets_per_truck())) if per_year else None
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
    annual = b.get("annual_volume_pcs") or rd.annual_volume_pcs
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
         "b_pcs_per_carton": int(_f("b_pcs_per_carton") or 0) or None,
         "annual_volume_pcs": int(_f("annual_volume_pcs") or 0) or None}
    data = redesign_metrics(rd, b)
    # Live 3D + spec dla B: przelicz paletę silnikiem z bieżących wymiarów (fallback: zapis B → A).
    base = rd.product.latest_instruction()
    a = rd.a
    if rd.scope == "op":
        bl, bw, bh = a.get("carton_l"), a.get("carton_w"), a.get("carton_h")
    else:
        bl = b["b_carton_l"] or rd.b_carton_l or a.get("carton_l")
        bw = b["b_carton_w"] or rd.b_carton_w or a.get("carton_w")
        bh = b["b_carton_h"] or rd.b_carton_h or a.get("carton_h")
    pb = (_engine_pallet(base, bl, bw, bh) if (base and bl and bw and bh)
          else {"three": None, "spec": None, "error": "Brak wymiarów kartonu B"})
    data["three_b"] = pb["three"]
    data["spec_html_b"] = render_to_string("ui/carton_opt/_pallet_spec.html",
                                           {"s": pb["spec"], "err": pb["error"]})
    return JsonResponse(data)


@module_required("carton_opt")
def carton_opt_redesigns(request):
    """Lista projektów A/B + formularz założenia nowego."""
    rows = []
    limit = REDESIGNS_LIST_LIMIT
    qs = PackagingRedesign.objects.select_related("product")
    total = qs.count()
    for rd in qs[:limit]:
        m = redesign_metrics(rd)
        rows.append({"rd": rd, "fill_a": m["a"]["fill"], "fill_b": m["b"]["fill"]})
    return render(request, "ui/carton_opt/redesigns.html",
                  {"rows": rows, "total": total, "limit": limit,
                   "truncated": total > limit})


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
    # Jeden indeks = jeden projekt: jeśli już istnieje, OTWÓRZ go zamiast tworzyć duplikat.
    existing = PackagingRedesign.objects.filter(product=product).first()
    if existing:
        messages.info(request, f"Projekt A/B dla {product.code} już istnieje — otwieram.")
        return redirect("ui:carton_opt_redesign_detail", pk=existing.pk)
    rd = PackagingRedesign.create_for(product, scope=scope, user=request.user)
    messages.success(request, f"Projekt A/B dla {product.code} założony — wersja A zamrożona.")
    return redirect("ui:carton_opt_redesign_detail", pk=rd.pk)


@_optimizer
@require_POST
def carton_opt_suggest_to_redesign(request):
    """Z auto-sugestii wymiarów → nowy Projekt A/B (B = wymiary sugestii, zakres „karton").
    Reużywa create_for (snapshot A) — operator od razu widzi A|B|Δ na ekranie detalu."""
    product = get_object_or_404(Product, pk=request.POST.get("product_id"))

    def _dim(name):
        try:
            v = int(request.POST.get(name))
            return v if v > 0 else 0
        except (TypeError, ValueError):
            return 0
    l, w, h = _dim("length_cm"), _dim("width_cm"), _dim("height_cm")
    if not (l and w and h):
        messages.error(request, "Brak wymiarów sugestii — nie założono projektu.")
        return _safe_next(request, f"{reverse('ui:carton_opt_variants')}?product={product.code}")
    # Jeden indeks = jeden projekt: użyj istniejącego (zaktualizuj B) albo załóż nowy.
    rd = PackagingRedesign.objects.filter(product=product).first()
    created = rd is None
    if created:
        rd = PackagingRedesign.create_for(product, scope="karton", user=request.user)
    rd.b_carton_l, rd.b_carton_w, rd.b_carton_h = l, w, h
    fill = (request.POST.get("fill") or "").strip()[:8]
    rd.notes = f"Z auto-sugestii{f' (wypełnienie {fill}%)' if fill else ''}"
    rd.save(update_fields=["b_carton_l", "b_carton_w", "b_carton_h", "notes", "updated_at"])
    _verb = "założony" if created else "zaktualizowany (B z sugestii)"
    messages.success(request, f"Projekt A/B dla {product.code} z sugestii {l}×{w}×{h} {_verb}.")
    return redirect("ui:carton_opt_redesign_detail", pk=rd.pk)


def _engine_pallet(base, l, w, h):
    """Policz układ kartonu l×w×h na palecie z konfiguracji `base` (BEZ zapisu do DB) i
    zwróć {three(JSON dla renderPalVizLevel), spec(inżynierski), error}. Reużywa silnik
    (_build_pallet/PalletCalculator/_eval_layouts) + _compute_cog. spec: kartony/warstwę,
    warstwy, kartony/paletę, wysokość vs max, waga, wypełnienie, pokrycie podłogi, nawis,
    środek ciężkości (offset + wysokość) oraz ocena stabilności."""
    if not base:
        return {"three": None, "spec": None, "error": "Brak instrukcji bazowej materiału"}
    try:
        l, w, h = int(l), int(w), int(h)
        pallet, meta = _build_pallet(base.pallet_code or "EU",
                                     base.max_height_total_cm, base.max_weight_kg)
        carton = CartonVariant(
            sku=base.product.code, variant="AB",
            dims=Dimensions(l_cm=l, w_cm=w, h_cm=h),
            unit_weight_kg=max(0.001, float(base.unit_weight or 0)),   # 0 → validate rzuca; nie blokuj na braku wagi
            pieces_per_carton=int(base.pcs_per_carton or 1),
            demand_pieces=int(base.demand_pcs or 1000), carton_tare_kg=float(base.carton_tare or 0.0),
            allow_rotation=True).validate()
        res = PalletCalculator.calculate(carton, pallet)
        layouts = _eval_layouts(carton, pallet, meta, res)
        if not layouts:
            return {"three": None, "spec": None, "error": "Silnik nie zwrócił układu"}
        lay = layouts[0]
        pl, pw = meta["length_cm"], meta["width_cm"]
        base_h = meta.get("base_height_cm", PALLET_BASE_HEIGHT_CM)
        placements = lay["placements"]
        three = json.dumps({
            "type": "pallet", "pallet": {"l": pl, "w": pw, "base_h": base_h},
            "carton": {"l": l, "w": w, "h": h}, "placements": placements,
            "layers": lay["layers_used"], "label": base.product.code, "color": "#DCC4A0"})

        usable_h = (base.max_height_total_cm or 0) - base_h
        fill = _vol_fill(lay["cartons_per_pallet"], pl, pw, usable_h, l, w, h)
        maxx = max((p["x"] + p["dx"] for p in placements), default=0)
        maxy = max((p["y"] + p["dy"] for p in placements), default=0)
        overhang = round(max(0.0, maxx - pl, maxy - pw), 1)
        cog = lay.get("cog") or {}
        cog_off = max(abs(cog.get("offset_x") or 0), abs(cog.get("offset_y") or 0))
        cog_z = round(base_h + lay["layers_used"] * h / 2.0, 1)   # CoG stosu (uniform)
        stack_h = lay["total_height_used_cm"]
        min_dim = min(pl, pw) or 1
        slender = round(stack_h / min_dim, 2)                     # smukłość: ryzyko przewrócenia
        cog_pct = 100 * cog_off / min_dim
        # ponytail: progi heurystyki stabilności — do strojenia gdy pojawią się realne dane z hali.
        if overhang > 0 or slender > 1.8 or cog_pct > 15:
            stab, why = "ryzyko", ("nawis poza paletę" if overhang > 0
                                   else "wysoki/wąski stos" if slender > 1.8 else "CoG poza środkiem")
        elif slender > 1.4 or cog_pct > 8:
            stab, why = "uwaga", ("smukły stos" if slender > 1.4 else "CoG lekko poza środkiem")
        else:
            stab, why = "stabilny", "niski, wyśrodkowany"
        spec = {
            "pallet_l": pl, "pallet_w": pw, "max_h": base.max_height_total_cm, "max_w": base.max_weight_kg,
            "carton_l": l, "carton_w": w, "carton_h": h,
            "per_layer": lay["cartons_per_layer"], "layers": lay["layers_used"],
            "per_pallet": lay["cartons_per_pallet"], "stack_h": stack_h,
            "headroom": round((base.max_height_total_cm or 0) - stack_h, 1),
            "weight": lay["weight_per_pallet_kg"], "fill": fill, "floor": lay["area_used_pct"],
            "cog_x": cog.get("offset_x"), "cog_y": cog.get("offset_y"), "cog_z": cog_z,
            "overhang": overhang, "slender": slender, "stability": stab, "stability_why": why,
        }
        return {"three": three, "spec": spec, "error": ""}
    except Exception as exc:
        return {"three": None, "spec": None, "error": f"Nie mieści się / błąd: {exc}"[:120]}


def _orientation_options(base, l, w, h):
    """3 ustawienia kartonu (który wymiar jest PIONOWY) policzone silnikiem — „na płasko"
    (H↑), „na bok" (W↑), „na sztorc" (L↑). Reużywa _engine_pallet z przestawionymi
    wymiarami (silnik traktuje h jako pionową). Oznacza najlepsze wypełnienie. Dedup dla
    sześcianu / równych boków."""
    if not base or not (l and w and h):
        return []
    l, w, h = int(l), int(w), int(h)
    variants = [("Na płasko (H↑)", (l, w, h)),
                ("Na bok (W↑)", (l, h, w)),
                ("Na sztorc (L↑)", (w, h, l))]
    seen, out = set(), []
    for label, dims in variants:
        if dims in seen:
            continue
        seen.add(dims)
        s = (_engine_pallet(base, *dims).get("spec")) or {}
        out.append({"label": label, "l": dims[0], "w": dims[1], "h": dims[2],
                    "fill": s.get("fill"), "per_pallet": s.get("per_pallet"),
                    "stability": s.get("stability")})
    best = max((o for o in out if o["fill"] is not None),
               key=lambda o: o["fill"], default=None)
    for o in out:
        o["best"] = (o is best)
    return out


@module_required("carton_opt")
def carton_opt_redesign_detail(request, pk):
    rd = get_object_or_404(PackagingRedesign.objects.select_related("product"), pk=pk)
    base = rd.product.latest_instruction()
    a = rd.a
    a_dims = (a.get("carton_l"), a.get("carton_w"), a.get("carton_h"))
    if rd.scope == "op":                        # zakres OP: karton B = karton A
        b_dims = a_dims
    else:
        b_dims = (rd.b_carton_l or a.get("carton_l"), rd.b_carton_w or a.get("carton_w"),
                  rd.b_carton_h or a.get("carton_h"))
    pallet_a = (_engine_pallet(base, *a_dims) if all(a_dims)
                else {"three": None, "spec": None, "error": "Brak wymiarów kartonu A"})
    pallet_b = (_engine_pallet(base, *b_dims) if all(b_dims)
                else {"three": None, "spec": None, "error": "Brak wymiarów kartonu B"})
    orientations = (_orientation_options(base, *b_dims)
                    if base and rd.scope != "op" and all(b_dims) else [])
    return render(request, "ui/carton_opt/redesign_detail.html", {
        "rd": rd, "metrics": redesign_metrics(rd),
        "locked": rd.status == "accepted",
        "pallet_a": pallet_a, "pallet_b": pallet_b, "orientations": orientations,
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

__all__ = [
    'REDESIGNS_LIST_LIMIT',
    '_side_metrics',
    'redesign_metrics',
    'carton_opt_redesign_metrics',
    'carton_opt_redesigns',
    'carton_opt_redesign_new',
    'carton_opt_suggest_to_redesign',
    '_engine_pallet',
    '_orientation_options',
    'carton_opt_redesign_detail',
    'carton_opt_redesign_save',
]
