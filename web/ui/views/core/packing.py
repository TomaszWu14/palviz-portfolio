# Agregator zgodnosci (W3): pociety na submoduly, importy dzialaja bez zmian.
from .packing_core import (
    _build_pallet, _form_max_height, _compute_cog, _eval_layouts, _meta_from_rec,
    _carton_from_rec, _render_panel, _recalculate_instruction, _save_instruction_from_carton,
    _optimization_hints,
    _layers_by_weight,  # noqa: F401 (not star-exported, but test suite imports it directly)
)
from .packing_optimize import (
    _build_loc_fits, _optimize_packaging, _factorizations, _best_box, _suggest_packaging,
    _build_stack_base,
)

__all__ = [
    '_build_loc_fits',
    '_build_pallet',
    '_form_max_height',
    '_compute_cog',
    '_optimization_hints',
    '_eval_layouts',
    '_meta_from_rec',
    '_carton_from_rec',
    '_render_panel',
    '_recalculate_instruction',
    '_save_instruction_from_carton',
    '_optimize_packaging',
    '_factorizations',
    '_best_box',
    '_suggest_packaging',
    '_build_stack_base',
]
