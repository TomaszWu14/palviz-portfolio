# Agregator zgodnosci (W3): pociety na submoduly, importy dzialaja bez zmian.
from .admin_users import (
    admin_panel, admin_users, admin_user_form, admin_users_import, admin_user_delete,
    admin_user_toggle_active, admin_module_access, admin_role_matrix, admin_control_zones,
    _online_user_ids,  # noqa: F401 (not star-exported, but huctl/views/hu_leader.py imports it directly)
)
from .admin_zaria import (
    admin_zaria_panel, admin_zaria_models, admin_zaria_model_delete,
    admin_zaria_role_access, admin_zaria_user_access, admin_zaria_config,
    admin_zaria_usage, admin_zaria_usage_export_xlsx, admin_zaria_usage_export_csv,
    admin_zaria_audit, admin_zaria_test, admin_access_audit,
)
from .admin_misc import admin_positions, admin_import_status, admin_position_form

__all__ = [
    'admin_panel',
    'admin_users',
    'admin_user_form',
    'admin_users_import',
    'admin_user_delete',
    'admin_user_toggle_active',
    'admin_module_access',
    'admin_role_matrix',
    'admin_positions',
    'admin_import_status',
    'admin_position_form',
    'admin_control_zones',
    'admin_zaria_panel',
    'admin_zaria_models',
    'admin_zaria_model_delete',
    'admin_zaria_role_access',
    'admin_zaria_user_access',
    'admin_zaria_config',
    'admin_zaria_usage',
    'admin_zaria_usage_export_xlsx',
    'admin_zaria_usage_export_csv',
    'admin_zaria_audit',
    'admin_zaria_test',
    'admin_access_audit',
]
