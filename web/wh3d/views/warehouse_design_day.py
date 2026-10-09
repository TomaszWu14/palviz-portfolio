# Profil ruchów i dzień projektowy z importu zadań EWM (spec projektowania magazynu, krok 3):
# percentyle strumieni (P90/P95/P99), godzina szczytu, ABC/XYZ, profil zleceń, pojemność.
from django.core.cache import cache

from ui.views.core import _planner, get_object_or_404, render, safe_json, WarehouseSnapshot
from wh3d.design_day import PERCENTILES, build_profile, load_groups, load_inputs
from wh3d.models_tasks import WarehouseTaskBatch

__all__ = ["ewm_tasks_profile"]

CACHE_TTL = 24 * 3600          # partia po imporcie się nie zmienia — agregaty liczone raz na dobę
MIN_DAYS = 20                  # mniej dni roboczych = percentyl ≈ maksimum → ostrzeżenie


@_planner
def ewm_tasks_profile(request, pk):
    batch = get_object_or_404(WarehouseTaskBatch, pk=pk, status="done")
    p = int(request.GET.get("p")) if request.GET.get("p") in {str(q) for q in PERCENTILES} else 95
    inputs = cache.get_or_set(f"wt-profile-inputs:{batch.pk}", lambda: load_inputs(batch), CACHE_TTL)
    profile = build_profile(inputs["daily"], inputs["hourly"], inputs["material_days"],
                            inputs["lines_per_order"], p=p, groups=load_groups(inputs["material_days"]))
    chart = None
    if profile:
        design = next((s["design"] for s in profile["streams"] if s["key"] == "total"), 0)
        chart = {"daily": {**profile["daily_series"], "design": design},
                 "hours": {"avg": [round(sum(v[h] for v in profile["hourly_avg"].values()), 1) for h in range(24)],
                           "design_day": profile["design_day"]["hours"]}, "p": p}
    return render(request, "ui/ewm_tasks/profile.html", {
        "batch": batch, "profile": profile, "p": p, "percentiles": PERCENTILES, "min_days": MIN_DAYS,
        "chart_json": safe_json(chart), "snapshot": WarehouseSnapshot.objects.order_by("-uploaded_at").first(),
    })
