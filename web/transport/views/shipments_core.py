# Przesylki: lista, formularz, karta, anulowanie/requote, usuwanie, zaladunek kontenera.
from ui.views.core import (
    Carrier, Count, DriverAssignment, GROUP_ADMIN, GROUP_CONTROLLER,
    GROUP_LEADER, Paginator, Product, Q, QuoteRecipient, Shipment, ShipmentForm,
    ShipmentLine, _as_int, _build_container_load,
    _build_vehicle_pallet_load, _calc_carrier_quotes, _calc_shipment_data,
    _import_shipment_lines_excel, _md_or_tr, _transport_mgr, get_object_or_404,
    has_role, messages, module_required, redirect, render, require_POST,
    safe_json, transaction,
)
from django.conf import settings
from django.db.models import Prefetch
from django.urls import reverse
from transport.pallet_count import pallet_height_cm, shipment_pallet_calc
from .mailing import _cancel_mailto, _client_loaded_mailto, _driver_form_mailto, _wh_mailto  # noqa: F401


@module_required("transport")
def planner_shipments(request):
    q = request.GET.get("q", "").strip()
    hu_filter = request.GET.get("hu", "").strip()      # "recheck" | "ready"
    # Prefetch the workflow relations so the status badge per row stays cheap.
    # Stock containers (HU import) belong to the Control module, not the transport list.
    shipments = (Shipment.objects.filter(is_stock=False).annotate(lines_count=Count("lines"))
                 .prefetch_related("quote_offers", "wh_confirmations", "handling_units", "driver",
                                   Prefetch("lines", queryset=ShipmentLine.objects
                                            .select_related("product").order_by("order", "id")))
                 .order_by("-created_at"))
    if q:
        shipments = shipments.filter(Q(name__icontains=q) | Q(recipient_name__icontains=q)
                                     | Q(destination_city__icontains=q))
    if hu_filter == "recheck":
        shipments = shipments.filter(handling_units__status="to_recheck").distinct()
    # Paginate BEFORE the per-row KPI pass below: each row runs _calc_shipment_data
    # (a few queries) + NBP conversions, so the loop cost scaled with the WHOLE
    # (unbounded) transport history. Bounding to one page bounds that to one screen.
    page_obj = Paginator(shipments, 25).get_page(request.GET.get("page", 1))
    shipments = list(page_obj)
    from palletizer.services.vehicle_load import VEHICLES
    from ui import nbp
    _veh_name = {v["key"]: v["name"] for v in VEHICLES}
    # Show quote value in BOTH currencies side by side; header carries the NBP EUR rate.
    eur_rate = nbp.get_rate("EUR")        # PLN per 1 EUR (None when NBP offline)
    # Instrukcje paletyzacji dla CAŁEJ strony jednym zapytaniem (nie per wiersz — PERF-002).
    from ui.views.core.helpers_shipment_calc import _latest_instructions
    instr_map = _latest_instructions({ln.product_id for sh in shipments for ln in sh.lines.all()})
    # Per-row KPIs: pallets/volume, chosen transport type(s), quote value/count/avg and the
    # warehouse-readiness flag — everything the planner needs without opening each row.
    for sh in shipments:
        try:
            calc, sc = shipment_pallet_calc(sh, with_packing=False,   # list KPI: fast estimate
                                            instr_map=instr_map)   # BIZ-007: wys. wysyłki
            sh.n_pallets = sc["n_pallets"] if sc else 0
            sh.volume_m3 = calc["total_vol_m3"]
            sh.total_weight_kg = calc["total_weight_kg"]
        except Exception:
            sh.n_pallets, sh.volume_m3, sh.total_weight_kg = 0, 0, 0

        # Transport type(s) chosen at import — what we calculate/quote against (no guessing).
        sh.vehicle_label = ", ".join(_veh_name.get(k, k) for k in sh.transport_mode_keys())

        # Quotes — count, plus value (chosen or cheapest) and average in BOTH PLN and EUR
        # (offers converted via NBP; falls back to raw amount when a rate is missing).
        offers = list(sh.quote_offers.all())
        priced = [o for o in offers if o.submitted_at and o.amount is not None]
        sh.quote_count = len(priced)
        chosen = next((o for o in offers if o.selected), None)
        sh.quote_is_selected = bool(chosen)

        def _stats(cur):
            vals = []
            for o in priced:
                v = nbp.convert(o.total_amount, o.currency, cur)
                vals.append(v if v is not None else o.total_amount)
            avg = (sum(vals) / len(vals)) if vals else None
            # Headline = the chosen offer's price, but only if it actually has one (an offer
            # can be marked selected before the forwarder fills in the amount); otherwise the
            # cheapest priced offer, so the row never blanks out while priced offers exist.
            if chosen is not None and chosen.amount is not None:
                cv = nbp.convert(chosen.total_amount, chosen.currency, cur)
                value = cv if cv is not None else chosen.total_amount
            else:
                value = min(vals) if vals else None
            return value, avg
        sh.quote_pln, sh.quote_avg_pln = _stats("PLN")
        sh.quote_eur, sh.quote_avg_eur = _stats("EUR")

        # Warehouse readiness: HU control fully OK, or the prep/date confirmation came back yes.
        wh = {w.kind: w for w in sh.wh_confirmations.all()}
        sh.wh_ready = (sh.hu_checked_ready()
                       or any(getattr(wh.get(k), "status", "") == "yes" for k in ("date", "pre")))

    return render(request, "ui/planner/shipments.html", {
        "page_obj": page_obj, "q": q, "hu_filter": hu_filter, "vehicles": VEHICLES,
        "eur_rate": eur_rate,
        "can_control": has_role(request.user, GROUP_ADMIN, GROUP_CONTROLLER, GROUP_LEADER)})

