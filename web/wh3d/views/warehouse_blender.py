# Scena przepływów modelu magazynu: eksport do Blendera (tools/blender/) oraz ten sam
# JSON dla animacji w aplikacji (odtwarzacz three.js w widoku 3D modelu).
from ui.views.core import (
    _planner, get_object_or_404, hall_feature_dict, JsonResponse, PickerActivityBatch, WarehouseModel,
    WarehouseSnapshot,
)
from wh3d.blender_scene import build_scene_for_model, model_floor, model_racks
from wh3d.blender_tasks import MAX_HOURS, default_start, load_window, parse_start
from wh3d.models_tasks import WarehouseTaskBatch

# Limity: A* ~20 ms na trasę (hala 120×80 m) — 10 pickerów × 25 pobrań ≈ 6 s liczenia,
# a widok jest synchroniczny (jeden worker gunicorna na czas liczenia).
MAX_FORKLIFTS, MAX_PICKERS, MAX_PICKS = 10, 10, 25


def _clamped(raw, default, lo, hi):
    try:
        return min(hi, max(lo, int(raw)))
    except (TypeError, ValueError):
        return default


def _clamped_float(raw, default, lo, hi):
    try:
        return min(hi, max(lo, float(raw)))
    except (TypeError, ValueError):
        return default


def _wt_window(request):
    """?wt=<id>|latest — wózki z importu zadań EWM w oknie ?wt_from (czas lokalny,
    domyślnie 1. godzina partii) + ?wt_hours (0,25–24, domyślnie 1) z kompresją ?wt_scale
    (1–120 — skraca tylko postoje między zadaniami). Inna wartość/brak → wózki demo."""
    param = (request.GET.get("wt") or "").strip()
    done = WarehouseTaskBatch.objects.filter(status="done")
    if param == "latest":
        batch = done.first()
    elif param.isdigit():
        batch = get_object_or_404(done, pk=int(param))
    else:
        return None
    if batch is None:
        return None
    start = parse_start(request.GET.get("wt_from")) or default_start(batch)
    if start is None:                                     # partia bez potwierdzonych zadań
        return None
    return load_window(batch, start, hours=_clamped_float(request.GET.get("wt_hours"), 1.0, 0.25, MAX_HOURS),
                       scale=_clamped_float(request.GET.get("wt_scale"), 1.0, 1.0, 120.0))


def _sim_scene(request, wm):
    """?sim=<id importu WT>&sim_h=<godzina 5–20>&p=&mult=&agv=&kombi=&ept= — godzina z symulacji
    dnia projektowego na tym modelu (etap 3b). Brak ?sim → None (zwykła scena)."""
    from wh3d.design_sim import DAY_END_H, DAY_START_H
    from wh3d.design_sim_scene import build_sim_scene
    from wh3d.views.warehouse_design_sim import design_day, run_simulation, sim_params

    param = (request.GET.get("sim") or "").strip()
    if not param.isdigit():
        return None
    batch = get_object_or_404(WarehouseTaskBatch, pk=int(param), status="done")
    prm = sim_params(request.GET)
    hour = _clamped(request.GET.get("sim_h"), DAY_START_H + 3, DAY_START_H, DAY_END_H - 1)
    racks = model_racks(wm)
    features = [hall_feature_dict(f) for f in wm.features.all()]
    model = {"id": wm.pk, "name": wm.name}
    day = design_day(batch, prm["p"])
    trace = []
    result = run_simulation(batch, wm, day, prm, trace=trace)[0] if day else None
    if result is None:                    # brak dnia albo model bez VNA/półek → pusta scena + komunikat
        return build_sim_scene(model, model_floor(wm, racks), racks, features, [], hour)
    return build_sim_scene(model, model_floor(wm, racks), racks, features, trace, hour,
                           info={"batch": batch.name, "day": day.isoformat(), "p": prm["p"],
                                 "mult": prm["mult"], "fleet": prm["fleet"]})


def _scene_from_request(request, wm):
    """Scena „palviz.blender-flow" wg parametrów URL (wspólne dla eksportu i animacji).

    ?batch=<id>|latest — kompletacja wg importu aktywności pickerów; inna wartość/brak —
    symulacja demo. ?snapshot=<id>|latest — zajętość/blokady z eksportu SAP. ?pallets=0 —
    bez palet w lokalizacjach. ?forklifts=0–10 (demo), ?pickers=1–10, ?picks=1–25.
    ?wt=… — wózki z zadań EWM (patrz `_wt_window`)."""
    batch_param = (request.GET.get("batch") or "").strip()
    batch = None
    if batch_param == "latest":
        batch = PickerActivityBatch.objects.order_by("-uploaded_at").first()
    elif batch_param.isdigit():
        batch = get_object_or_404(PickerActivityBatch, pk=int(batch_param))
    sim = _sim_scene(request, wm)
    if sim is not None:
        return sim
    snap_param = (request.GET.get("snapshot") or "").strip()
    snapshot = None
    if snap_param == "latest":
        snapshot = WarehouseSnapshot.objects.order_by("-uploaded_at").first()
    elif snap_param.isdigit():
        snapshot = get_object_or_404(WarehouseSnapshot, pk=int(snap_param))
    return build_scene_for_model(
        wm, batch, snapshot=snapshot,
        forklifts=_clamped(request.GET.get("forklifts"), 3, 0, MAX_FORKLIFTS),
        max_pickers=_clamped(request.GET.get("pickers"), 6, 1, MAX_PICKERS),
        max_picks=_clamped(request.GET.get("picks"), 12, 1, MAX_PICKS),
        with_pallets=request.GET.get("pallets") != "0", wt=_wt_window(request))


@_planner
def warehouse_model_blender_json(request, pk):
    """Scena „palviz.blender-flow" (JSON do pobrania) dla skryptu Blendera / Blender MCP."""
    wm = get_object_or_404(WarehouseModel, pk=pk)
    resp = JsonResponse(_scene_from_request(request, wm), json_dumps_params={"ensure_ascii": False})
    resp["Content-Disposition"] = f'attachment; filename="palviz_model_{wm.pk}_blender.json"'
    return resp


@_planner
def warehouse_model_flow_json(request, pk):
    """Ta sama scena dla animacji przepływów w aplikacji (fetch z widoku 3D modelu)."""
    wm = get_object_or_404(WarehouseModel, pk=pk)
    return JsonResponse(_scene_from_request(request, wm), json_dumps_params={"ensure_ascii": False})
