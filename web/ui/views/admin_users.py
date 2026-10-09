# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from .core import (
    _admin_only, render, get_object_or_404, settings, messages, redirect, require_POST,
    _read_table, _detect_column, HandlingUnit, WarehouseRackType, GROUP_CONTROLLER,
    GROUP_LEADER, ControllerZone, has_role, GROUP_ADMIN
)
from ..roles import ALL_GROUPS, ROLE_LABELS, MODULE_ROLES
from ..platform_modules import MODULES, NAV_GROUP_ORDER, can_open_module
from ..models import (UserModuleAccess, ZariaAdminAudit,
                      AccessAudit)



def _zaria_audit(request, action, detail=""):
    """Zapisz zdarzenie administracyjne ZARIA do audytu (bez treści rozmów)."""
    ZariaAdminAudit.objects.create(actor=request.user, action=action[:60], detail=str(detail)[:400])


def _access_audit(request, action, target_user=None, detail=""):
    """Zapisz zmianę roli / dostępu do audytu (P3): kto, kogo dotyczy, co się zmieniło."""
    AccessAudit.objects.create(actor=request.user, action=action[:60],
                               target_user=target_user, detail=str(detail)[:400])


def _online_user_ids():
    """(online_user_ids:set, total_sessions:int) for users with a live Django session.
    Cached 30 s — decoding every session row on each admin page load is the cost ceiling.
    # ponytail: locmem/redis cache, fine at this scale; drop TTL if 'online' must be exact."""
    from django.core.cache import cache
    cached = cache.get("admin_online_sessions")
    if cached is not None:
        return cached
    from django.contrib.sessions.models import Session
    from django.utils import timezone
    online, total = set(), 0
    for s in Session.objects.filter(expire_date__gte=timezone.now()):
        total += 1
        try:
            uid = s.get_decoded().get("_auth_user_id")
            if uid:
                online.add(int(uid))
        except (ValueError, TypeError, KeyError):
            continue   # corrupt/foreign session payload — skip it
    result = (online, total)
    cache.set("admin_online_sessions", result, 30)
    return result


def _role_groups():
    """All eight PalViz role groups in precedence order (Admin first). Ensures each exists
    so the admin form always lists the full role set, even on a not-yet-seeded database."""
    from django.contrib.auth.models import Group
    return [Group.objects.get_or_create(name=name)[0] for name in ALL_GROUPS]


@_admin_only
def admin_panel(request):
    """Landing for the advanced admin panel — users, the login × module matrix, the
    role → module matrix and the HU control-zone matrix, with a few live counters."""
    from django.contrib.auth.models import User

    online_ids, _ = _online_user_ids()
    return render(request, "ui/admin/panel.html", {
        "n_users": User.objects.count(),
        "n_active": User.objects.filter(is_active=True).count(),
        "n_online": len(online_ids),
        "n_roles": len(ALL_GROUPS),
        "n_modules": len(MODULES),
        "n_overrides": UserModuleAccess.objects.count(),
    })


@_admin_only
def admin_users(request):
    from django.contrib.auth.models import User
    from django.core.paginator import Paginator
    from django.db.models import Q

    online_user_ids, total_session_count = _online_user_ids()

    q = request.GET.get("q", "").strip()
    users = User.objects.prefetch_related("groups").order_by("username")
    if q:
        users = users.filter(Q(username__icontains=q) | Q(first_name__icontains=q)
                             | Q(last_name__icontains=q) | Q(email__icontains=q))
    page = Paginator(users, 50).get_page(request.GET.get("page"))
    return render(request, "ui/admin/users.html", {
        "users": page,                      # page object — iterable like a queryset
        "page_obj": page,
        "q": q,
        "total_users": User.objects.count(),
        "groups": _role_groups(),           # all eight roles, not just four
        "online_user_ids": online_user_ids,
        "active_session_count": len(online_user_ids),
        "total_session_count": total_session_count,
    })


from .admin_users_form import admin_user_form  # noqa: F401  (wydzielone: limit 500 linii)


