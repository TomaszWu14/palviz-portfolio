# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from .core import (
    login_required, redirect, has_role, GROUP_LEADER, render, Product, Q,
    PalletizationInstruction, _planner, WarehouseSnapshot, Count, _any_role
)
from django.db.models import F, Value
from ..product_lookup import resolve_ref


@login_required(login_url="/login/")
def module_home(request):
    """GROOVE hub — the first screen after login. Pick one of the platform modules
    ("tabs") you have access to. Modules are role-gated (admins see all); a user with a
    single accessible module skips the picker and goes straight in."""
    from ..platform_modules import portal_modules
    from ..roles import is_control_only
    # Zainstalowana apka skanera („GROOVE Go") = tylko skaner, ale rozpoznaje ją PRZEGLĄDARKA
    # (pvInstalledApp w _pwa_head: display-mode / sessionStorage — per okno), a hub odbija
    # ją do launchera skryptem w home.html. Dawniej decydowało ciasteczko pv_scanner, które
    # apka dzieli z kartami przeglądarki → desktop po zalogowaniu lądował w /control/.
    if is_control_only(request.user):
        # Desktop: Lider-tylko włada pulpitem (KPI/TV/reopen/typy) → hub kontroli; zwykły
        # kontroler → skaner. (Na zainstalowanej apce oba idą przez launcher wyżej.)
        # Spec UX §1: pulpit „Moja zmiana" jest ekranem domowym zwykłego kontrolera.
        return redirect("ui:hu_control_hub" if has_role(request.user, GROUP_LEADER)
                        else "ui:hu_my_shift")
    # Portal: tiles for every module the user may access, each linking to where the module
    # actually lives (external service/subdomain if configured, else this instance).
    modules = [{"key": m.key, "label": m.name, "desc": m.description, "color": m.color,
                "icon": m.icon, "url": url}
               for m, url in portal_modules(request.user)]
    if len(modules) == 1:
        return redirect(modules[0]["url"])
    if not modules:
        return redirect("ui:warehouse_search")
    # Odbicie do skanera tylko dla ról, które launcher wpuści (inaczej apka → 403).
    scanner_app = any(m["key"] in ("kontrola_hu", "phv") for m in modules)
    resp = render(request, "ui/home.html", {"modules": modules, "scanner_app": scanner_app})
    resp.delete_cookie("pv_scanner", samesite="Lax")   # sprzątanie starego ciasteczka (1 rok)
    return resp


@login_required(login_url="/login/")
def scanner_launcher(request):
    """GROOVE Go — wejście apki skanera. BEZ wyboru modułu (jeden widok skanera):
    kto ma Kontrolę HU → menu kontroli (z kafelkiem MATinfo na dole), reszta (picker /
    Magazyn) → wprost MATinfo (podgląd hierarchii). `?app=1` (start_url) przechodzi dalej,
    żeby strona docelowa oznaczyła okno jako apkę (sessionStorage, _pwa_head.html)."""
    from django.urls import reverse
    from ..platform_modules import can_open_module
    if can_open_module(request.user, "kontrola_hu"):
        target = reverse("ui:hu_control_menu")  # menu kontroli = jeden widok skanera
    elif can_open_module(request.user, "phv"):
        target = reverse("ui:phv_home")         # picker widzi tylko MATinfo
    else:
        # Q-43: /scan/ dostępny dla każdej roli — bez modułu skanera wyszukiwarka
        # magazynowa (tylko odczyt) zamiast 403.
        target = reverse("ui:warehouse_search")
    return redirect(target + ("?app=1" if request.GET.get("app") else ""))


@login_required(login_url="/login/")
def warehouse_search(request):
    from ..roles import is_control_only
    if is_control_only(request.user):           # keep control operators in their module
        # Spec UX §1: pulpit „Moja zmiana" jest ekranem domowym zwykłego kontrolera.
        return redirect("ui:hu_control_hub" if has_role(request.user, GROUP_LEADER)
                        else "ui:hu_my_shift")
    query = request.GET.get("q", "").strip()
    results = []

    if query:
        # exact match → direct redirect (pełna kaskada skanera: REF/EAN sztuki/kartonu/OPZ)
        exact = resolve_ref(query, active_only=True)
        if exact and exact.latest_instruction():
            return redirect("ui:warehouse_instruction", code=exact.code)
        # partial match → list results
        results = list(
            Product.objects.filter(
                Q(code__icontains=query) | Q(name__icontains=query), is_active=True
            ).order_by("code")[:20]
        )

    recent = PalletizationInstruction.objects.filter(is_active=True).select_related("product").order_by("-updated_at")[:6]
    return render(request, "ui/warehouse/search.html", {"query": query, "results": results, "recent": recent})

NEAR_FULL_PCT = 90        # zones at/above this are flagged as near-full


