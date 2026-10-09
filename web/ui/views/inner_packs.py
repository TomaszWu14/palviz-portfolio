# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from .core import (
    _planner, InnerPack, Count, render, _md_role, get_object_or_404, InnerPackForm,
    messages, redirect
)


@_planner
def planner_inner_packs(request):
    packs = InnerPack.objects.annotate(cartons_cnt=Count("cartons"))
    return render(request, "ui/planner/inner_packs.html",
                  {"packs": packs, "active_tab": "inner_packs"})

@_md_role
def planner_inner_pack_form(request, pk=None):
    instance = get_object_or_404(InnerPack, pk=pk) if pk else None
    if request.method == "POST":
        form = InnerPackForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, "Opakowanie zbiorcze zapisane." if pk else "Opakowanie zbiorcze dodane.")
            return redirect("ui:planner_inner_packs")
    else:
        form = InnerPackForm(instance=instance)
    return render(request, "ui/planner/inner_pack_form.html", {"form": form, "instance": instance})

@_md_role
def planner_inner_pack_delete(request, pk):
    pack = get_object_or_404(InnerPack, pk=pk)
    if request.method == "POST":
        pack.delete()
        messages.success(request, f'Opakowanie "{pack.name}" usunięte.')
        return redirect("ui:planner_inner_packs")
    return render(request, "ui/planner/inner_pack_confirm_delete.html", {"pack": pack})

__all__ = [
    'planner_inner_packs',
    'planner_inner_pack_form',
    'planner_inner_pack_delete',
]