@_admin_only
@require_POST
def admin_users_import(request):
    """Import/aktualizacja użytkowników z CSV/XLSX — upsert po loginie.

    Kolumny: login* · imię · nazwisko · email · hasło (tylko nowi; puste = konto bez
    hasła do ustawienia przez admina/SSO) · role (nazwy grup po przecinku/średniku,
    spoza katalogu ról — pomijane) · telefon · dział · aktywny (tak/nie).
    Przy SSO role pochodzą z IdP — kolumna ról jest wtedy ignorowana (jak w formularzu)."""
    from django.contrib.auth.models import User

    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Nie wybrano pliku.")
        return redirect("ui:admin_users")
    header, rows = _read_table(f)
    c_login = _detect_column(header, ["login", "użytkownik", "uzytkownik", "username"])
    if c_login is None:
        messages.error(request, "Brak kolumny „login”. Nagłówki: " + ", ".join(header))
        return redirect("ui:admin_users")
    c_first = _detect_column(header, ["imię", "imie", "first"])
    c_last  = _detect_column(header, ["nazwisko", "last"])
    c_email = _detect_column(header, ["email", "e-mail", "mail"])
    c_pass  = _detect_column(header, ["hasło", "haslo", "password"])
    c_roles = _detect_column(header, ["role", "grupy", "groups", "rola"])
    c_phone = _detect_column(header, ["telefon", "phone"])
    c_dept  = _detect_column(header, ["dział", "dzial", "department"])
    c_activ = _detect_column(header, ["aktywny", "active"])

    sso_managed_roles = bool(getattr(settings, "OIDC_ENABLED", False))
    groups_by_name = {g.name: g for g in _role_groups()}
    truthy = {"tak", "yes", "true", "1", "x", "aktywny"}

    def cell(row, idx):
        return (str(row[idx]).strip() if idx is not None and idx < len(row)
                and row[idx] is not None else "")

    from ..models import UserProfile
    added = updated = skipped = 0
    for row in rows:
        login = cell(row, c_login)
        if not login:
            skipped += 1
            continue
        obj = User.objects.filter(username=login).first()
        created = obj is None
        if created:
            obj = User(username=login[:150])
            pwd = cell(row, c_pass)
            if pwd:
                obj.set_password(pwd)
            else:
                obj.set_unusable_password()      # konto bez hasła — ustawi admin / SSO
        obj.first_name = cell(row, c_first)[:150] or obj.first_name
        obj.last_name = cell(row, c_last)[:150] or obj.last_name
        obj.email = cell(row, c_email)[:254] or obj.email
        activ = cell(row, c_activ).lower()
        if activ:
            obj.is_active = activ in truthy
        obj.save()
        if not sso_managed_roles and c_roles is not None:
            names = [n.strip() for n in cell(row, c_roles).replace(";", ",").split(",") if n.strip()]
            valid = [groups_by_name[n] for n in names if n in groups_by_name]
            if names:                             # kolumna wypełniona → ustaw dokładnie te role
                obj.groups.set(valid)
        prof, _ = UserProfile.objects.get_or_create(user=obj)
        phone, dept = cell(row, c_phone), cell(row, c_dept)
        if phone:
            prof.phone = phone[:20]
        if dept:
            prof.department = dept[:80]
        prof.save(update_fields=["phone", "department"])
        added += 1 if created else 0
        updated += 0 if created else 1
    _access_audit(request, "users_import", None,
                  f"import użytkowników: +{added} / zaktualizowano {updated}"
                  + (f" / pominięto {skipped}" if skipped else ""))
    messages.success(request, f"Import użytkowników: dodano {added}, zaktualizowano {updated}"
                              + (f", pominięto {skipped}" if skipped else "") + ".")
    return redirect("ui:admin_users")


@_admin_only
def admin_user_delete(request, pk):
    from django.contrib.auth.models import User
    obj = get_object_or_404(User, pk=pk)
    if obj == request.user:
        messages.error(request, "Nie możesz usunąć własnego konta.")
        return redirect("ui:admin_users")
    if request.method == "POST":
        username = obj.username
        obj.delete()
        messages.success(request, f"Użytkownik '{username}' usunięty.")
        return redirect("ui:admin_users")
    return render(request, "ui/admin/user_confirm_delete.html", {"obj": obj})


@_admin_only
@require_POST
def admin_user_toggle_active(request, pk):
    from django.contrib.auth.models import User
    obj = get_object_or_404(User, pk=pk)
    if obj == request.user:
        messages.error(request, "Nie możesz dezaktywować własnego konta.")
    else:
        obj.is_active = not obj.is_active
        obj.save(update_fields=["is_active"])
        state = "aktywowany" if obj.is_active else "dezaktywowany"
        messages.success(request, f"Użytkownik '{obj.username}' {state}.")
    return redirect("ui:admin_users")


