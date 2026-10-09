"""Moduł „Optymalizacja kartonów" — centrum zarządzania wypełnieniem palet.

Faza 1: skrzynka odbiorcza zgłoszeń ze skanera (PHV → PackagingIssue) o kartonach do
optymalizacji — niewypełniony / dopasować do palety / za ciężki. Operator (rola
Optymalizacja kartonów) widzi listę wg pilności, filtruje po statusie i przesuwa
status open → w przeglądzie → rozwiązane. Zgłoszenia powstają już w phv.py; tu jest
tylko ich zarządzanie (rola-scoped: wszystkie zgłoszenia, nie tylko własne).
"""
from .core import (
    module_required, render, _optimizer, require_POST, get_object_or_404, messages,
    redirect, _safe_next, has_role, GROUP_ADMIN, _admin_only
)
from django.db.models import Count
from django.utils import timezone
from django.urls import reverse
from ..models import (PackagingIssue, PalletizationInstruction, OptimizationConfig)
from .phv import OPTIMIZATION_ISSUE_TYPES

# Pilność zgłoszenia po typie — „dopasuj do palety" i „za ciężki" są operacyjnie
# pilniejsze niż samo niedopełnienie. Steruje sortowaniem i kolorem w skrzynce.

# Pilność zgłoszenia po typie — „dopasuj do palety" i „za ciężki" są operacyjnie
# pilniejsze niż samo niedopełnienie. Steruje sortowaniem i kolorem w skrzynce.
_URGENCY = {"carton_fit_pallet": 0, "carton_too_heavy": 1, "carton_underfilled": 2}


def _optimization_issues():
    return PackagingIssue.objects.filter(issue_type__in=OPTIMIZATION_ISSUE_TYPES)


@module_required("carton_opt")
def carton_opt_inbox(request):
    """Skrzynka zgłoszeń optymalizacyjnych: lista + filtr statusu + podgląd pojedynczego."""
    status = request.GET.get("status", "open")     # open|in_review|resolved|all
    qs = _optimization_issues().select_related("product", "reporter")
    if status in dict(PackagingIssue.STATUS):
        qs = qs.filter(status=status)

    issues = sorted(qs, key=lambda o: (o.status == "resolved",
                                       _URGENCY.get(o.issue_type, 9),
                                       -o.created_at.timestamp()))

    issue_id = request.GET.get("issue")
    single = (_optimization_issues().filter(pk=issue_id).select_related("product", "reporter").first()
              if issue_id and issue_id.isdigit() else None)

    counts = {s: 0 for s, _ in PackagingIssue.STATUS}
    counts.update({r["status"]: r["n"] for r in
                   _optimization_issues().values("status").annotate(n=Count("id"))})
    return render(request, "ui/carton_opt/inbox.html", {
        "issues": issues[:200], "single": single, "f_status": status,
        "statuses": PackagingIssue.STATUS, "counts": counts,
        "open_count": counts.get("open", 0),
    })


@_optimizer
@require_POST
def carton_opt_claim_issue(request, pk):
    """Przyjmij zgłoszenie do optymalizacji: przypisz operatora + status „w przeglądzie"."""
    issue = get_object_or_404(_optimization_issues(), pk=pk)
    issue.assigned_to = request.user
    issue.assigned_at = timezone.now()
    if issue.status == "open":
        issue.status = "in_review"
    issue.save(update_fields=["assigned_to", "assigned_at", "status"])
    messages.success(request, f"Zgłoszenie #{issue.pk} przyjęte.")
    if issue.product:
        return redirect(f"{reverse('ui:carton_opt_variants')}?product={issue.product.code}&issue={issue.pk}")
    return _safe_next(request, "ui:carton_opt_inbox")


@_optimizer
@require_POST
def carton_opt_set_status(request, pk):
    """Zmiana statusu zgłoszenia + opcjonalna notatka rozwiązania."""
    issue = get_object_or_404(_optimization_issues(), pk=pk)
    status = request.POST.get("status")
    if status in dict(PackagingIssue.STATUS):
        issue.status = status
        notes = (request.POST.get("resolver_notes") or "").strip()[:300]
        if notes:
            issue.resolver_notes = notes
        issue.resolved_at = timezone.now() if status == "resolved" else None
        issue.save(update_fields=["status", "resolver_notes", "resolved_at"])
        messages.success(request, f"Zgłoszenie #{issue.pk}: {issue.get_status_display()}.")
    return _safe_next(request, "ui:carton_opt_inbox")


