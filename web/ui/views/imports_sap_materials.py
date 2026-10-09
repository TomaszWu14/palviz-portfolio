# Import danych materiałowych SAP (eksport BW „SAP_Dane_materialowe”) → MaterialMaster:
# hierarchia asortymentu + przeliczniki z objętościami i wagami (projektowanie magazynu).
from .core import _md_role, messages, redirect, require_POST
from ..sap_materials import parse_workbook, stats, upsert

__all__ = ["excel_import_sap_materials"]

MAX_MB = 50


@_md_role
@require_POST
def excel_import_sap_materials(request):
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Nie wybrano pliku.")
        return redirect("ui:planner_excel_templates")
    if not f.name.lower().endswith((".xlsm", ".xlsx")):
        messages.error(request, "Oczekiwano pliku .xlsm lub .xlsx (eksport „SAP_Dane_materialowe”).")
        return redirect("ui:planner_excel_templates")
    if f.size > MAX_MB * 1024 * 1024:
        messages.error(request, f"Plik zbyt duży (max {MAX_MB} MB).")
        return redirect("ui:planner_excel_templates")
    try:
        materials = parse_workbook(f)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui:planner_excel_templates")
    except Exception as exc:                               # uszkodzony plik / nie-Excel
        messages.error(request, f"Nie udało się odczytać pliku: {exc}")
        return redirect("ui:planner_excel_templates")
    created, updated = upsert(materials)
    s = stats(materials)
    messages.success(request, (
        f"Dane materiałowe SAP: {s['total']} materiałów ({created} nowych, {updated} zaktualizowanych) — "
        f"z hierarchią {s['with_hierarchy']}, z przelicznikiem na karton {s['with_carton']}, "
        f"na paletę {s['with_pallet']}."))
    return redirect("ui:planner_excel_templates")
