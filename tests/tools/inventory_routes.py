"""Inwentaryzacja tras GROOVE: każda nazwana trasa → ścieżka, widok, strażnik, metody HTTP.

Strażnika odczytuje z domknięć dekoratorów (bez uruchamiania widoków):
  • role_required(*grupy)         → guard "roles" + lista grup,
  • login_required / _planner     → guard "login",
  • require_http_methods / _POST  → dozwolone metody,
  • module_required(klucz)       → guard "module" + role modułu huba (MODULES; bez ról = każdy
                                    zalogowany) — plus indywidualne nadpisania UserModuleAccess,
  • csrf_exempt, django-ninja     → oznaczenia.
Widok bez żadnego z nich = guard "none" (publiczny albo sprawdza uprawnienia w środku — do
ręcznej weryfikacji). Uruchom z web/:  PYTHONPATH=.. python ../tests/tools/inventory_routes.py
"""
import json
import os
import sys

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "palletweb.settings")

import django  # noqa: E402

django.setup()

from django.urls import URLPattern, URLResolver, get_resolver  # noqa: E402

from core.platform_modules import MODULE_BY_KEY  # noqa: E402
from core.roles import ALL_GROUPS  # noqa: E402

GROUPS = set(ALL_GROUPS)


def _cells(fn):
    for c in getattr(fn, "__closure__", None) or ():
        try:
            yield c.cell_contents
        except ValueError:
            continue


def inspect_view(view):
    """Idzie łańcuchem dekoratorów (__wrapped__ + funkcje w domknięciach)."""
    info = {"guard": "none", "roles": [], "methods": None, "csrf_exempt": bool(getattr(view, "csrf_exempt", False))}
    seen, stack = set(), [view]
    while stack:
        fn = stack.pop()
        if id(fn) in seen or not callable(fn):
            continue
        seen.add(id(fn))
        if getattr(getattr(fn, "__code__", None), "co_qualname", "").startswith("module_required."):
            key = next((v for v in _cells(fn) if isinstance(v, str)), None)
            mod = MODULE_BY_KEY.get(key)
            if mod is not None:
                info.update(guard="module", module=key, roles=sorted(mod.roles or []))
        for val in _cells(fn):
            if isinstance(val, tuple) and val and all(isinstance(v, str) for v in val):
                if set(val) <= GROUPS and info["guard"] != "module":
                    info["roles"] = sorted(set(info["roles"]) | set(val))
                    info["guard"] = "roles"
            elif isinstance(val, (list, tuple)) and val and all(v in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"} for v in val):
                info["methods"] = sorted(set(val))
            elif callable(val):
                name = getattr(val, "__name__", "")
                if name == "<lambda>" and "is_authenticated" in getattr(getattr(val, "__code__", None), "co_names", ()):
                    if info["guard"] == "none":
                        info["guard"] = "login"
                stack.append(val)
        wrapped = getattr(fn, "__wrapped__", None)
        if wrapped is not None:
            stack.append(wrapped)
    return info


def walk(patterns, prefix="", ns=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            sub_ns = f"{ns}:{p.namespace}" if ns and p.namespace else (p.namespace or ns)
            yield from walk(p.url_patterns, prefix + str(p.pattern), sub_ns)
        elif isinstance(p, URLPattern):
            view = p.callback
            module = getattr(view, "__module__", "")
            entry = {"name": f"{ns}:{p.name}" if ns and p.name else (p.name or ""),
                     "path": prefix + str(p.pattern), "module": module,
                     "view": getattr(view, "__name__", type(view).__name__)}
            if "ninja" in module:
                entry.update(guard="api", roles=[], methods=None, csrf_exempt=True)
            else:
                entry.update(inspect_view(view))
            yield entry


def main():
    routes = list(walk(get_resolver().url_patterns))
    json.dump(routes, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
