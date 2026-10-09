# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from .core import (_admin_only, render, get_object_or_404, settings, messages, redirect, _md_or_tr)
from ..roles import ALL_GROUPS
from ..platform_modules import MODULES


from .admin_users import _access_audit, _role_groups  # noqa: F401

@_admin_only
def admin_positions(request):
    """Panel stanowisk (BLOK G): lista + macierz stanowisko × moduł hub-a.

    Macierz liczona z Position.groups × MODULES.roles — dodanie stanowiska będącego
    kombinacją ISTNIEJĄCYCH grup nie wymaga zmian w kodzie widoków (grupy = frozen
    contract; widoki dalej sprawdzają grupy, stanowisko tylko je nadaje)."""
    from ..models import Position
    positions = Position.objects.prefetch_related("groups", "users__user")
    rows = []
    for p in positions:
        gnames = {g.name for g in p.groups.all()}
        cols = [(bool(set(m.roles) & gnames) if m.roles else True) for m in MODULES]
        rows.append({"p": p, "groups": sorted(gnames), "cols": cols,
                     "user_count": p.users.count()})
    return render(request, "ui/admin/positions.html", {
        "rows": rows, "modules": list(MODULES)})


@_admin_only
def admin_position_form(request, pk=None):
    """Dodaj/edytuj stanowisko: nazwa, opis, zestaw grup (ról), aktywność.
    Zapis przelicza grupy WSZYSTKICH kont na tym stanowisku (jedno źródło prawdy)."""
    from django.contrib.auth.models import Group
    from ..models import Position
    obj = get_object_or_404(Position, pk=pk) if pk else None
    errors = []
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()[:80]
        if not name:
            errors.append("Nazwa stanowiska jest wymagana.")
        elif Position.objects.filter(name=name).exclude(pk=obj.pk if obj else None).exists():
            errors.append(f'Stanowisko „{name}” już istnieje.')
        if not errors:
            from django.db import transaction as _tx
            with _tx.atomic():
                if not obj:
                    obj = Position(name=name)
                obj.name = name
                obj.description = (request.POST.get("description") or "").strip()[:200]
                obj.is_active = request.POST.get("is_active") == "1"
                try:
                    obj.order = max(0, int(request.POST.get("order") or 100))
                except ValueError:
                    obj.order = 100
                obj.save()
                group_qs = Group.objects.filter(pk__in=request.POST.getlist("groups"),
                                                name__in=ALL_GROUPS)
                obj.groups.set(group_qs)
                # Przelicz konta na tym stanowisku (poza SSO — tam grupy nadaje IdP).
                if not getattr(settings, "OIDC_ENABLED", False):
                    for prof in obj.users.select_related("user"):
                        obj.apply_to(prof.user)
            gnames = ", ".join(g.name for g in obj.groups.all()) or "—"
            _access_audit(request, "position_change", None,
                          f"stanowisko {obj.name}: grupy {gnames}")
            messages.success(request, f'Stanowisko „{obj.name}” zapisane.')
            return redirect("ui:admin_positions")
    return render(request, "ui/admin/position_form.html", {
        "obj": obj, "errors": errors, "groups": _role_groups(),
        "position_group_ids": list(obj.groups.values_list("pk", flat=True)) if obj else []})




@_md_or_tr
def admin_import_status(request):
    """BLOK F: panel statusu importów — ostatni przebieg per źródło (ImportRun) +
    istniejące batche (lokalizacje/snapshot/layout/heatmapa czytane wprost — bez
    dublowania śladu). Wiersz czerwony, gdy import starszy niż próg (default 24 h)."""
    from django.utils import timezone as _tz
    from ..models import (ImportRun, PickerActivityBatch,
                          WarehouseLayout, WarehouseLocationMasterBatch,
                          WarehouseSnapshot)
    stale_hours = int(getattr(settings, "IMPORT_STALE_HOURS", 24))
    now = _tz.now()

    rows = []

    def _add(kind_label, when, status="ok", count=0, error="", who=""):
        age_h = (now - when).total_seconds() / 3600 if when else None
        rows.append({"label": kind_label, "when": when, "status": status,
                     "count": count, "error": error, "who": who,
                     "stale": (age_h is None or age_h > stale_hours)})

    # Źródła z własnym batchem — stan wprost z modeli batchy.
    b = WarehouseLocationMasterBatch.objects.filter(is_active=True).first()
    _add("Lokalizacje — master", b.uploaded_at if b else None,
         count=b.location_count if b else 0)
    snap = WarehouseSnapshot.objects.first()
    _add("Snapshot zajętości (SAP WMS)", snap.uploaded_at if snap else None,
         count=snap.row_count if snap else 0)
    lay = WarehouseLayout.objects.filter(is_active=True).first()
    _add("Layout magazynu", lay.uploaded_at if lay else None,
         count=lay.location_count if lay else 0)
    pab = PickerActivityBatch.objects.order_by("-uploaded_at").first()
    _add("Aktywność pickerów (heatmapa)", pab.uploaded_at if pab else None,
         count=pab.row_count if pab else 0)

    # Pozostałe źródła — ostatni ImportRun per kind.
    covered_by_batch = {"locations_master", "warehouse_snapshot", "warehouse_layout",
                        "picker_activity"}
    latest = {}
    for run in ImportRun.objects.select_related("user")[:1000]:
        latest.setdefault(run.kind, run)
    for kind, label in ImportRun.KINDS:
        if kind in covered_by_batch:
            continue
        run = latest.get(kind)
        _add(label, run.started_at if run else None,
             status=run.status if run else "ok",
             count=run.row_count if run else 0,
             error=run.error_message if run else "",
             who=(run.user.get_username() if (run and run.user) else ""))

    rows.sort(key=lambda r: (r["when"] is not None, r["when"] or now), reverse=False)
    return render(request, "ui/admin/import_status.html", {
        "rows": rows, "stale_hours": stale_hours})

__all__ = [
    'admin_positions',
    'admin_position_form',
    'admin_import_status',
]
