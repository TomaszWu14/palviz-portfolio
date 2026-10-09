# Agregator zgodnosci (W3): pociety na submoduly, importy dzialaja bez zmian.
from .warehouse_map_core import (
    warehouse_map, warehouse_where_is, warehouse_map_upload,
    warehouse_picking_route, warehouse_location_contents,
    warehouse_aisle_config, warehouse_layout_features,
    # underscore-prefixed helpers re-exported through this facade — consumed
    # directly by wh3d/tests/test_antresola_excluded.py, test_nonrack_render.py,
    # test_picking_route.py, test_special_zones.py (import from .warehouse_map,
    # not .warehouse_map_core) — must stay reachable here (zero behavior change).
    _RACK_RE, _hall_features_data, _serpentine_route,
)
from .warehouse_map_detail import warehouse_map_detail, _is_antresola, _special_zones  # noqa: F401
from .warehouse_map_layouts import (
    warehouse_layout_upload, warehouse_layout_seed, warehouse_layout_delete,
    warehouse_layout_list_upload, warehouse_demo_seed, warehouse_map_delete,
)

__all__ = [
    'warehouse_map',
    'warehouse_where_is',
    'warehouse_location_contents',
    'warehouse_picking_route',
    'warehouse_map_upload',
    'warehouse_map_detail',
    'warehouse_aisle_config',
    'warehouse_layout_features',
    'warehouse_layout_upload',
    'warehouse_layout_seed',
    'warehouse_layout_delete',
    'warehouse_layout_list_upload',
    'warehouse_demo_seed',
    'warehouse_map_delete',
    # helpery konsumowane przez wh3d/tests przez tę fasadę (zero behavior change)
    '_RACK_RE', '_hall_features_data', '_serpentine_route',
    '_is_antresola', '_special_zones',
]
