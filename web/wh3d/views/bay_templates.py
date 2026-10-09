"""Szablony gniazd (słup regału: palety na belce, poziomy od podłogi) — lista, formularz, usuwanie."""
from django.core.exceptions import ValidationError
from django.db.models import Count, ProtectedError

from ui.views.core import _md_role, _planner, get_object_or_404, messages, redirect, render, require_POST
from wh3d.models import BayTemplate, WarehouseRackType

EMPTY_LEVEL_ROWS = 3   # puste wiersze na nowe poziomy w formularzu


def _int(raw):
    try:
        return int(float(str(raw).replace(",", ".")))
    except (TypeError, ValueError, OverflowError):
        return 0


def _levels_from_post(post):
    """Wiersze tabeli poziomów (lvl-<i>-letter/height/type/split/kg) → lista poziomów; pusta litera = pomiń."""
    levels, i = [], 0
    while f"lvl-{i}-letter" in post:
        letter = post.get(f"lvl-{i}-letter", "").strip().upper()
        if letter:
            levels.append({"letter": letter, "height_mm": _int(post.get(f"lvl-{i}-height")),
                           "ewm_type": post.get(f"lvl-{i}-type", "").strip()[:20],
                           "split": post.get(f"lvl-{i}-split") == "1",
                           "max_kg": _int(post.get(f"lvl-{i}-kg"))})
        i += 1
    return levels


@_planner
def bay_template_list(request):
    templates = BayTemplate.objects.annotate(n_racks=Count("racks", distinct=True),
                                             n_bays=Count("bay_overrides", distinct=True))
    return render(request, "ui/warehouse_model/bay_templates.html", {"templates": templates})


@_md_role
def bay_template_form(request, pk=None):
    obj = get_object_or_404(BayTemplate, pk=pk) if pk else BayTemplate()
    if request.method == "POST":
        obj.name = request.POST.get("name", "").strip()[:100]
        obj.pallets_per_beam = max(0, _int(request.POST.get("pallets_per_beam")))
        obj.beam_mm = _int(request.POST.get("beam_mm"))
        obj.depth_mm = _int(request.POST.get("depth_mm"))
        obj.notes = request.POST.get("notes", "").strip()[:200]
        obj.levels = _levels_from_post(request.POST)
        try:
            obj.full_clean()
        except ValidationError as exc:
            for msg in exc.messages:
                messages.error(request, msg)
        else:
            obj.save()
            messages.success(request, f"Szablon „{obj.name}” zapisany.")
            return redirect("ui:bay_template_list")
    return render(request, "ui/warehouse_model/bay_template_form.html", {
        "obj": obj,
        "rows": list(obj.levels) + [{}] * EMPTY_LEVEL_ROWS,
        "ewm_types": WarehouseRackType.objects.values_list("code", flat=True),
    })


@require_POST
@_md_role
def bay_template_delete(request, pk):
    obj = get_object_or_404(BayTemplate, pk=pk)
    try:
        obj.delete()
    except ProtectedError:
        messages.error(request, f"Szablon „{obj.name}” jest użyty jako wyjątek gniazda — "
                                "najpierw zmień te gniazda (albo ponów „Wykryj z EWM”).")
    else:
        messages.success(request, f"Szablon „{obj.name}” usunięty.")
    return redirect("ui:bay_template_list")


__all__ = ["bay_template_list", "bay_template_form", "bay_template_delete"]
