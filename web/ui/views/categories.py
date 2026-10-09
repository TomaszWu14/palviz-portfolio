# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from .core import (
    _planner, ProductCategory, Count, Q, render, _md_role, get_object_or_404, messages,
    redirect
)


@_planner
def planner_categories(request):
    cats = ProductCategory.objects.annotate(
        product_cnt=Count("products", filter=Q(products__is_active=True))
    )
    return render(request, "ui/planner/categories.html", {"categories": cats, "active_tab": "products"})

@_md_role
def planner_category_form(request, pk=None):
    from ..forms import ProductCategoryForm
    obj = get_object_or_404(ProductCategory, pk=pk) if pk else None
    if request.method == "POST":
        form = ProductCategoryForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Kategoria zapisana.")
            return redirect("ui:planner_categories")
    else:
        form = ProductCategoryForm(instance=obj)
    return render(request, "ui/planner/category_form.html", {"form": form, "object": obj})

@_md_role
def planner_category_delete(request, pk):
    cat = get_object_or_404(ProductCategory, pk=pk)
    if request.method == "POST":
        cat.delete()
        messages.success(request, "Kategoria usunięta.")
        return redirect("ui:planner_categories")
    return render(request, "ui/planner/category_confirm_delete.html", {"object": cat})

__all__ = [
    'planner_categories',
    'planner_category_form',
    'planner_category_delete',
]
