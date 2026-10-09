"""GROOVE platform — the modules ("tabs") a user picks after logging in.

Everything lives in this single repo/app (`ui`); each module is just a role-gated entry
point into one functional area. `modules_for(user)` returns the modules a user may open,
which drives the GROOVE hub (home picker).
"""
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from .roles import (GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT,
                    GROUP_CONTROLLER, GROUP_LEADER, GROUP_VIEWER, GROUP_CLIENT,
                    GROUP_WAREHOUSE, GROUP_OPTIMIZER)


@dataclass(frozen=True)
class Module:
    key: str             # stable id
    name: str            # UI label (Polish)
    description: str     # tile subtitle (Polish)
    url_name: str        # entry point, reversed as ui:<url_name>
    icon: str            # emoji shown on the tile
    color: str           # tile accent colour
    roles: tuple = ()    # groups allowed ([] → any logged-in user)
    group: str = ""      # sekcja w pasku nawigacji 1b (puste = pozycja bezpośrednia)


# Order = display order on the hub.
MODULES = [
    Module("data_center", _("Data Center"), _("Master data: opakowania, stock, klienci, produkty"),
           "data_center", "🗄️", "#3b82f6", (GROUP_ADMIN, GROUP_MASTER_DATA), "Dane"),
    Module("paletyzacja", _("Paletyzacja"), _("Jak układać ładunek i dobierać opakowanie"),
           "planner_calc_index", "🧮", "#0ea5e9", (GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT), "Operacje"),
    Module("transport", _("Wycena przesyłek"), _("Objętość, ładunek, przewoźnicy, wyceny"),
           "planner_shipments", "🚚", "#f59e0b", (GROUP_ADMIN, GROUP_TRANSPORT), "Transport"),
    Module("magazyn", _("Magazyn 3D"), _("Lokalizacje, widok 3D, kompletacja i analiza"),
           "warehouse_map", "🏭", "#10b981", (GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT, GROUP_VIEWER), "Magazyn"),
    Module("kontrola_hu", _("Kontrola HU"), _("Skaner, kontrola zawartości, KPI i raporty"),
           "hu_control_hub", "📲", "#7c3aed", (GROUP_ADMIN, GROUP_CONTROLLER, GROUP_LEADER), "Operacje"),
    Module("wydruk_hu", _("Wydruk HU"), _("Etykiety HU (Zebra/ZPL) — druk sekwencyjny per projekt"),
           "hu_print_home", "🏷️", "#06b6d4", (GROUP_ADMIN, GROUP_WAREHOUSE), "Operacje"),
    Module("zadania", _("Zadania i powiadomienia"), _("Zadania zespołu i alerty o niezgodnościach stocku"),
           "tasks_home", "🔔", "#e11d48", (GROUP_ADMIN, GROUP_MASTER_DATA)),
    Module("klienci", _("Baza klientów"), _("Klienci / odbiorcy i ich wymagania dostaw"),
           "planner_customers", "🤝", "#0d9488",
           (GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT, GROUP_CLIENT), "Dane"),
    # Otwarte dla wszystkich ról wewnętrznych (bez GROUP_CLIENT). Nowe role widzą kafelek,
    # ale czat działa dopiero po nadaniu im ZariaModelRoleAccess w panelu (krok wdrożeniowy);
    # cap pieniężny ożywa migracją danych 0100.
    Module("zaria", _("ZARIA"), _("Asystent AI — czat z modelami LLM"),
           "zaria_home", "🤖", "#d946ef",
           (GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT, GROUP_CONTROLLER,
            GROUP_LEADER, GROUP_VIEWER, GROUP_WAREHOUSE), "Dane"),
    Module("wysylka_ukraina", _("Wysyłka UKRAINA"), _("Monitoring wskazanych partii: ACME vs DLT"),
           "ukraine_home", "🇺🇦", "#6366f1", (GROUP_ADMIN, GROUP_TRANSPORT, GROUP_MASTER_DATA), "Transport"),
    Module("phv", _("MATinfo"), _("Podgląd materiału: szt → OPZ → karton → ładunek, przeliczniki, wagi, zgłoszenia"),
           "phv_home", "🧱", "#84cc16",
           (GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_WAREHOUSE, GROUP_CONTROLLER, GROUP_LEADER), "Magazyn"),
    Module("carton_opt", _("Optymalizacja kartonów"),
           _("Zarządzanie wypełnieniem ładunku: zgłoszenia ze skanera, pilność, warianty kartonów"),
           "carton_opt_inbox", "📦", "#f97316",
           (GROUP_ADMIN, GROUP_OPTIMIZER, GROUP_MASTER_DATA), "Operacje"),
]

# Kolejność sekcji w pasku nawigacji 1b (grupy + panele). Moduły bez `group` (np. Zadania)
# renderują się jako bezpośrednia pozycja z licznikiem.
NAV_GROUP_ORDER = ["Operacje", "Magazyn", "Transport", "Dane"]


MODULE_BY_KEY = {m.key: m for m in MODULES}

# Deployment variants — a single codebase/DB serving only a subset of modules when this
# instance is deployed under its own name/host (e.g. a warehouse-floor "Kontrola HU" app).
VARIANT_MODULES = {
    "hu": ("kontrola_hu", "wydruk_hu"),
}


