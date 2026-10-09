"""Moduł „Optymalizacja kartonów" — centrum zarządzania wypełnieniem palet.

Faza 1: skrzynka odbiorcza zgłoszeń ze skanera (PHV → PackagingIssue) o kartonach do
optymalizacji — niewypełniony / dopasować do palety / za ciężki. Operator (rola
Optymalizacja kartonów) widzi listę wg pilności, filtruje po statusie i przesuwa
status open → w przeglądzie → rozwiązane. Zgłoszenia powstają już w phv.py; tu jest
tylko ich zarządzanie (rola-scoped: wszystkie zgłoszenia, nie tylko własne).
"""
from .core import (
    PALLET_BASE_HEIGHT_CM, module_required, Q, render, _optimizer,
    require_POST, get_object_or_404, messages, _safe_next
)
from django.utils import timezone
from django.db import transaction
from django.db.models import Max
from django.urls import reverse
from ..models import (OptimizationConfig,
                      Product, CartonAlternative, CartonPromotion)
from .core.packing import _recalculate_instruction
from ..hierarchy import build_hierarchy
from ..product_lookup import resolve_ref
from typing import NamedTuple

# Pilność zgłoszenia po typie — „dopasuj do palety" i „za ciężki" są operacyjnie
# pilniejsze niż samo niedopełnienie. Steruje sortowaniem i kolorem w skrzynce.
from .carton_opt_issues import _optimization_issues, _vol_fill  # noqa: F401
# Czyste funkcje pakowania (CODE-001: wydzielone, re-eksport bez zmian w __all__).
from .carton_opt_fill import pack_into, _variant_fill, _suggest_dims

def _pallets_per_truck():
    """Pojemność auta [palet] z konfiguracji modułu (OptimizationConfig, domyślnie 33)."""
    return OptimizationConfig.load().pallets_per_truck or 33
_OPT_PALLET_MAX_H_CM = 220  # realny limit magazynu (stos Z paletą); maxy w instrukcjach bywają niższe (klient/transport)