def _matrix_token():
    """Odcisk całej macierzy nadpisań (login×moduł) — optimistic-lock. Zmiana
    dowolnej komórki (dodanie/usunięcie/przełączenie) zmienia token, więc zapis
    z nieświeżej karty zostaje wykryty i wstrzymany zamiast nadpisać cudze zmiany."""
    import hashlib
    rows = list(UserModuleAccess.objects.order_by("user_id", "module_key")
                .values_list("user_id", "module_key", "allowed"))
    return hashlib.md5(repr(rows).encode()).hexdigest()[:16]


@_admin_only
def admin_module_access(request):
    """The advanced login × module matrix. Each cell is a 3-state override on top of the
    role gate: inherit (role default) / allow (force-grant) / deny (force-block). Saving
    rebuilds the whole override table from the submitted grid."""
    from django.contrib.auth.models import User

    users = User.objects.prefetch_related("groups").order_by("username")

    if request.method == "POST":
        from django.db import transaction
        with transaction.atomic():
            # Optimistic lock: token opisuje stan macierzy, który admin miał wczytany.
            # Jeśli ktoś zapisał w międzyczasie, token się nie zgadza → wstrzymaj, nie
            # nadpisuj cudzych zmian (delete+bulk_create inaczej cicho gubi update).
            if request.POST.get("matrix_token", "") != _matrix_token():
                messages.error(request, "Ktoś inny zmienił macierz dostępu w międzyczasie. "
                                        "Twój zapis wstrzymano, żeby nie nadpisać cudzych zmian — "
                                        "odśwież stronę i nanieś zmiany ponownie.")
                return redirect("ui:admin_module_access")
            bulk = []
            for u in users:
                for m in MODULES:   # iteracja po katalogu MODULES ⇒ tylko poprawne klucze
                    v = request.POST.get(f"m_{u.pk}_{m.key}", "inherit")
                    if v == "allow":
                        bulk.append(UserModuleAccess(user=u, module_key=m.key, allowed=True))
                    elif v == "deny":
                        bulk.append(UserModuleAccess(user=u, module_key=m.key, allowed=False))
            UserModuleAccess.objects.all().delete()
            UserModuleAccess.objects.bulk_create(bulk)
        _access_audit(request, "module_access", None, f"macierz login×moduł — {len(bulk)} nadpisań")
        messages.success(request, f"Zapisano macierz dostępu — {len(bulk)} nadpisań.")
        return redirect("ui:admin_module_access")

    # Kolumny grupowane wg Module.group (rejestr MODULES nie jest posortowany po grupach —
    # sortujemy tu, żeby dwuwierszowy nagłówek i komórki wierszy były w tej samej kolejności).
    def _gk(m):
        return (NAV_GROUP_ORDER.index(m.group) if m.group in NAV_GROUP_ORDER
                else len(NAV_GROUP_ORDER), m.name)
    ordered = sorted(MODULES, key=_gk)
    col_groups = []
    for m in ordered:
        label = m.group or "Pozostałe"
        if col_groups and col_groups[-1]["name"] == label:
            col_groups[-1]["span"] += 1
        else:
            col_groups.append({"name": label, "span": 1})

    matrix_token = _matrix_token()
    overrides = {(o.user_id, o.module_key): o.allowed for o in UserModuleAccess.objects.all()}
    rows = []
    for u in users:
        cells = []
        for m in ordered:
            ov = overrides.get((u.pk, m.key))            # True / False / None
            state = "allow" if ov is True else ("deny" if ov is False else "inherit")
            role_ok = can_open_module(u, m.key, {})       # what "inherit" resolves to (role gate)
            cells.append({"module": m, "state": state, "role_ok": role_ok,
                          "effective": can_open_module(u, m.key)})
        rows.append({"user": u, "cells": cells})
    return render(request, "ui/admin/module_access.html",
                  {"modules": ordered, "col_groups": col_groups, "rows": rows,
                   "matrix_token": matrix_token})


