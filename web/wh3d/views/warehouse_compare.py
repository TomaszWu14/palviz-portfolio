# Porównanie wariantów hali na dniu projektowym (plan 2026-10-02, etap 7): pojemność + flota
# dobrana symulacją + droga, czekanie, zadania po 21:00 — obok siebie, najlepsza wartość wyróżniona.
from ui.views.core import _planner, get_object_or_404, hall_feature_dict, render, WarehouseModel
from wh3d.blender_scene import model_floor, model_racks
from wh3d.design_compare import capacity, comparison, required_fleet, variant_row
from wh3d.design_day import PERCENTILES
from wh3d.design_sim import load_day_tasks
from wh3d.models_tasks import WarehouseTaskBatch
from wh3d.views.warehouse_design_sim import design_day, sim_params

__all__ = ["ewm_tasks_compare"]

MAX_VARIANTS = 4


@_planner
def ewm_tasks_compare(request, pk):
    batch = get_object_or_404(WarehouseTaskBatch, pk=pk, status="done")
    prm = sim_params(request.GET)
    models = list(WarehouseModel.objects.order_by("-created_at")[:50])
    ids = [int(x) for x in request.GET.getlist("models") if x.isdigit()][:MAX_VARIANTS]
    chosen = [m for i in dict.fromkeys(ids) for m in models if m.pk == i]
    day = design_day(batch, prm["p"])
    rows, skipped = [], []
    if day and chosen:
        tasks = load_day_tasks(batch, day)
        for wm in chosen:
            racks = model_racks(wm)
            features = [hall_feature_dict(f) for f in wm.features.all()]
            result, fleet = required_fleet(tasks, racks, features, dict(prm["fleet"]),
                                           multiplier=prm["mult"], calib=prm["calib"])
            if result is None:
                skipped.append(wm.name)
            rows.append(variant_row(capacity(racks, features, model_floor(wm, racks)), result, fleet))
    return render(request, "ui/ewm_tasks/compare.html", {
        "batch": batch, "p": prm["p"], "percentiles": PERCENTILES, "mult": prm["mult"], "calib": prm["calib"],
        "models": models, "chosen": chosen, "chosen_ids": {m.pk for m in chosen}, "day": day,
        "table": comparison(rows) if rows else [], "skipped": skipped, "max_variants": MAX_VARIANTS,
    })
