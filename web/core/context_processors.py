from django.conf import settings

from .platform_modules import can_open_module
from .roles import (GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT,
                    GROUP_CONTROLLER, GROUP_LEADER, GROUP_WAREHOUSE, GROUP_OPTIMIZER,
                    ALL_GROUPS)


def branding(request):
    """Expose the env-driven brand name + the user's unread-notification count (bell)
    to every template. Read APP_NAME from settings at call time so it can be overridden
    per-request in tests and changed by an env var without code edits."""
    ctx = {"app_name": getattr(settings, "APP_NAME", "GROOVE"), "unread_notifications": 0,
           "oidc_enabled": getattr(settings, "OIDC_ENABLED", False),
           "admin_contact_email": getattr(settings, "ADMIN_CONTACT_EMAIL", ""),
           # Link „nie pamiętasz hasła?" ma sens tylko, gdy serwer umie wysłać e-mail.
           "password_reset_enabled": bool(getattr(settings, "EMAIL_HOST", "")),
           # Preferencje UI (motyw/język) — zasilają skrypt no-flash i przełączniki.
           "ui_theme": "", "ui_lang": ""}
    user = getattr(request, "user", None)
    ctx["nav_modules"] = []
    if user is not None and user.is_authenticated:
        prof = getattr(user, "profile", None)
        if prof is not None:
            ctx["ui_theme"] = prof.ui_theme or ""
            ctx["ui_lang"] = prof.ui_lang or ""
        from ui.models import Notification
        ctx["unread_notifications"] = Notification.objects.filter(
            recipient=user, is_read=False).count()
        # Górna nawigacja = dokładnie te same moduły co kafelki huba (jedno źródło:
        # platform_modules.MODULES), więc nagłówki zawsze pokrywają się z hubem.
        from django.urls import reverse
        from .platform_modules import modules_for, nav_grouped
        from ui.nav_icons import nav_icon_svg          # zwykły moduł, nie templatetag
        ctx["nav_modules"] = [(m, reverse(f"ui:{m.url_name}")) for m in modules_for(user)]
        # Pasek nawigacji 1b: sekcje (grupy z panelami) + pozycje bezpośrednie (Zadania).
        # Ikonę SVG liczymy TU (Python, plain import) — base.html nie zależy od custom-filtra
        # ani od maszynerii templatetags (stabilne pod --parallel/coverage w CI).
        groups, direct = nav_grouped(user)
        ctx["nav_groups"] = [(label, [(m, u, nav_icon_svg(m.key)) for m, u in items])
                             for label, items in groups]
        ctx["nav_direct"] = [(m, u, nav_icon_svg(m.key)) for m, u in direct]
    return ctx


def section_theme(request):
    """Motyw sekcji operatora (UserProfile.section) → cała aplikacja skanera przyjmuje
    kolor przewoźnika. Brak sekcji / niezalogowany → {"section_theme": None} (fallback teal)."""
    theme = None
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        from ui.theme import theme_for_carrier
        prof = getattr(user, "profile", None)
        section = getattr(prof, "section", "") or ""
        theme = theme_for_carrier(section)
        if theme is not None:
            # can_switch: >1 dozwolona strefa → badge w nagłówku linkuje do wyboru strefy.
            allowed = getattr(prof, "allowed_sections_list", []) or []
            theme = {**theme, "section_code": section.upper(),
                     "can_switch": len(allowed) > 1}
    return {"section_theme": theme}


def user_roles(request):
    """Inject role flags into every template context.

    Resolves the user's group set with a SINGLE query and derives every flag in
    Python, instead of calling has_role() 8× (which would hit the DB per call).
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {k: False for k in (
            "role_is_admin", "role_is_master_data", "role_is_transport", "role_is_viewer",
            "role_is_control", "role_is_leader", "role_is_planner", "role_is_warehouse",
            "can_write_products", "can_write_shipments", "can_use_calculator", "can_manage_users",
            "can_open_matinfo", "can_open_magazyn", "role_is_optimizer",
        )}

    is_super = user.is_superuser
    groups = set(user.groups.values_list("name", flat=True))   # one query

    def has(*names):
        return is_super or not groups.isdisjoint(names)

    return {
        "role_is_admin":       has(GROUP_ADMIN),
        "role_is_master_data": has(GROUP_ADMIN, GROUP_MASTER_DATA),
        "role_is_transport":   has(GROUP_ADMIN, GROUP_TRANSPORT),
        "role_is_control":     has(GROUP_ADMIN, GROUP_CONTROLLER, GROUP_LEADER),
        "role_is_leader":      has(GROUP_ADMIN, GROUP_LEADER),
        "role_is_warehouse":   has(GROUP_ADMIN, GROUP_WAREHOUSE),
        "role_is_optimizer":   has(GROUP_ADMIN, GROUP_OPTIMIZER, GROUP_MASTER_DATA),
        # role_is_planner gates warehouse-modelling tools (e.g. the 3D editor link);
        # same owners as warehouse_model write access in roles.py.
        "role_is_planner":     has(GROUP_ADMIN, GROUP_MASTER_DATA),
        # role_is_viewer = true for anyone with at least one PalViz role.
        "role_is_viewer":      has(*ALL_GROUPS),
        "can_write_products":  has(GROUP_ADMIN, GROUP_MASTER_DATA),
        "can_write_shipments": has(GROUP_ADMIN, GROUP_TRANSPORT),
        "can_use_calculator":  has(GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT),
        "can_manage_users":    has(GROUP_ADMIN),
        # Linki do modułów = ta sama reguła co strażnik widoku (@module_required): rola ALBO
        # nadpisanie per użytkownik (UserModuleAccess) — B-008. Leniwie (szablon woła callable),
        # więc zapytanie o nadpisania leci tylko na stronach, które flagi używają.
        # MATinfo = moduł phv (zakładka w skanerze); magazyn = mapa 3D (link na stronie stanów, B-006).
        "can_open_matinfo": lambda: can_open_module(user, "phv"),
        "can_open_magazyn": lambda: can_open_module(user, "magazyn"),
    }