def _vol_fill(cpp, pallet_l, pallet_w, usable_h, cl, cw, ch):
    """Wypełnienie OBJĘTOŚCI palety (%) = objętość kartonów / dostępna objętość ładunku
    (pełna wysokość). None = brak danych. NIE używamy layout['cube_used_pct'] — ono liczy
    gęstość w JUŻ zajętej wysokości (≈ pokrycie podłogi), więc paleta ułożona 2/10 warstw
    wychodziłaby „pełna" i dashboard nie wykryłby niedopełnienia w pionie."""
    pallet_vol = (pallet_l or 0) * (pallet_w or 0) * (usable_h or 0)
    carton_vol = (cl or 0) * (cw or 0) * (ch or 0)
    if cpp and pallet_vol > 0 and carton_vol > 0:
        return min(100, round(100 * cpp * carton_vol / pallet_vol))
    return None


def _fill_pct(instr):
    """Wypełnienie objętości palety (%) dla instrukcji z jej wybranego layoutu."""
    layout = instr.get_selected_layout() or {}
    usable_h = (instr.max_height_total_cm or 0) - (instr.pallet_base_height_cm or 0)
    return _vol_fill(layout.get("cartons_per_pallet"), instr.pallet_length_cm,
                     instr.pallet_width_cm, usable_h, instr.carton_l, instr.carton_w, instr.carton_h)


def _urgency_level(fill, threshold):
    """Pilność materiału wg dystansu do progu: 0=krytyczna (<50%), 1=wysoka (<próg−15),
    2=średnia (<próg). Steruje kolorem i sortowaniem."""
    if fill < 50:
        return 0
    if fill < threshold - 15:
        return 1
    return 2


@module_required("carton_opt")
def carton_opt_dashboard(request):
    """Dashboard pilności per materiał: aktywne instrukcje z wypełnieniem palety poniżej
    progu (OptimizationConfig.min_fill_pct), posortowane od najgorszego wypełnienia."""
    cfg = OptimizationConfig.load()
    threshold = cfg.min_fill_pct
    rows = []
    qs = (PalletizationInstruction.objects.filter(is_active=True)
          .select_related("product").order_by("product__code", "-version"))
    seen = set()
    for instr in qs:
        if instr.product_id in seen:      # tylko najnowsza aktywna wersja per produkt
            continue
        seen.add(instr.product_id)
        fill = _fill_pct(instr)
        if fill is None or fill >= threshold:
            continue
        rows.append({
            "product": instr.product, "instr": instr, "fill": fill,
            "urgency": _urgency_level(fill, threshold), "gap": threshold - fill,
        })
    rows.sort(key=lambda r: (r["urgency"], r["fill"]))
    return render(request, "ui/carton_opt/dashboard.html", {
        "rows": rows, "threshold": threshold, "cfg": cfg,
        "can_edit_threshold": has_role(request.user, GROUP_ADMIN),
    })


@_admin_only
@require_POST
def carton_opt_set_config(request):
    """Zmiana progu wypełnienia (tylko admin) — steruje dashboardem pilności."""
    cfg = OptimizationConfig.load()

    def _clamp(name, cur, lo, hi):
        try:
            return min(max(int(request.POST.get(name, cur)), lo), hi)
        except (TypeError, ValueError):
            return cur
    cfg.min_fill_pct = _clamp("min_fill_pct", cfg.min_fill_pct, 1, 100)
    cfg.suggest_vol_down_pct = _clamp("suggest_vol_down_pct", cfg.suggest_vol_down_pct, 0, 90)
    cfg.suggest_vol_up_pct = _clamp("suggest_vol_up_pct", cfg.suggest_vol_up_pct, 0, 100)
    cfg.pallets_per_truck = _clamp("pallets_per_truck", cfg.pallets_per_truck, 1, 66)
    cfg.save(update_fields=["min_fill_pct", "suggest_vol_down_pct",
                            "suggest_vol_up_pct", "pallets_per_truck", "updated_at"])
    messages.success(request, f"Zapisano: próg {cfg.min_fill_pct}%, "
                     f"pasmo sugestii −{cfg.suggest_vol_down_pct}%/+{cfg.suggest_vol_up_pct}%.")
    return _safe_next(request, "ui:carton_opt_dashboard")

__all__ = [
    '_URGENCY',
    '_optimization_issues',
    'carton_opt_inbox',
    'carton_opt_claim_issue',
    'carton_opt_set_status',
    '_vol_fill',
    '_fill_pct',
    '_urgency_level',
    'carton_opt_dashboard',
    'carton_opt_set_config',
]
