# Agregator zgodnosci (W3): pociety na submoduly, importy dzialaja bez zmian.
from .products_crud import (
    planner_products, planner_product_form, planner_product_delete,
    planner_product_csv_template, planner_product_import,
)
from .products_import_md import (
    planner_master_data_import, planner_master_data_purge, planner_product_hierarchy,
)
from .products_variant import product_switch_variant


__all__ = [
    'planner_products',
    'planner_product_form',
    'planner_product_delete',
    'planner_product_csv_template',
    'planner_product_import',
    'planner_master_data_import',
    'planner_master_data_purge',
    'planner_product_hierarchy',
    'product_switch_variant',
]