@_transport_mgr
def planner_shipment_form(request, pk=None):
    instance = get_object_or_404(Shipment, pk=pk) if pk else None
    form = ShipmentForm(request.POST or None, instance=instance)
    products = Product.objects.filter(is_active=True).order_by("code")

    if request.method == "POST" and form.is_valid():
        excel_file = request.FILES.get("excel_file")
        # Validate the upload BEFORE touching existing lines — an early return inside
        # the atomic block used to commit the line deletion and silently wipe the
        # shipment when the file was too big or failed to parse.
        if excel_file and excel_file.size > 10 * 1024 * 1024:
            messages.error(request, "Plik Excel zbyt duży (max 10 MB).")
        else:
            try:
                with transaction.atomic():
                    shipment = form.save()

                    # Clear existing lines; Excel import takes priority over manual rows
                    shipment.lines.all().delete()

                    if excel_file:
                        _import_shipment_lines_excel(shipment, excel_file)
                    else:
                        product_ids = request.POST.getlist("line_product")
                        quantities  = request.POST.getlist("line_quantity")
                        units       = request.POST.getlist("line_unit")
                        notes_list  = request.POST.getlist("line_notes")

                        for i, pid in enumerate(product_ids):
                            # Index into the parallel lists defensively — a malformed form
                            # with mismatched list lengths must not drop or misalign rows.
                            qty  = quantities[i] if i < len(quantities) else ""
                            unit = units[i]      if i < len(units)      else ""
                            if not pid or not qty:
                                continue
                            try:
                                product = Product.objects.get(pk=pid)
                                qty_f = float(qty.replace(",", "."))
                                if qty_f <= 0:
                                    continue
                                ShipmentLine.objects.create(
                                    shipment=shipment,
                                    product=product,
                                    quantity=qty_f,
                                    unit=unit or "kar",
                                    notes=notes_list[i] if i < len(notes_list) else "",
                                    order=i,
                                )
                            except (Product.DoesNotExist, ValueError):
                                continue

                messages.success(request, f'Przesylka "{shipment.name}" zapisana.')
                return redirect("ui:planner_shipment_detail", pk=shipment.pk)
            except ValueError as exc:
                # Import failed → atomic rolled back, existing lines preserved.
                messages.error(request, str(exc))

    existing_lines = instance.lines.select_related("product").all() if instance else []
    return render(request, "ui/planner/shipment_form.html", {
        "form": form,
        "instance": instance,
        "products": products,
        "existing_lines": existing_lines,
    })


