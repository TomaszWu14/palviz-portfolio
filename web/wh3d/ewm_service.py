"""Warstwa ORM nad czystymi modułami adresowania: master EWM, plan modelu, zapis „Wykryj z EWM”."""
from django.db import transaction
from django.db.models import Prefetch, Q

from .addressing import expand_model
from .ewm_compliance import compliance
from .ewm_detect import detect
from .models import (
    BayTemplate, LocationOverride, WarehouseLocationMaster, WarehouseLocationMasterBatch, WarehouseModelRack,
)


def active_master():
    """Aktywny (najnowszy) import mastera lokalizacji albo None."""
    return WarehouseLocationMasterBatch.objects.filter(is_active=True).order_by("-uploaded_at").first()


def master_rows(batch, zones):
    """[(kod, typ EWM, wysokość mm, udźwig kg)] z mastera — tylko kody stref modelu."""
    if batch is None or not zones:
        return []
    prefix = Q()
    for zone in zones:
        prefix |= Q(location_code__startswith=f"{zone}-")
    return list(WarehouseLocationMaster.objects.filter(prefix, batch=batch)
                .values_list("location_code", "warehouse_type", "height_mm", "max_weight_kg"))


def detect_for_model(wm, batch):
    """Propozycja „Wykryj z EWM” dla rzędów modelu (nic nie zapisuje)."""
    racks = list(wm.racks.all())
    rows = [{"zone": r.zone, "rack_id": r.rack_id, "n_bays": r.n_bays} for r in racks]
    return detect(rows, master_rows(batch, {r.zone for r in racks}), list(BayTemplate.objects.all()))


@transaction.atomic
def apply_proposal(wm, proposal):
    """Zapis propozycji: nowe szablony, szablon domyślny + numeracja rzędów, wyjątki (ZASTĘPUJĄ
    dotychczasowe wyjątki wykrytych rzędów). → (nowe szablony, rzędy, wyjątki)."""
    by_key, created = {}, 0
    existing = BayTemplate.objects.in_bulk([t["pk"] for t in proposal["templates"] if t["pk"]])
    for t in proposal["templates"]:
        if t["pk"]:
            by_key[t["key"]] = existing[t["pk"]]
        else:
            by_key[t["key"]] = BayTemplate.objects.create(
                name=t["name"], beam_mm=t["beam_mm"], pallets_per_beam=t["pallets_per_beam"], levels=t["levels"])
            created += 1
    racks = {(r.zone, r.rack_id): r for r in wm.racks.select_for_update()}
    touched, new_overrides = [], []
    for row in proposal["rows"]:
        rack = racks[(row["zone"], row["rack_id"])]
        rack.template = by_key[row["template"]]
        rack.bay_numbers = row["bay_numbers"]
        touched.append(rack)
        for o in row["overrides"]:
            fields = {k: v for k, v in o.items() if k != "template"}
            new_overrides.append(LocationOverride(rack=rack, template=by_key.get(o.get("template")), **fields))
    if touched:
        WarehouseModelRack.objects.bulk_update(touched, ["template", "bay_numbers"])
    LocationOverride.objects.filter(rack__in=touched).delete()
    LocationOverride.objects.bulk_create(new_overrides)
    return created, len(touched), len(new_overrides)


def plan_for_model(wm):
    """Rozwinięty plan modelu → (rzędy, miejsca, duplikaty)."""
    racks = list(wm.racks.select_related("template").prefetch_related(
        Prefetch("overrides", queryset=LocationOverride.objects.select_related("template"))))
    locations, duplicates = expand_model([(r, r.template, list(r.overrides.all())) for r in racks])
    return racks, locations, duplicates


def compliance_for_model(wm, batch):
    """Raport zgodności planu modelu z aktywnym masterem (batch=None → brak kodów EWM)."""
    racks, locations, duplicates = plan_for_model(wm)
    rows = [{"zone": r.zone, "rack_id": r.rack_id, "has_template": r.template_id is not None} for r in racks]
    codes = [row[0] for row in master_rows(batch, {r.zone for r in racks})]
    return compliance(rows, locations, duplicates, codes)
