# Przewoznicy: katalog, stawki, CRUD.
from ui.views.core import (
    Carrier, CarrierForm, CarrierRate, CarrierRateForm, CarrierZone,
    CarrierZoneForm, _pk4, _transport_mgr, get_object_or_404, messages,
    redirect, render,
)


@_transport_mgr
def planner_carriers(request):
    carriers = Carrier.objects.prefetch_related("zones__rates").all()
    return render(request, "ui/planner/carriers.html", {"carriers": carriers})

@_transport_mgr
def planner_carrier_form(request, pk=None):
    instance = get_object_or_404(Carrier, pk=pk) if pk else None
    form = CarrierForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        carrier = form.save()
        messages.success(request, f'Przewoznik "{carrier.name}" zapisany.')
        return redirect("ui:planner_carrier_rates", pk=carrier.pk)
    return render(request, "ui/planner/carrier_form.html", {"form": form, "instance": instance})

@_transport_mgr
def planner_carrier_delete(request, pk):
    carrier = get_object_or_404(Carrier, pk=pk)
    if request.method == "POST":
        name = carrier.name
        carrier.delete()
        messages.success(request, f'Przewoznik "{name}" usuniety.')
        return redirect("ui:planner_carriers")
    return render(request, "ui/planner/carrier_confirm_delete.html", {"carrier": carrier})

@_transport_mgr
def planner_carrier_rates(request, pk):
    """Manage zones + rates for a carrier (tabular inline)."""
    carrier = get_object_or_404(Carrier, pk=pk)
    zones = carrier.zones.prefetch_related("rates").all()

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "add_zone":
            zform = CarrierZoneForm(request.POST)
            if zform.is_valid():
                z = zform.save(commit=False)
                z.carrier = carrier
                z.save()
                messages.success(request, "Strefa dodana.")
            return redirect("ui:planner_carrier_rates", pk=pk)

        if action == "del_zone":
            qs = CarrierZone.objects.filter(pk=_pk4(request.POST.get("zone_id")), carrier=carrier)
            if qs.exists():
                qs.delete()
                messages.success(request, "Strefa usunięta.")
            else:
                messages.error(request, "Nie znaleziono strefy do usunięcia.")
            return redirect("ui:planner_carrier_rates", pk=pk)

        if action == "add_rate":
            zone_id = request.POST.get("zone_id")
            zone = get_object_or_404(CarrierZone, pk=_pk4(zone_id), carrier=carrier)
            rform = CarrierRateForm(request.POST)
            if rform.is_valid():
                r = rform.save(commit=False)
                r.zone = zone
                r.save()
                messages.success(request, "Stawka dodana.")
            return redirect("ui:planner_carrier_rates", pk=pk)

        if action == "del_rate":
            qs = CarrierRate.objects.filter(pk=_pk4(request.POST.get("rate_id")), zone__carrier=carrier)
            if qs.exists():
                qs.delete()
                messages.success(request, "Stawka usunięta.")
            else:
                messages.error(request, "Nie znaleziono stawki do usunięcia.")
            return redirect("ui:planner_carrier_rates", pk=pk)

    zone_form = CarrierZoneForm()
    rate_form = CarrierRateForm()
    return render(request, "ui/planner/carrier_rates.html", {
        "carrier": carrier,
        "zones": zones,
        "zone_form": zone_form,
        "rate_form": rate_form,
    })

__all__ = [
    "planner_carriers",
    "planner_carrier_form",
    "planner_carrier_delete",
    "planner_carrier_rates",
]
