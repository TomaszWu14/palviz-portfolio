from functools import wraps

# ── FROZEN CONTRACT: group-name strings ──────────────────────────────────────
# These 8 string values ARE the contract, not just the constant names. They are:
#   1. Row values in Django's auth_group table (renaming a string ≠ renaming a
#      group — it silently creates a NEW empty group and strips everyone's access).
#   2. The vocabulary the SSO backend maps IdP claims onto (oidc.py imports
#      ALL_GROUPS / GROUP_ADMIN to sync users from Keycloak role claims).
#   3. Imported by 9+ modules (context_processors, models, notifications, oidc,
#      platform_modules, admin/base/hu_print/warehouse_search views, mgmt cmds).
# RULE: never change a string on the right-hand side. Add a new GROUP_* constant
# for a new group; to retire one, migrate auth_group rows + SSO claim mapping
# first. The Python identifier on the left may be refactored freely; the literal
# may not. See memory: genuine-cut-vertices.
# ─────────────────────────────────────────────────────────────────────────────
GROUP_ADMIN       = "Administratorzy"
GROUP_MASTER_DATA = "Master Data"
GROUP_TRANSPORT   = "Transport"
GROUP_CONTROLLER  = "Kontrola HU"
GROUP_LEADER      = "Lider kontroli"
GROUP_VIEWER      = "Podgląd"
GROUP_CLIENT      = "Obsługa klienta"   # wąska rola: tylko baza klientów
GROUP_WAREHOUSE   = "Magazyn"           # operator magazynowy: druk etykiet HU
GROUP_OPTIMIZER   = "Optymalizacja kartonów"   # operator optymalizacji wypełnienia palet

ALL_GROUPS = [GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT, GROUP_CONTROLLER,
              GROUP_LEADER, GROUP_VIEWER, GROUP_CLIENT, GROUP_WAREHOUSE, GROUP_OPTIMIZER]

# Role → label mapping for UI
ROLE_LABELS = {
    GROUP_ADMIN:       ("Administratorzy", "#ef4444"),
    GROUP_MASTER_DATA: ("Master Data",     "#3b82f6"),
    GROUP_TRANSPORT:   ("Transport",       "#f59e0b"),
    GROUP_CONTROLLER:  ("Kontrola HU",     "#7c3aed"),
    GROUP_LEADER:      ("Lider kontroli",  "#0e5e74"),
    GROUP_VIEWER:      ("Podgląd",         "#6b7280"),
    GROUP_CLIENT:      ("Obsługa klienta", "#0d9488"),
    GROUP_WAREHOUSE:   ("Magazyn",         "#0891b2"),
    GROUP_OPTIMIZER:   ("Optymalizacja kartonów", "#d97706"),
}

# Module → allowed roles (write access). Read access = any authenticated user unless marked.
MODULE_ROLES = {
    "products_write":      [GROUP_ADMIN, GROUP_MASTER_DATA],
    "cartons_write":       [GROUP_ADMIN, GROUP_MASTER_DATA],
    "inner_packs_write":   [GROUP_ADMIN, GROUP_MASTER_DATA],
    "categories_write":    [GROUP_ADMIN, GROUP_MASTER_DATA],
    "instructions_write":  [GROUP_ADMIN, GROUP_MASTER_DATA],
    "reports_manage":      [GROUP_ADMIN, GROUP_MASTER_DATA],
    "carriers_write":      [GROUP_ADMIN, GROUP_TRANSPORT],
    "shipments_write":     [GROUP_ADMIN, GROUP_TRANSPORT],
    "shipments_read":      [GROUP_ADMIN, GROUP_TRANSPORT],
    "stock_write":         [GROUP_ADMIN, GROUP_MASTER_DATA],
    "occupancy_read":      [GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT, GROUP_VIEWER],
    "warehouse_model":     [GROUP_ADMIN, GROUP_MASTER_DATA],
    "excel_templates":     [GROUP_ADMIN, GROUP_MASTER_DATA],
    "calculator":          [GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT],
    "user_management":     [GROUP_ADMIN],
    "hu_print":            [GROUP_ADMIN, GROUP_WAREHOUSE],
    "carton_opt":          [GROUP_ADMIN, GROUP_OPTIMIZER, GROUP_MASTER_DATA],
}