def variant_modules():
    """Set of module keys this deployment is allowed to SERVE, or None = the full platform.

    Precedence: explicit GROOVE_MODULES (comma-separated keys) → named GROOVE_VARIANT preset
    → None (serve everything)."""
    from django.conf import settings
    explicit = (getattr(settings, "GROOVE_MODULES", "") or "").strip()
    if explicit:
        keys = {k.strip() for k in explicit.split(",") if k.strip() in MODULE_BY_KEY}
        return keys or None
    keys = VARIANT_MODULES.get((getattr(settings, "GROOVE_VARIANT", "") or "").lower())
    return set(keys) if keys else None


def module_external_urls():
    """{module_key: absolute URL} — where each module lives on another service/subdomain, so
    the portal can link across instances. Parsed from GROOVE_MODULE_URLS ("key=url,key=url")."""
    from django.conf import settings
    out = {}
    for part in (getattr(settings, "GROOVE_MODULE_URLS", "") or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and v:
                out[k] = v
    return out


def module_link(module, ext=None):
    """Portal URL for a module — its external service URL if configured, else the local path."""
    ext = module_external_urls() if ext is None else ext
    if module.key in ext:
        return ext[module.key]
    from django.urls import reverse
    return reverse(f"ui:{module.url_name}")


def portal_modules(user):
    """(module, url) pairs the user may access by ROLE/override — regardless of what THIS
    instance serves — each linking to where the module actually lives. Drives the hub as a
    portal across services. (Distinct from modules_for, which is what this instance serves.)"""
    ov = (_module_overrides(user)
          if getattr(user, "is_authenticated", False) and not user.is_superuser else {})
    ext = module_external_urls()
    mods = [m for m in MODULES if can_open_module(user, m.key, ov, ignore_variant=True)]
    keys = {m.key for m in mods}
    if "data_center" in keys:              # customers reached inside Data Center — don't duplicate
        mods = [m for m in mods if m.key != "klienci"]
    return [(m, module_link(m, ext)) for m in mods]


def _module_overrides(user):
    """{module_key: allowed} — this user's explicit per-module overrides (one query,
    pamiętane na obiekcie użytkownika — nawigacja pyta kilka razy na stronę)."""
    ov = getattr(user, "_palviz_module_ov", None)
    if ov is None:
        from ui.models import UserModuleAccess
        ov = user._palviz_module_ov = {
            o.module_key: o.allowed
            for o in UserModuleAccess.objects.filter(user=user).only("module_key", "allowed")}
    return ov


def can_open_module(user, key, overrides=None, ignore_variant=False):
    """Effective access to a hub module = superuser, else a per-user override if present,
    else the module's role gate. `overrides` lets callers pass a pre-fetched dict.
    `ignore_variant=True` skips the this-instance-serves check (used by the portal, which
    links to modules living on OTHER instances)."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if not ignore_variant:
        allowed = variant_modules()
        if allowed is not None and key not in allowed:
            return False                  # this deployment simply doesn't serve that module
    if user.is_superuser:
        return True
    ov = _module_overrides(user) if overrides is None else overrides
    if key in ov:
        return ov[key]
    m = MODULE_BY_KEY.get(key)
    if not m:
        return False
    # Grupy pamiętane na obiekcie użytkownika (jak _perm_cache Django): nawigacja pyta
    # o każdy moduł, has_role per moduł = ~30 zapytań na każdą stronę (PERF-002).
    names = getattr(user, "_palviz_groups", None)
    if names is None:
        names = user._palviz_groups = frozenset(user.groups.values_list("name", flat=True))
    return not m.roles or not names.isdisjoint(m.roles)


def module_required(key):
    """Decorator for a module's ENTRY view — gate on `can_open_module` (role default plus
    the per-user override). Backward-compatible: with no override it equals the module's
    role gate. Use on the 8 hub entry points."""
    from functools import wraps

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login
                return redirect_to_login(request.get_full_path(), "/login/")
            if can_open_module(request.user, key):
                return view_func(request, *args, **kwargs)
            from django.shortcuts import render
            return render(request, "ui/403.html", status=403)
        return wrapped
    return decorator


def nav_grouped(user):
    """Pasek nawigacji 1b: (groups, direct) z modułów dostępnych roli.
      • groups = [(label, [(module, url), ...]), ...] w kolejności NAV_GROUP_ORDER,
        pomijając puste sekcje,
      • direct = [(module, url), ...] dla modułów bez `group` (np. Zadania — z licznikiem).
    Reużywa modules_for (role/override/dedup klienci) i module_link (URL, także cross-instance)."""
    mods = [(m, module_link(m)) for m in modules_for(user)]
    direct = [(m, u) for m, u in mods if not m.group]
    by = {}
    for m, u in mods:
        if m.group:
            by.setdefault(m.group, []).append((m, u))
    ordered = [(g, by[g]) for g in NAV_GROUP_ORDER if by.get(g)]
    ordered += [(g, items) for g, items in by.items() if g not in NAV_GROUP_ORDER]
    return ordered, direct


def modules_for(user):
    """Modules the given user may open (superuser sees all; empty roles → any auth user)."""
    ov = _module_overrides(user) if getattr(user, "is_authenticated", False) and not user.is_superuser else {}
    mods = [m for m in MODULES if can_open_module(user, m.key, ov)]
    # "Baza klientów" is a Data Center sub-screen, so don't duplicate it as a standalone
    # hub tile for users who already reach it via Data Center (Admin / Master Data /
    # superuser). It stays on the hub only for roles without Data Center — Transport and
    # the narrow "Obsługa klienta" role — which is their sole entry point.
    keys = {m.key for m in mods}
    if "data_center" in keys:
        mods = [m for m in mods if m.key != "klienci"]
    return mods