@_transport_mgr
def planner_shipment_detail(request, pk):
    shipment = get_object_or_404(Shipment, pk=pk)
    # Definition bar (no gate): set the transport type to visualise against + the load mode
    # (na paletach / bez palet). This drives whether we show the pallet or container view.
    if request.method == "POST" and request.POST.get("define"):
        from palletizer.services.vehicle_load import VEHICLES as _V
        lm = request.POST.get("load_mode")
        if lm in ("pallets", "loose"):
            shipment.load_mode = lm
        veh = request.POST.get("vehicle")
        if veh in {v["key"] for v in _V}:
            keys = [veh] + [k for k in shipment.transport_mode_keys() if k != veh]
            shipment.transport_modes = ",".join(keys)
        shipment.save(update_fields=["load_mode", "transport_modes"])
        messages.success(request, "Zapisano definicję wysyłki.")
        return redirect("ui:planner_shipment_detail", pk=pk)
    # Inline comment for a pallet-count discrepancy (no need to open the full edit form).
    if request.method == "POST" and request.POST.get("save_anchor_note") is not None:
        shipment.anchor_note = (request.POST.get("anchor_note") or "")[:300]
        shipment.save(update_fields=["anchor_note"])
        messages.success(request, "Zapisano komentarz.")
        return redirect("ui:planner_shipment_detail", pk=pk)
    # `?eff=` lets the user try a different stowage efficiency live without re-saving;
    # otherwise the shipment's stored value drives the pallet count and quotes.
    eff = _as_int(request.GET.get("eff"), shipment.stowage_efficiency_pct)

    # Two editable load-height scenarios (default 1.8 and 2.2 m), entered in metres.
    def _h_cm(val, default_cm):
        try:
            m = float(str(val).replace(",", "."))
        except (TypeError, ValueError):
            return default_cm
        return max(100, min(280, int(round(m * 100)))) if m else default_cm
    h1 = _h_cm(request.GET.get("h1"), 180)
    h2 = _h_cm(request.GET.get("h2"), 220)
    # A linked customer's max pallet height is a hard requirement — never propose a
    # taller pallet than the consignee allows, regardless of the entered scenarios.
    cap = shipment.customer.max_pallet_height_cm if shipment.customer_id else None
    if cap:
        h1, h2 = min(h1, cap), min(h2, cap)
    calc = _calc_shipment_data(shipment, stow_eff=eff, heights=[h1, h2])

    # The persisted pick (if it still matches one of the current scenarios) is the one
    # confirmed for the quote + warehouse build; the other scenario is then frozen.
    heights = [s["max_h_cm"] for s in calc["scenarios"]]
    chosen_h = shipment.selected_pallet_height_cm if shipment.selected_pallet_height_cm in heights else None
    # Selected height drives the 3D layout + carrier quote; defaults to the confirmed pick.
    sel_h = _as_int(request.GET.get("h"), chosen_h or heights[0])
    if sel_h not in heights:
        sel_h = heights[0]
    sel_scenario = next((s for s in calc["scenarios"] if s["max_h_cm"] == sel_h), calc["scenarios"][0])

    # Split view: build the 3D layout for EVERY scenario so both can be shown side
    # by side at once (no switching). Each gets its own pallet packing.
    three_views = []
    for sc in calc["scenarios"]:
        td = sc.get("three")        # packed once in _calc_shipment_data; count/LDM/fill match
        if td:
            three_views.append({"scenario": sc, "data_json": safe_json(td)})
    three_data = next((v["data_json"] for v in three_views
                       if v["scenario"]["max_h_cm"] == sel_h), None)

    # Carrier quotes use the pallet count for the selected height.
    carriers = Carrier.objects.filter(is_active=True).prefetch_related("zones__rates")
    quotes = _calc_carrier_quotes(calc, carriers, shipment.destination_country,
                                  n_pallets=sel_scenario["n_pallets"])

    # Whole-vehicle load plan for the selected scenario (#8): how many trucks/containers
    # this many pallets needs, with LDM, payload/space utilisation and weight balance.
    from palletizer.services.vehicle_load import plan_all as _plan_all
    vehicle_plans = _plan_all(
        n_pallets=int(sel_scenario["n_pallets"]),
        pallet_height_cm=int(sel_h),
        total_weight_kg=float(calc["total_weight_kg"]),
        double_stack=bool(request.GET.get("stack") == "2"),
    )

    from palletizer.services.vehicle_load import VEHICLES as _VEH
    # selected_offer() rescans quote_offers on each call — resolve it (and the client ETA)
    # once and reuse, instead of calling it four times while building the context.
    _sel_offer = shipment.selected_offer()
    _client_eta = shipment.client_eta or (_sel_offer.delivery_date if _sel_offer else None)
    return render(request, "ui/planner/shipment_detail.html", {
        "shipment": shipment,
        "calc": calc,
        "three_data": three_data,
        "three_views": three_views,
        "vehicles": _VEH,
        # Routing: container (loose cartons) view when loading loose or a container was
        # chosen; otherwise the pallet view. Drives which visualisation the page shows.
        "viz_kind": "container" if shipment.wants_container_viz() else "pallets",
        "primary_vehicle_key": shipment.primary_vehicle_key() or "naczepa",
        "vehicle_plans": vehicle_plans,
        "double_stack": request.GET.get("stack") == "2",
        "selected_height": sel_h,
        "chosen_height": chosen_h,
        "chosen_label": next((s["label"] for s in calc["scenarios"]
                              if s["max_h_cm"] == chosen_h), ""),
        "h1_m": f"{h1/100:.2f}", "h2_m": f"{h2/100:.2f}",
        "selected_scenario": sel_scenario,
        "customer": shipment.customer,
        "height_capped": bool(cap),
        "weight_capped": calc.get("weight_capped", False),
        "quotes": quotes,
        "has_recipients": QuoteRecipient.objects.filter(is_active=True, is_warehouse=False).exists(),
        "handling_units": shipment.handling_units.prefetch_related("items").all(),
        "quote_offers": (_qoffers := list(shipment.quote_offers.filter(submitted_at__isnull=False)
                                 .select_related("recipient").order_by("amount"))),
        # Only call out the cheapest when every offer is in the same currency —
        # comparing PLN vs EUR by raw amount would crown the wrong winner.
        "quote_single_currency": len({o.currency for o in _qoffers if o.amount is not None}) <= 1,
        "wh_confirmations": [
            {"obj": wr, "mailto": _wh_mailto(
                shipment, wr, request.build_absolute_uri(
                    reverse("ui:wh_readiness_response", args=[wr.token])))}
            for wr in shipment.wh_confirmations.all()
        ],
        # Client "goods loaded" notification: default ETA = the chosen forwarder's delivery
        # date (overridable), plus an Outlook/mailto fallback when server SMTP is off.
        "client_eta_default": _client_eta,
        "client_mailto": _client_loaded_mailto(shipment, _client_eta),
        "client_email": (shipment.customer.contact_email if shipment.customer_id else ""),
        "driver": getattr(shipment, "driver", None),
        "driver_form_mailto": _driver_form_mailto(request, shipment),
        "origin": getattr(settings, "SHIPMENT_ORIGIN_ADDRESS", ""),
        "cancel_mailto": _cancel_mailto(shipment),
        "google_maps_key": getattr(settings, "GOOGLE_MAPS_API_KEY", ""),
    })