@_admin_only
def admin_role_matrix(request):
    """Read-only reference matrices: hub modules × roles, and fine-grained write
    capabilities × roles — across ALL eight role groups."""
    from django.contrib.auth.models import Group
    role_names = list(ALL_GROUPS)
    from django.db.models import Count
    counts = {g.name: g.n for g in
              Group.objects.filter(name__in=role_names).annotate(n=Count("user"))}
    roles = [{"name": g, "color": ROLE_LABELS[g][1], "count": counts.get(g, 0)} for g in role_names]

    module_rows = [{
        "label": m.name,
        "icon": m.icon,
        "cols": [(r in m.roles) if m.roles else True for r in role_names],
    } for m in MODULES]

    cap_labels = {
        "products_write": "Produkty — edycja", "cartons_write": "Kartony — edycja",
        "inner_packs_write": "Opakowania zbiorcze — edycja", "categories_write": "Kategorie — edycja",
        "instructions_write": "Instrukcje paletyzacji — edycja", "reports_manage": "Zgłoszenia — zarządzanie",
        "carriers_write": "Przewoźnicy — edycja", "shipments_write": "Wysyłki — tworzenie",
        "shipments_read": "Wysyłki — podgląd", "stock_write": "Ruchy towarowe — dodawanie",
        "occupancy_read": "Wypełnienie magazynu — podgląd", "warehouse_model": "Model magazynu 3D/2D",
        "excel_templates": "Szablony Excel / import", "calculator": "Kalkulator paletyzacji",
        "user_management": "Zarządzanie użytkownikami", "hu_print": "Wydruk HU (etykiety)",
    }
    cap_rows = [{
        "label": label,
        "cols": [r in MODULE_ROLES.get(key, []) for r in role_names],
    } for key, label in cap_labels.items()]

    def _grants(rr):
        return sum(sum(1 for x in r["cols"] if x) for r in rr)
    return render(request, "ui/admin/role_matrix.html", {
        "roles": roles,
        "role_labels": ROLE_LABELS,
        "module_rows": module_rows,
        "cap_rows": cap_rows,
        "module_grants": _grants(module_rows),
        "cap_grants": _grants(cap_rows),
    })


@_admin_only
def admin_control_zones(request):
    """Editable matrix: which warehouse types (zones) each HU-controller may control.

    Rows = users in the Kontrola HU / Lider kontroli groups; columns = the warehouse
    types present on HUs merged with the rack-type catalog. A user with NO ticked box
    controls EVERY zone (backward-compatible); ticking a strict subset limits them.
    Admins & leaders always bypass the limit, so their rows are informational."""
    from django.contrib.auth.models import User

    codes = set(t for t in HandlingUnit.objects.values_list("warehouse_type", flat=True) if t)
    # Z katalogu tylko typy REGAŁOWE — strefy techniczne (BROK, LABO, wysyłkowe…) nie są
    # miejscem kontroli HU; jeśli realnie wystąpią na HU, i tak wejdą z pierwszego seta.
    codes |= set(WarehouseRackType.objects.filter(kind="rack").values_list("code", flat=True))
    zone_codes = sorted(codes)

    controllers = (User.objects.filter(
        groups__name__in=[GROUP_CONTROLLER, GROUP_LEADER]).distinct().order_by("username"))

    if request.method == "POST":
        _uid = request.POST.get("user_id") or ""
        target = User.objects.filter(pk=_uid).first() if _uid.isdigit() else None
        if target:
            selected = set(request.POST.getlist("zones"))
            valid = selected & set(zone_codes)
            ControllerZone.objects.filter(user=target).delete()
            if valid and valid != set(zone_codes):
                ControllerZone.objects.bulk_create(
                    [ControllerZone(user=target, code=c) for c in valid])
                messages.success(request, f"Zapisano — {target.username}: {len(valid)} z "
                                          f"{len(zone_codes)} stref.")
            else:
                messages.success(request, f"Zapisano — {target.username}: wszystkie strefy.")
        return redirect("ui:admin_control_zones")

    rows = []
    zcount = {c: 0 for c in zone_codes}   # ilu kontrolerów jest OGRANICZONYCH do danej strefy
    for u in controllers:
        bypass = u.is_superuser or has_role(u, GROUP_ADMIN, GROUP_LEADER)
        allowed = None if bypass else ControllerZone.zones_for(u)
        if allowed is not None:
            for c in allowed:
                if c in zcount:
                    zcount[c] += 1
        rows.append({
            "user": u,
            "bypass": bypass,
            "all_zones": allowed is None,
            "cols": [{"code": c, "checked": (allowed is None) or (c in allowed)}
                     for c in zone_codes],
        })
    zone_cards = [{"code": c, "count": zcount[c]} for c in zone_codes]
    return render(request, "ui/admin/control_zones.html", {
        "zone_codes": zone_codes,
        "zone_cards": zone_cards,
        "rows": rows,
    })

__all__ = [
    '_zaria_audit', '_access_audit', '_online_user_ids', '_role_groups',
    'admin_panel', 'admin_users', 'admin_user_form', 'admin_users_import',
    'admin_user_delete', 'admin_user_toggle_active', 'admin_module_access',
    'admin_role_matrix', 'admin_control_zones',
]
