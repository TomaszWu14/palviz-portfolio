"""„Wykryj z EWM” (podgląd propozycji → zapis) i raport zgodności modelu z EWM (+ XLSX)."""
from ui.views.core import (
    _md_role, _planner, get_object_or_404, messages, redirect, render, require_POST, WarehouseModel,
)
from ui.views.core.xlsx import _finalize_xlsx, _make_xlsx_response
from wh3d.ewm_service import active_master, apply_proposal, compliance_for_model, detect_for_model

NO_MASTER = ("Brak aktywnego mastera lokalizacji — wgraj eksport EWM (Magazyn 3D → Master lokalizacji), "
             "potem wróć do „Wykryj z EWM”.")
STATUS = {"ok": ("Zgodne", "badge-green"), "diff": ("Rozbieżności", "badge-red"),
          "no_template": ("Rząd bez szablonu", "badge-yellow"), "no_row": ("Brak rzędu na planie", "badge-gray")}
LIST_LIMIT = 100   # kodów na listę w HTML; pełne listy w XLSX


@_planner
def warehouse_model_detect(request, pk):
    wm = get_object_or_404(WarehouseModel, pk=pk)
    batch = active_master()
    ctx = {"wm": wm, "batch": batch, "no_master": NO_MASTER}
    if batch:
        proposal = detect_for_model(wm, batch)
        names = {t["key"]: t["name"] for t in proposal["templates"]}
        ctx.update({
            "proposal": proposal,
            "new_templates": [t for t in proposal["templates"] if not t["pk"]],
            "old_templates": [t for t in proposal["templates"] if t["pk"]],
            "rows": [{**r, "template_name": names[r["template"]],
                      "bay_ov": sum(1 for o in r["overrides"] if not o["letter"]),
                      "loc_ov": sum(1 for o in r["overrides"] if o["letter"])} for r in proposal["rows"]],
            "total_overrides": sum(len(r["overrides"]) for r in proposal["rows"]),
        })
    return render(request, "ui/warehouse_model/ewm_detect.html", ctx)


@require_POST
@_md_role
def warehouse_model_detect_save(request, pk):
    wm = get_object_or_404(WarehouseModel, pk=pk)
    batch = active_master()
    if not batch:
        messages.error(request, NO_MASTER)
        return redirect("ui:warehouse_model_detect", pk=wm.pk)
    created, rows, overrides = apply_proposal(wm, detect_for_model(wm, batch))
    messages.success(request, f"Zapisano „Wykryj z EWM”: {rows} rzędów, {created} nowych szablonów, "
                              f"{overrides} wyjątków.")
    return redirect("ui:warehouse_model_compliance", pk=wm.pk)


@_planner
def warehouse_model_compliance(request, pk):
    wm = get_object_or_404(WarehouseModel, pk=pk)
    batch = active_master()
    report = compliance_for_model(wm, batch)
    if request.GET.get("format") == "xlsx":
        return _compliance_xlsx(wm, report)
    for a in report["aisles"]:
        a["label"], a["badge"] = STATUS[a["status"]]
    return render(request, "ui/warehouse_model/ewm_compliance.html", {
        "wm": wm, "batch": batch, "report": report, "no_master": NO_MASTER, "limit": LIST_LIMIT})


def _safe(v):
    """Ucieczka przed wstrzyknięciem formuły XLSX: string zaczynający się od =+-@ dostaje wiodący apostrof."""
    return "'" + v if isinstance(v, str) and v[:1] in "=+-@" else v


def _compliance_xlsx(wm, report):
    wb, ws, response = _make_xlsx_response(f"zgodnosc_ewm_model_{wm.pk}.xlsx")
    ws.title = "Zgodność"
    ws.append(["Strefa", "Przejście", "Status", "Na planie", "W EWM", "Zgodne", "Zgodność %",
               "Tylko plan", "Tylko EWM", "Duplikaty"])
    for a in report["aisles"]:
        ws.append([_safe(a["zone"]), _safe(a["aisle"]), _safe(STATUS[a["status"]][0]), a["plan"], a["ewm"],
                   a["matched"], a["pct"], len(a["plan_only"]), len(a["ewm_only"]), len(a["duplicates"])])
    diff = wb.create_sheet("Rozbieżności")
    diff.append(["Strefa", "Przejście", "Kod", "Rodzaj"])
    for a in report["aisles"]:
        for kind, key in (("na planie, brak w EWM", "plan_only"), ("w EWM, brak na planie", "ewm_only"),
                          ("duplikat", "duplicates")):
            for code in a[key]:
                diff.append([_safe(a["zone"]), _safe(a["aisle"]), _safe(code), _safe(kind)])
    return _finalize_xlsx(wb, ws, response)


__all__ = ["warehouse_model_detect", "warehouse_model_detect_save", "warehouse_model_compliance"]