@_transport_mgr
@require_POST
def planner_shipment_cancel(request, pk):
    shipment = get_object_or_404(Shipment, pk=pk)
    shipment.mark_cancelled()
    offer = shipment.selected_offer()
    if offer and offer.recipient:
        messages.success(request, "Przesyłka anulowana. Powiadom spedycję przyciskiem powiadomienia.")
    else:
        messages.success(request, "Przesyłka anulowana.")
    return redirect("ui:planner_shipment_detail", pk=pk)


@_transport_mgr
@require_POST
def planner_shipment_requote(request, pk):
    """Re-open quoting when something changed: unlock forwarder forms and clear the
    chosen carrier + driver, then go back to the quote screen."""
    shipment = get_object_or_404(Shipment, pk=pk)
    shipment.quote_offers.update(selected=False, submitted_at=None)
    DriverAssignment.objects.filter(shipment=shipment).delete()
    shipment.mark_draft()      # anulowana / zatwierdzona (wybór oferty wyczyszczony) → robocza
    messages.success(request, "Otwarto ponowną wycenę — formularze spedycji odblokowane.")
    return redirect("ui:planner_shipment_quote", pk=pk)


@_transport_mgr
def planner_shipment_delete(request, pk):
    shipment = get_object_or_404(Shipment, pk=pk)
    if request.method == "POST":
        if shipment.has_controlled_hu():
            messages.error(request, "Nie można usunąć przesyłki — jej HU są już w kontroli "
                                    "(usunięcie skasowałoby historię kontroli). Anuluj ją zamiast usuwać.")
            return redirect("ui:planner_shipment_detail", pk=pk)
        name = shipment.name
        shipment.delete()
        messages.success(request, f'Przesylka "{name}" usunieta.')
        return redirect("ui:planner_shipments")
    return render(request, "ui/planner/shipment_confirm_delete.html", {"shipment": shipment})


@_md_or_tr
def planner_shipment_container(request, pk):
    """Loose-load view: pack the goods directly into a chosen container/trailer (no pallets)
    and visualise it. Switch the vehicle with ?vehicle=<key>."""
    from palletizer.services.vehicle_load import VEHICLES
    shipment = get_object_or_404(Shipment, pk=pk)
    calc = _calc_shipment_data(shipment, with_packing=False)
    key = request.GET.get("vehicle", "naczepa")
    vehicle = next((v for v in VEHICLES if v["key"] == key), VEHICLES[1])
    mode = "pallets" if request.GET.get("mode") == "pallets" else "loose"
    if mode == "pallets":
        max_h = pallet_height_cm(shipment)
        data = _build_vehicle_pallet_load(calc, vehicle, max_h)
    else:
        data = _build_container_load(calc, vehicle)
    return render(request, "ui/planner/container_load.html", {
        "shipment": shipment, "calc": calc, "vehicle": vehicle, "vehicles": VEHICLES,
        "data": data, "data_json": safe_json(data) if data else "null",
        "selected_key": vehicle["key"], "mode": mode,
    })

__all__ = [
    "planner_shipments",
    "planner_shipment_form",
    "planner_shipment_detail",
    "planner_shipment_cancel",
    "planner_shipment_requote",
    "planner_shipment_delete",
    "planner_shipment_container",
]