def _year_kpi(cpp, pcs, annual):
    """Palet/rok i aut/rok dla danej liczby kartonów/paletę. None gdy brak danych."""
    pcs_pp = (cpp or 0) * (pcs or 0)
    if not (pcs_pp and annual):
        return None
    pallets = -(-annual // pcs_pp)                     # ceil-div
    return {"pallets": pallets, "trucks": -(-pallets // _pallets_per_truck())}


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

    # InnerPack trzyma FloatField — "20.0×15.0" w UI to brzydki artefakt; całkowite → int.
    def _norm(dims):
        return tuple(int(v) if isinstance(v, float) and v.is_integer() else v
                     for v in dims) if dims else None
    u, p_, c = _norm(u), _norm(p_), _norm(c)

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
    base_h = instr.pallet_base_height_cm or PALLET_BASE_HEIGHT_CM
    # Decyzja usera 2026-08-24: optymalizacja liczy do REALNEGO limitu magazynu
    # 220 cm (z paletą), bo maxy w master dacie bywają różne (np. 108 = limit
    # klienta/transportu). Max z instrukcji pokazujemy obok, gdy się różni.
    max_h = _OPT_PALLET_MAX_H_CM
    instr_max = instr.max_height_total_cm or 0
    pallet_td, stack_str = "", ""
    pr = pack_into((meta_l, meta_w, max_h - base_h), c)
    if pr["count"]:
        pallet_td = _json.dumps({
            "type": "pallet",
            "pallet": {"l": meta_l, "w": meta_w, "base_h": base_h},
            "carton": {"l": c[0], "w": c[1], "h": c[2]},
            "placements": pr["placements"], "layers": pr["layers"] or 1,
            "label": product.code, "color": "#DCC4A0"})
        # Wysokość zbudowanego stosu vs dozwolone max — bez tego "wypełnienie" jest
        # nieczytelne (render pokazuje tylko stos, nie widać zapasu wysokości).
        stack_h = base_h + (pr["layers"] or 0) * c[2]
        stack_str = f"{stack_h}/{max_h} cm (zapas {max(0, max_h - stack_h)})"
    dims_str = f"{meta_l}×{meta_w} · max {max_h}"
    if instr_max and instr_max != max_h:
        dims_str += f" (instrukcja: {instr_max})"
    levels.append({"key": "pallet", "title": "Paleta", "dims": (meta_l, meta_w, None),
                   "three_data": pallet_td,
                   "spec": {"dims_str": dims_str,
                            "stack": stack_str,
                            "contains": pr["count"],
                            "fill": pr["fill"],
                            "error": pr["error"] or vf["error"]}})
    return levels


def _annual_volume(request, instr):
    """Wolumen roczny [szt] z ?annual= (edytowalny), domyślnie demand_pcs instrukcji; ≥ 0."""
    try:
        annual = int(request.GET.get("annual") or (instr.demand_pcs if instr else 0) or 0)
    except (TypeError, ValueError):
        annual = int(instr.demand_pcs or 0) if instr else 0
    return max(0, annual)


def _savings_fn(base_cpp, pcs, annual):
    """→ savings(cpp) = (palet/rok, aut/rok) oszczędzone vs obecny karton; (None, None) bez danych."""
    base_kpi = _year_kpi(base_cpp, pcs, annual)

    def _savings(cpp):
        rk = _year_kpi(cpp, pcs, annual)
        if not (base_kpi and rk):
            return None, None
        return base_kpi["pallets"] - rk["pallets"], base_kpi["trucks"] - rk["trucks"]
    return _savings


def _baseline(instr):
    """Obecny karton (instrukcja) z wyliczonym wypełnieniem — odniesienie dla wariantów."""
    if not instr:
        return None
    return {"label": "Obecny (instrukcja)", "l": instr.carton_l, "w": instr.carton_w,
            "h": instr.carton_h, **_variant_fill(instr.carton_l, instr.carton_w,
                                                 instr.carton_h, instr)}


def _delta(value, base):
    return value - base if value is not None and base is not None else None


def _variant_row(alt, instr, baseline, savings):
    """Wiersz tabeli wariantów: wypełnienie + historia wymiarów + delta/KPI vs obecny."""
    hist = [{"l": h.length_cm, "w": h.width_cm, "h": h.height_cm,
             "when": h.history_date} for h in alt.history.all()[:5]]
    row = {"alt": alt, "l": alt.length_cm, "w": alt.width_cm, "h": alt.height_cm,
           "history": hist, **_variant_fill(alt.length_cm, alt.width_cm,
                                             alt.height_cm, instr)}
    # Delta vs obecny (instrukcja) — steruje badge „lepszy" i decyzją operatora.
    row["delta_pp"] = _delta(row["fill"], baseline["fill"] if baseline else None)
    row["delta_cpp"] = _delta(row["cartons_per_pallet"],
                              baseline["cartons_per_pallet"] if baseline else None)
    row["save_pallets"], row["save_trucks"] = savings(row["cartons_per_pallet"])
    return row


def _variants_table(request, product):
    """Instrukcja, baseline, wiersze wariantów i KPI roczne materiału.
    → (instr, baseline, rows, annual, savings)."""
    instr = product.latest_instruction()
    baseline = _baseline(instr)
    # KPI oszczędności: wolumen roczny (edytowalny, default demand_pcs) → palet/aut rocznie.
    annual = _annual_volume(request, instr)
    pcs = (instr.pcs_per_carton if instr else 0) or 0
    savings = _savings_fn(baseline["cartons_per_pallet"] if baseline else None, pcs, annual)
    rows = [_variant_row(alt, instr, baseline, savings)
            for alt in product.carton_alternatives.filter(is_active=True)]
    # Najlepsze wypełnienie na wierzchu (warianty; baseline zostaje osobno jako odniesienie).
    rows.sort(key=lambda r: (r["fill"] is None, -(r["fill"] or 0)))
    return instr, baseline, rows, annual, savings


def _dim_suggestions(instr, cfg, savings):
    """Auto-sugestie wymiarów (pasmo objętości z konfiguracji) z KPI oszczędności."""
    sugg = _suggest_dims(instr, cfg.suggest_vol_down_pct, cfg.suggest_vol_up_pct)
    for s in sugg:
        s["save_pallets"], s["save_trucks"] = savings(s["cartons_per_pallet"])
    return sugg


def _workspace_issue(request):
    """Zgłoszenie z ?issue=<pk> (kontekst warsztatu). Uwaga: filtr tylko po pk, bez
    materiału — przypięte testem charakteryzującym jako obecne zachowanie."""
    iid = request.GET.get("issue")
    if not (iid and iid.isdigit()):
        return None
    return (_optimization_issues().select_related("reporter", "assigned_to")
            .filter(pk=iid).first())


def _pallet_three(product, instr):
    """three_data palety z build_hierarchy (reuse renderu); None przy braku/błędzie."""
    try:
        for lvl in build_hierarchy(product, instr).get("levels", []):
            if lvl["key"] == "pallet":
                return lvl["three_data"]
    except Exception:
        return None
    return None


def _dims_or_none(l, w, h):
    return (l, w, h) if l and w and h else None


def _slot_ctx(product, instr, slot):
    """Aktywny wariant slotu (B/C) i jego kaskada hierarchii; (alt, None) bez instrukcji."""
    alt = (product.carton_alternatives
           .filter(is_active=True, slot=slot).order_by("-updated_at").first())
    if not (alt and instr):
        return alt, None
    unit = _dims_or_none(alt.unit_l_cm, alt.unit_w_cm, alt.unit_h_cm)
    pack = _dims_or_none(alt.pack_l_cm, alt.pack_w_cm, alt.pack_h_cm)
    return alt, _hierarchy_levels(instr, product, unit=unit, pack=pack,
                                  carton=(alt.length_cm, alt.width_cm, alt.height_cm))


def _workspace_ctx(request, product, instr):
    """Warsztat materiału: promocje, zgłoszenie, 3D palety i hierarchie A/B/C."""
    if not product:
        return {"promotions": [], "issue": None, "pallet_three": None, "hier_a": None,
                "variant_b": None, "hier_b": None, "variant_c": None, "hier_c": None}
    promotions = list(product.carton_promotions.select_related("user")[:10])
    # Kontekst zgłoszenia (warsztat) + podgląd 3D palety (build_hierarchy) — reuse renderu.
    issue = _workspace_issue(request)
    pallet_three = _pallet_three(product, instr) if instr else None
    hier_a = _hierarchy_levels(instr, product) if instr else None
    variant_b, hier_b = _slot_ctx(product, instr, "B")
    variant_c, hier_c = _slot_ctx(product, instr, "C")
    return {"promotions": promotions, "issue": issue, "pallet_three": pallet_three,
            "hier_a": hier_a, "variant_b": variant_b, "hier_b": hier_b,
            "variant_c": variant_c, "hier_c": hier_c}


@module_required("carton_opt")
def carton_opt_variants(request):
    """Warianty kartonu materiału z wyliczonym wypełnieniem side-by-side + historia zmian.
    Materiał wskazany przez ?product=<kod>. Bez materiału → wyszukiwarka."""
    q = (request.GET.get("product") or "").strip()
    product = resolve_ref(q)
    suggestions, rows, instr, baseline, annual, savings = [], [], None, None, 0, None
    if q and not product:
        suggestions = list(Product.objects.filter(
            Q(code__icontains=q) | Q(name__icontains=q), is_active=True).values("code", "name")[:10])
    if product:
        instr, baseline, rows, annual, savings = _variants_table(request, product)
    cfg = OptimizationConfig.load()
    has_instr = bool(product and instr)
    dim_suggestions = (_dim_suggestions(instr, cfg, savings)
                       if has_instr and request.GET.get("suggest") else [])
    return render(request, "ui/carton_opt/variants.html", {
        "q": q, "product": product, "instr": instr, "baseline": baseline,
        "rows": rows, "suggestions": suggestions,
        "dim_suggestions": dim_suggestions, "cfg": cfg,
        "annual": annual if has_instr else 0,
        **_workspace_ctx(request, product, instr),
    })


@_optimizer
@require_POST
def carton_opt_variant_save(request):
    """Dodaj lub edytuj wariant kartonu (edycja zapisuje historię przez simple_history)."""
    product = get_object_or_404(Product, pk=request.POST.get("product_id"))

    def _int(name):
        try:
            return max(1, int(request.POST.get(name)))
        except (TypeError, ValueError):
            return 0
    l, w, h = _int("length_cm"), _int("width_cm"), _int("height_cm")
    label = (request.POST.get("label") or "").strip()[:80]
    if not (label and l and w and h):
        messages.error(request, "Podaj nazwę i wszystkie wymiary wariantu.")
        return _safe_next(request, "ui:carton_opt_variants")
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
    alt_id = request.POST.get("alt_id")
    if alt_id and alt_id.isdigit():
        alt = get_object_or_404(CartonAlternative, pk=alt_id, product=product)
        alt.label, alt.length_cm, alt.width_cm, alt.height_cm = label, l, w, h
        for k, v in extra.items():
            setattr(alt, k, v)
        alt.save()      # simple_history zapisuje poprzednie wymiary
        messages.success(request, f"Wariant „{label}” zaktualizowany.")
    else:
        alt = CartonAlternative.objects.create(product=product, label=label,
                                               length_cm=l, width_cm=w, height_cm=h, **extra)
        messages.success(request, f"Dodano wariant „{label}”.")
    if slot:      # slot unikalny: dezaktywuj inne aktywne warianty tego slotu
        product.carton_alternatives.filter(slot=slot, is_active=True).exclude(pk=alt.pk).update(is_active=False)
    return _safe_next(request, "ui:carton_opt_variants")


@_optimizer
@require_POST
def carton_opt_variant_delete(request, pk):
    """Ukryj wariant (soft-delete — historia zostaje)."""
    alt = get_object_or_404(CartonAlternative, pk=pk)
    alt.is_active = False
    alt.save(update_fields=["is_active", "updated_at"])
    messages.success(request, "Wariant usunięty.")
    return _safe_next(request, "ui:carton_opt_variants")


class PromoteResult(NamedTuple):
    ok: bool
    error: str = ""
    instruction: object = None
    recalc_error: str = ""
    issue_resolved: bool = False


def promote_variant(alt, user, *, issue=None):
    """Przenieś wariant kartonu do instrukcji paletyzacji jako NOWĄ wersję (version+1, stara
    nietknięta). Zwraca PromoteResult — widok mapuje na komunikaty/redirect. Testowalne bez
    HTTP: podajesz CartonAlternative + usera (+opcjonalnie zgłoszenie).

    Kroki: fit-guard → bump wersji pod select_for_update → recalc (błąd łapany, nie wywraca)
    → domknięcie zgłoszenia → audyt CartonPromotion (wypełnienie przed→po)."""
    base = alt.product.latest_instruction()
    if not base:
        return PromoteResult(False, "Brak instrukcji bazowej materiału — nie ma z czego utworzyć wersji.")
    # Nie tworzymy złej instrukcji: wariant musi się zmieścić na palecie bazowej.
    check = _variant_fill(alt.length_cm, alt.width_cm, alt.height_cm, base)
    if not check["fits"] or check["fill"] is None:
        return PromoteResult(False, f"Wariant „{alt.label}” nie mieści się na palecie — nie przeniesiono.")
    # Wypełnienie liczone silnikiem (jak baseline w widoku) — niezależne od zapisanych layoutów.
    fill_before = _variant_fill(base.carton_l, base.carton_w, base.carton_h, base)["fill"]

    with transaction.atomic():
        Product.objects.select_for_update().get(pk=alt.product_id)   # serializacja numeracji wersji
        last_v = base.product.instructions.aggregate(m=Max("version"))["m"] or 0
        base.pk = None
        base._state.adding = True
        base.version = last_v + 1
        base.carton_l, base.carton_w, base.carton_h = alt.length_cm, alt.width_cm, alt.height_cm
        base.carton = None                       # wymiary z wariantu, nie z rekordu Carton
        base.layouts = []                        # wymuś świeże przeliczenie
        base.selected_layout = ""
        base.notes = (f"{base.notes}\n" if base.notes else "") + \
                     f"Z wariantu „{alt.label}” (Optymalizacja kartonów)"
        base.save()
    recalc_error = ""
    try:
        _recalculate_instruction(base)
    except Exception as exc:
        recalc_error = str(exc)
    # Domknięcie zgłoszenia (warsztat): promocja z kontekstem ?issue → rozwiąż zgłoszenie.
    issue_resolved = False
    if issue and issue.status != "resolved":
        issue.status = "resolved"
        issue.resolved_at = timezone.now()
        issue.resolver_notes = (issue.resolver_notes or
                                f"Zoptymalizowano: karton {alt.length_cm}×{alt.width_cm}×{alt.height_cm}")[:300]
        issue.save(update_fields=["status", "resolved_at", "resolver_notes"])
        issue_resolved = True
    # Audyt promocji: kto/kiedy/skąd + wypełnienie przed→po (fill_after z przeliczonej wersji).
    CartonPromotion.objects.create(
        product=alt.product, version=base.version,
        dims=f"{alt.length_cm}×{alt.width_cm}×{alt.height_cm}",
        source_label=alt.label, fill_before=fill_before, fill_after=check["fill"],
        issue=issue, user=user)
    return PromoteResult(True, instruction=base, recalc_error=recalc_error, issue_resolved=issue_resolved)


@_optimizer
@require_POST
def carton_opt_variant_promote(request, pk):
    """Domknięcie pętli: przenieś wariant do instrukcji jako nową wersję. Logika (fit/wersja/
    recalc/zgłoszenie/audyt) w promote_variant — tu tylko orkiestracja komunikatów/redirectu."""
    alt = get_object_or_404(CartonAlternative, pk=pk, is_active=True)
    back = f"{reverse('ui:carton_opt_variants')}?product={alt.product.code}"
    iid = request.POST.get("issue")
    issue = (_optimization_issues().filter(pk=iid, product=alt.product).first()
             if iid and iid.isdigit() else None)
    res = promote_variant(alt, request.user if request.user.is_authenticated else None, issue=issue)
    if not res.ok:
        messages.error(request, res.error)
        return _safe_next(request, back)
    if res.recalc_error:
        messages.warning(request, f"Zapisano wersję v{res.instruction.version}, ale błąd obliczeń: {res.recalc_error}")
    else:
        messages.success(request, f"Utworzono instrukcję v{res.instruction.version} z wariantu „{alt.label}” i przeliczono.")
    if res.issue_resolved:
        messages.success(request, f"Zgłoszenie #{issue.pk} rozwiązane.")
        back = f"{reverse('ui:carton_opt_variants')}?product={alt.product.code}&issue={issue.pk}"
    return _safe_next(request, back)


# ── Projekty A/B (redesign opakowań) — metryki A|B|Δ ────────────────────────────

__all__ = [
    'pack_into', '_variant_fill', '_suggest_dims', '_pallets_per_truck',
    '_OPT_PALLET_MAX_H_CM', '_year_kpi', '_hierarchy_levels', 'carton_opt_variants',
    'carton_opt_variant_save', 'carton_opt_variant_delete', 'PromoteResult',
    'promote_variant', 'carton_opt_variant_promote',
]
