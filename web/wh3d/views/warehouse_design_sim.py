# Symulacja dnia projektowego na modelu hali (plan 2026-10-02, etap 3a): zadania EWM
# z dnia P95 × mnożnik wzrostu → flota AGV / kombi / EPT → wykorzystanie, czekanie, sugerowana flota.
from urllib.parse import urlencode

from django.core.cache import cache

from ui.views.core import _planner, get_object_or_404, hall_feature_dict, render, WarehouseModel
from wh3d.blender_scene import model_racks
from wh3d.design_day import PERCENTILES, build_profile, load_inputs
from wh3d.design_sim import FLEET_KINDS, load_day_tasks, simulate
from wh3d.models_tasks import TASK_KINDS, WarehouseTaskBatch
from wh3d.views.warehouse_design_day import CACHE_TTL

__all__ = ["ewm_tasks_simulate"]

DEFAULT_FLEET = {"agv": 4, "kombi": 5, "ept": 5}      # wstępne wymiarowanie z planu


def _int(raw, default, lo, hi):
    try:
        return min(hi, max(lo, int(raw)))
    except (TypeError, ValueError):
        return default


def _float(raw, default, lo, hi):
    try:
        return min(hi, max(lo, float(str(raw).replace(",", "."))))
    except (TypeError, ValueError):
        return default


def sim_params(g):
    """Parametry symulacji z GET (wspólne dla ekranu wyników i animacji godziny)."""
    p = _int(g.get("p"), 95, 0, 100)
    return {"p": p if p in PERCENTILES else 95, "mult": _float(g.get("mult"), 1.0, 0.1, 10.0),
            "fleet": {k: _int(g.get(k), DEFAULT_FLEET[k], 1, 60) for k, _ in FLEET_KINDS},
            # współczynniki z „Kalibracji na obecnej hali” (etap 6); 1,0 = czasy katalogowe
            "calib": {"trucks": _float(g.get("kt"), 1.0, 0.3, 10.0), "picking": _float(g.get("kp"), 1.0, 0.3, 10.0)}}


def design_day(batch, p):
    inputs = cache.get_or_set(f"wt-profile-inputs:{batch.pk}", lambda: load_inputs(batch), CACHE_TTL)
    profile = build_profile(inputs["daily"], inputs["hourly"], p=p)
    return profile["design_day"]["date"] if profile else None


def run_simulation(batch, wm, day, prm, trace=None):
    """(wynik, liczba zadań dnia) — wynik None, gdy model nie ma VNA / półek / doków."""
    tasks = load_day_tasks(batch, day)
    result = simulate(tasks, model_racks(wm), [hall_feature_dict(f) for f in wm.features.all()],
                      prm["fleet"], multiplier=prm["mult"], trace=trace, calib=prm["calib"])
    return result, len(tasks)


@_planner
def ewm_tasks_simulate(request, pk):
    batch = get_object_or_404(WarehouseTaskBatch, pk=pk, status="done")
    g = request.GET
    prm = sim_params(g)
    models = list(WarehouseModel.objects.order_by("-created_at")[:50])
    wm = next((m for m in models if str(m.pk) == g.get("model")), models[0] if models else None)
    day = design_day(batch, prm["p"])
    result, n_tasks = (None, 0)
    if day and wm and "run" in g:
        result, n_tasks = run_simulation(batch, wm, day, prm)
    anim_query = urlencode({"sim": batch.pk, "p": prm["p"], "mult": prm["mult"], **prm["fleet"],
                            "kt": prm["calib"]["trucks"], "kp": prm["calib"]["picking"]})
    return render(request, "ui/ewm_tasks/simulate.html", {
        "batch": batch, "p": prm["p"], "percentiles": PERCENTILES, "mult": prm["mult"], "fleet": prm["fleet"],
        "fleet_rows": [(k, label, prm["fleet"][k]) for k, label in FLEET_KINDS],
        "models": models, "wm": wm, "day": day, "ran": "run" in g, "result": result, "n_tasks": n_tasks,
        "wait_rows": [(dict(TASK_KINDS).get(k, k), w) for k, w in (result or {}).get("waits", {}).items()],
        "anim_query": anim_query, "calib": prm["calib"],
    })
