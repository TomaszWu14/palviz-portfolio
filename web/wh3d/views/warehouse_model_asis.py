# Model OBECNEGO magazynu (w metrach) generowany z mapy lokalizacji + mastera lokalizacji.
from ui.views.core import (
    _md_role, messages, redirect, require_POST, WarehouseLayout,
    WarehouseLocationMasterBatch,
)
from wh3d.model_from_layout import create_model_from_layout


@require_POST
@_md_role
def warehouse_model_from_layout(request):
    """Tworzy model magazynu z AKTYWNEJ mapy lokalizacji i aktywnego mastera lokalizacji
    (podziałka gniazda jak w mapie 3D). Pozycje dopracowuje się potem w „Edytuj współrzędne"."""
    layout = WarehouseLayout.objects.filter(is_active=True).order_by("-uploaded_at").first()
    if layout is None:
        messages.error(request, "Brak aktywnej mapy lokalizacji — wgraj mapę w Magazyn 3D.")
        return redirect("ui:warehouse_model_list")
    master = WarehouseLocationMasterBatch.objects.filter(is_active=True).order_by("-uploaded_at").first()
    wm, rep = create_model_from_layout(layout, master)
    if not rep["racks"]:
        messages.warning(request, "Mapa nie zawiera rozpoznawalnych kodów regałowych — model jest pusty.")
    else:
        msg = (f"Utworzono model obecnego magazynu: {rep['racks']} regałów z {rep['cells']} lokalizacji, "
               f"hala {rep['floor'][0]}×{rep['floor'][1]} m, podziałka gniazda {rep['slot_mm']} mm.")
        if not master:
            msg += " Brak aktywnego mastera lokalizacji — wysokości i głębokości domyślne."
        messages.success(request, msg)
    if rep["skipped_codes"]:
        messages.info(request, f"Pominięto {rep['skipped_codes']} kodów spoza regałów "
                               f"(strefy blokowe, doki…) — dodaj je jako elementy hali.")
    if rep["multi_row_aisles"]:
        messages.warning(request, "Alejki rozłożone na kilka rzędów mapy (sprawdź pozycje): "
                                  + ", ".join(rep["multi_row_aisles"][:10]))
    return redirect("ui:warehouse_model_view", pk=wm.pk)