@_planner
def planner_occupancy(request):
    """Occupancy overview from latest snapshot — per zone, with overall fill KPIs and
    alerts (near-full ≥90% or zones holding blocked locations)."""
    latest = WarehouseSnapshot.objects.first()
    zones, alerts = [], []
    totals = {"total": 0, "occupied": 0, "empty": 0, "blocked": 0, "pct": 0}
    if latest:
        zone_data = (latest.rows.values("zone")
                     .annotate(total=Count("id"),
                               empty=Count("id", filter=Q(is_empty=True)),
                               occupied=Count("id", filter=Q(is_empty=False)),
                               blocked=Count("id", filter=Q(blocked_pick=True) | Q(blocked_put=True)))
                     .order_by("zone"))
        for z in zone_data:
            pct = round(z["occupied"] / z["total"] * 100) if z["total"] else 0
            zones.append({**z, "pct": pct})
            totals["total"] += z["total"]
            totals["occupied"] += z["occupied"]
            totals["empty"] += z["empty"]
            totals["blocked"] += z["blocked"]
            if pct >= NEAR_FULL_PCT:
                alerts.append({"zone": z["zone"], "kind": "near_full", "pct": pct})
            elif z["blocked"]:
                alerts.append({"zone": z["zone"], "kind": "blocked", "count": z["blocked"]})
        totals["pct"] = round(totals["occupied"] / totals["total"] * 100) if totals["total"] else 0

    if request.GET.get("export") == "csv":
        from ui.views.core.xlsx import safe_csv_writer  # SEC-007
        from django.http import HttpResponse
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="wypelnienie_magazynu.csv"'
        response.write("﻿")  # UTF-8 BOM for Excel
        w = safe_csv_writer(response)
        w.writerow(["Strefa", "Lokalizacji", "Zajęte", "Wolne", "Zablokowane", "Wypełnienie [%]"])
        for z in zones:
            w.writerow([z["zone"], z["total"], z["occupied"], z["empty"], z["blocked"], z["pct"]])
        w.writerow(["RAZEM", totals["total"], totals["occupied"], totals["empty"],
                    totals["blocked"], totals["pct"]])
        return response

    return render(request, "ui/planner/occupancy.html", {
        "snapshot": latest, "zones": zones, "totals": totals, "alerts": alerts,
        "near_full_pct": NEAR_FULL_PCT,
    })



@_any_role
def warehouse_bins(request):
    """BLOK D: „Miejsca składowania" — lista miejsc PUSTYCH i ZABLOKOWANYCH z ostatniego
    snapshotu SAP WMS. Filtry (typ magazynu / rozmiar / status) pamiętane w sesji,
    liczniki przy każdej opcji, sort po numerze regału rosnąco. Dane to snapshot
    z ręcznego importu — baner pokazuje datę wgrania.

    Rozmiar: „połówka" NIE jest polem — implikowana z sub-slotu kodu (C-1/C-2/D-1/D-2,
    półki dzielone ~40 cm); pełne = pozostałe."""
    latest = WarehouseSnapshot.objects.first()

    # Filtry: GET nadpisuje sesję; brak GET → ostatnio użyte (albo domyślne).
    saved = request.session.get("bins_filters", {})
    f_type = request.GET.get("t", saved.get("t", "all"))       # 0052|0010|other|all
    f_size = request.GET.get("s", saved.get("s", "all"))       # full|half|all
    f_status = request.GET.get("st", saved.get("st", "both"))  # empty|blocked|both
    request.session["bins_filters"] = {"t": f_type, "s": f_size, "st": f_status}

    rows, counts = [], {}
    if latest:
        blocked_q = Q(blocked_pick=True) | Q(blocked_put=True)
        base = latest.rows.filter(Q(is_empty=True) | blocked_q)   # tylko puste/zablokowane
        half_q = Q(col_code__contains="-")                        # sub-slot -1/-2 = połówka

        def _t(qs, t):
            if t == "0052":
                return qs.filter(warehouse_type="0052")
            if t == "0010":
                return qs.filter(warehouse_type__in=("0010", "0011"))
            if t == "other":
                return qs.exclude(warehouse_type__in=("0052", "0010", "0011"))
            return qs

        def _s(qs, sname):
            if sname == "half":
                return qs.filter(half_q)
            if sname == "full":
                return qs.exclude(half_q)
            return qs

        def _st(qs, st):
            if st == "empty":
                return qs.filter(is_empty=True).exclude(blocked_q)
            if st == "blocked":
                return qs.filter(blocked_q)
            return qs

        # Liczniki per opcja filtra — każdy liczony przy USTALONYCH pozostałych filtrach.
        for key in ("all", "0052", "0010", "other"):
            counts[f"t_{key}"] = _st(_s(_t(base, key), f_size), f_status).count()
        for key in ("all", "full", "half"):
            counts[f"s_{key}"] = _st(_s(_t(base, f_type), key), f_status).count()
        for key in ("both", "empty", "blocked"):
            counts[f"st_{key}"] = _st(_s(_t(base, f_type), f_size), key).count()

        qs = _st(_s(_t(base, f_type), f_size), f_status)
        # Sort: numer regału (stack) rosnąco NUMERYCZNIE (stack to CharField),
        # potem aleja i kod — Cast na int, nienumeryczne lądują na końcu.
        from django.db.models import IntegerField as _Int
        from django.db.models.functions import Cast, NullIf
        qs = (qs.annotate(stack_no=Cast(NullIf("stack", Value("")), output_field=_Int()))
              .order_by(F("stack_no").asc(nulls_last=True), "aisle", "location_code"))
        rows = list(qs[:500])

    return render(request, "ui/warehouse/bins.html", {
        "snapshot": latest, "rows": rows, "counts": counts,
        "f_type": f_type, "f_size": f_size, "f_status": f_status,
        "shown_cap": 500,
    })


__all__ = [
    "warehouse_bins",
    'module_home',
    'scanner_launcher',
    'warehouse_search',
    'planner_occupancy',
]