def has_role(user, *group_names):
    """Return True if user is superuser or belongs to one of group_names."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=group_names).exists()


def is_app_admin(user):
    """Pełny administrator aplikacji = superuser LUB grupa Administratorzy. Jedno źródło
    prawdy dla nadpisań na poziomie obiektu (zarządzanie cudzą treścią) — używaj tego
    zamiast gołego `user.is_superuser`, żeby admin z grupy też miał spójny dostęp."""
    return has_role(user, GROUP_ADMIN)


def is_control_only(user):
    """True for a HU-control operator with no planner access — they live entirely in
    the control module and are sent straight to its scanner on login."""
    if not user or not user.is_authenticated or user.is_superuser:
        return False
    names = set(user.groups.values_list("name", flat=True))
    return bool(names & {GROUP_CONTROLLER, GROUP_LEADER}) and not (
        names & {GROUP_ADMIN, GROUP_TRANSPORT, GROUP_MASTER_DATA})


# Display precedence (most privileged first) — distinct from ALL_GROUPS, so a user who is
# both Lider kontroli and Kontrola HU shows the higher Leader badge, not the controller one.
_ROLE_PRECEDENCE = [GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT, GROUP_LEADER,
                    GROUP_CONTROLLER, GROUP_OPTIMIZER, GROUP_WAREHOUSE, GROUP_CLIENT,
                    GROUP_VIEWER]


def get_user_primary_role(user):
    """Return the label of the highest-precedence matching role for display purposes."""
    if not user or not user.is_authenticated:
        return None
    if user.is_superuser:
        return ROLE_LABELS[GROUP_ADMIN]
    names = set(user.groups.values_list("name", flat=True))
    for group_name in _ROLE_PRECEDENCE:
        if group_name in names:
            return ROLE_LABELS[group_name]
    return ("Brak roli", "#6b7280")


def role_required(*group_names):
    """Decorator: require login + group membership (superusers always pass)."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login
                return redirect_to_login(request.get_full_path(), "/login/")
            if has_role(request.user, *group_names):
                return view_func(request, *args, **kwargs)
            from django.shortcuts import render
            return render(request, "ui/403.html", status=403)
        return wrapped
    return decorator


# Convenience decorators
_admin_only    = role_required(GROUP_ADMIN)
_master_data   = role_required(GROUP_ADMIN, GROUP_MASTER_DATA)
_transport_mgr = role_required(GROUP_ADMIN, GROUP_TRANSPORT)
_controller    = role_required(GROUP_ADMIN, GROUP_CONTROLLER, GROUP_LEADER)
_leader        = role_required(GROUP_ADMIN, GROUP_LEADER)      # KPI + service privileges
_md_or_tr      = role_required(GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT)
# Warehouse stock (imported HU) is shared by HU control and 3D-map modelling.
_md_or_control = role_required(GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_CONTROLLER, GROUP_LEADER)
# Customer database: managed by the narrow "Obsługa klienta" role plus the broader roles.
_customer_mgr  = role_required(GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT, GROUP_CLIENT)
# Warehouse label printing (HU labels): the dedicated "Magazyn" operator plus admin.
_warehouse     = role_required(GROUP_ADMIN, GROUP_WAREHOUSE)
# Optymalizacja kartonów: dedykowany operator + admin/master data.
_optimizer     = role_required(GROUP_ADMIN, GROUP_OPTIMIZER, GROUP_MASTER_DATA)
_any_role      = role_required(*ALL_GROUPS)
