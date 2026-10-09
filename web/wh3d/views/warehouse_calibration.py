# Kalibracja symulacji na obecnej hali (plan 2026-10-02, etap 6): realne czasy cykli wózków
# i pickerów z zadań EWM dnia projektowego vs fizyka symulacji na modelu obecnej hali.
from urllib.parse import urlencode

from ui.views.core import _planner, get_object_or_404, hall_feature_dict, render, WarehouseModel
from wh3d.blender_scene import model_racks
from wh3d.blender_stock import SlotLocator, load_master_levels
from wh3d.blender_tasks import ROW_FIELDS
from wh3d.design_calibration import GAP_MAX_S, MIN_PAIRS, calibrate
from wh3d.design_day import PERCENTILES
from wh3d.models_tasks import TASK_KINDS, WarehouseTaskBatch
from wh3d.views.warehouse_design_sim import design_day, sim_params

__all__ = ["ewm_tasks_calibration"]


def _day_rows(batch, day):
    qs = batch.tasks.exclude(confirmed_at=None).filter(confirmed_at__date=day)
    return list(qs.order_by("confirmed_at", "id").values_list(*ROW_FIELDS))


@_planner
def ewm_tasks_calibration(request, pk):
    batch = get_object_or_404(WarehouseTaskBatch, pk=pk, status="done")
    p = sim_params(request.GET)["p"]
    models = list(WarehouseModel.objects.order_by("-created_at")[:50])
    wm = next((m for m in models if str(m.pk) == request.GET.get("model")), None)
    day = design_day(batch, p)
    result = None
    if wm and day:
        rows = _day_rows(batch, day)
        codes = {c for r in rows for c in (r[4], r[5]) if c}
        locator = SlotLocator(model_racks(wm), codes, load_master_levels())
        result = calibrate(rows, locator, [hall_feature_dict(f) for f in wm.features.all()])
    k = {g["key"]: g["k"] for g in (result or {}).get("groups", []) if g["k"]}
    sim_query = urlencode({"p": p, "kt": k.get("trucks", 1.0), "kp": k.get("picking", 1.0)})
    kinds = dict(TASK_KINDS)
    return render(request, "ui/ewm_tasks/calibration.html", {
        "batch": batch, "p": p, "percentiles": PERCENTILES, "models": models, "wm": wm, "day": day,
        "result": result, "sim_query": sim_query, "gap_max_min": GAP_MAX_S // 60, "min_pairs": MIN_PAIRS,
        "kind_rows": [dict(r, label=kinds.get(r["kind"], r["kind"])) for r in (result or {}).get("kinds", [])],
    })
