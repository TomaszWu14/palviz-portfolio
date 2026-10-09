# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from .core import (_md_role, require_POST, get_object_or_404, Product, messages, _safe_next)


@_md_role
@require_POST
def product_switch_variant(request, pk: int):
    """E2: przełącz obowiązujący wariant paletyzacji A↔B. Kto/kiedy — HistoricalRecords
    na Product (HistoryRequestMiddleware zapisuje użytkownika)."""
    product = get_object_or_404(Product, pk=pk)
    target = "B" if product.active_variant == "A" else "A"
    if not product.instructions.filter(is_active=True, variant=target).exists():
        messages.error(request, f"Brak aktywnej instrukcji w wariancie {target} — najpierw ją utwórz.")
        return _safe_next(request, "ui:planner_products")
    product.active_variant = target
    product.save(update_fields=["active_variant", "updated_at"])
    messages.success(request, f"Obowiązuje wariant {target} paletyzacji dla {product.code}.")
    return _safe_next(request, "ui:planner_products")

__all__ = [
    'product_switch_variant',
]
