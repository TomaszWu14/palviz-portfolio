# Gotowosc magazynowa: zapytanie/odpowiedz WH, wizualizacja palet, przejecie liczby palet.
from ui.views.core import (
    Shipment, WarehouseReadiness,
    _transport_mgr, get_object_or_404, messages, redirect, render, require_POST,
)
from core.ratelimit import post_rate_limit
from django.urls import reverse
from django.utils import timezone
from transport.pallet_count import shipment_pallet_calc
from .mailing import _send_wh_email, _smtp_unconfigured, _wh_recipients  # noqa: F401


@_transport_mgr
@require_POST
def planner_shipment_wh_request(request, pk, kind):
    """Create/refresh a warehouse readiness request (kind 'pre' or 'date')."""
    shipment = get_object_or_404(Shipment, pk=pk)
    if kind not in ("pre", "date"):
        return redirect("ui:planner_shipment_detail", pk=pk)
    wr, _ = WarehouseReadiness.objects.get_or_create(shipment=shipment, kind=kind)
    wr.status = "pending"
    wr.requested_at = timezone.now()
    wr.answered_at = None
    # Record the pallet count we're asking the warehouse to prepare, so the response page
    # shows the same figure as the quote and the warehouse can confirm or counter it.
    try:
        _, _sc = shipment_pallet_calc(shipment, with_packing=False)   # BIZ-007
        wr.asked_pallets = _sc["n_pallets"] if _sc else None
    except Exception:
        wr.asked_pallets = None
    wr.confirmed_pallets = None
    wr.suggested_pallets = None
    if kind == "pre":
        wr.deadline = wr.requested_at + __import__("datetime").timedelta(minutes=15)
    else:
        from datetime import datetime as _dt
        try:
            wr.pickup_date = _dt.strptime((request.POST.get("pickup_date") or "").strip(), "%Y-%m-%d").date()
        except ValueError:
            pass
        wr.deadline = None
    wr.save()
    # Without server SMTP, auto-open the warehouse mail in Outlook on the detail page
    # (?wh_mail=<id>) so the request actually goes out on click — same as the forwarder flow.
    if _smtp_unconfigured():
        # Don't auto-open a recipient-less mailto — surface the missing-address error instead.
        if not _wh_recipients():
            messages.error(request, "Utworzono zapytanie, ale brak adresu magazynu — oznacz adres jako "
                                    "„magazyn” w bazie adresowej lub ustaw WAREHOUSE_EMAIL.")
            return redirect("ui:planner_shipment_detail", pk=pk)
        messages.success(request, "Utworzono zapytanie o gotowość — otwieram e-mail do magazynu w Outlooku.")
        return redirect(reverse("ui:planner_shipment_detail", args=[pk]) + f"?wh_mail={wr.pk}")
    ok, info = _send_wh_email(request, shipment, wr)
    messages.success(request, f"Wysłano zapytanie o gotowość do magazynu ({info})." if ok
                     else f"Utworzono zapytanie — wyślij przyciskiem „Do magazynu”. ({info})")
    return redirect("ui:planner_shipment_detail", pk=pk)


@_transport_mgr
@require_POST
def planner_shipment_wh_send(request, pk, wr_id):
    """Resend an existing readiness request as the HTML e-mail."""
    shipment = get_object_or_404(Shipment, pk=pk)
    wr = get_object_or_404(WarehouseReadiness, pk=wr_id, shipment=shipment)
    ok, info = _send_wh_email(request, shipment, wr)
    if ok:
        messages.success(request, f"Wysłano ponownie do magazynu ({info}).")
    else:
        messages.error(request, f"Nie wysłano e-maila — {info}")
    return redirect("ui:planner_shipment_detail", pk=pk)


def _wh_pallet_viz(shipment):
    """Lightweight pallet visualisation for the warehouse response page: one tile per
    pallet with its build height and a stacked SKU colour bar — same load the forwarder
    sees, so the warehouse confirms exactly what was quoted. Returns (pallets, height_cm)."""
    try:
        _, sc = shipment_pallet_calc(shipment, stow_eff=shipment.stowage_efficiency_pct)   # BIZ-007
    except Exception:
        return [], 0
    if not sc or not sc.get("three"):
        return [], (sc["max_h_cm"] if sc else 0)
    out = []
    for p in sc["three"]["pallets"]:
        boxes = p.get("boxes") or []
        if not boxes:
            out.append({"empty": True, "height_cm": 0, "n": 0, "segments": []})
            continue
        agg = {}                       # dict keeps insertion order (Py 3.7+)
        for b in boxes:
            k = (b.get("label", ""), b.get("color", "#94a3b8"))
            agg[k] = agg.get(k, 0) + 1
        n = len(boxes)
        segs = [{"label": lbl, "color": col, "count": c, "pct": round(100 * c / n, 1)}
                for (lbl, col), c in agg.items()]
        out.append({"empty": False, "height_cm": p.get("height_cm", 0), "n": n, "segments": segs})
    return out, sc["max_h_cm"]


@post_rate_limit("wh_readiness_response", 20, 3600, by="token")   # SEC-014
def wh_readiness_response(request, token):
    """Public token page: the warehouse sees the quoted pallet load and either confirms it,
    proposes a different pallet count, or declines — always giving the fastest ready time."""
    wr = get_object_or_404(WarehouseReadiness.objects.select_related("shipment"), token=token)
    shipment = wr.shipment
    if request.method == "POST":
        # Idempotencja (jak quote_response): po pierwszej odpowiedzi token jest wielorazowy,
        # więc odświeżenie/podwójny klik ponawiał notify_warehouse_response (in-app+mail bez
        # dedupu) → spam planistom. Zamykamy po answered_at.
        if wr.answered_at:
            return render(request, "ui/wh_response.html",
                          {"wr": wr, "shipment": shipment, "saved": True})
        ans = request.POST.get("answer")
        wr.note = (request.POST.get("note") or "")[:300]

        def _parse_dt(raw):
            raw = (raw or "").strip()
            if not raw:
                return None
            from datetime import datetime as _dt
            for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    return timezone.make_aware(_dt.strptime(raw, fmt))
                except (ValueError, TypeError):
                    continue
            return None

        if ans == "no":
            wr.status = "no"
            wr.answered_at = timezone.now()
            wr.confirmed_pallets = wr.suggested_pallets = None
        elif ans in ("yes", "suggest"):
            wr.status = "yes"
            wr.answered_at = timezone.now()
            wr.ready_at = _parse_dt(request.POST.get("ready_at"))
            sug = 0
            if ans == "suggest":
                try:
                    sug = int(request.POST.get("suggested_pallets") or 0)
                except (ValueError, TypeError):
                    sug = 0
            # Only a count that DIFFERS from what we asked is a real counter-proposal; a
            # "suggest" that repeats the asked figure is just a confirmation (otherwise the
            # alert reads "N zamiast N" and the apply button — gated on a difference — can
            # never close the task).
            if sug and sug != wr.asked_pallets:
                wr.suggested_pallets = sug
                wr.confirmed_pallets = sug
            else:
                wr.suggested_pallets = None
                wr.confirmed_pallets = wr.asked_pallets
        if ans in ("yes", "no", "suggest"):
            wr.save()
        # Alert the planners (team task + in-app/e-mail) only when action is needed:
        # a real counter-proposal or a decline — never a plain confirmation.
        from ui.notifications import notify_warehouse_response
        if wr.suggested_pallets:
            notify_warehouse_response(shipment, wr, "suggest")
        elif ans == "no":
            notify_warehouse_response(shipment, wr, "no")
        elif wr.status == "yes" and wr.kind == "date":
            # Loop closed: warehouse ready for the pickup date → tell the creator so they
            # can decide whether to notify the client of loading + the approx delivery date.
            notify_warehouse_response(shipment, wr, "confirmed_date")
        return render(request, "ui/wh_response.html", {"wr": wr, "shipment": shipment, "saved": True})
    pallets, viz_h = _wh_pallet_viz(shipment)
    return render(request, "ui/wh_response.html", {
        "wr": wr, "shipment": shipment, "viz_pallets": pallets, "viz_height_cm": viz_h})


@_transport_mgr
@require_POST
def planner_shipment_apply_wh_count(request, pk):
    """Adopt the pallet count the warehouse confirmed/proposed: pin it on the shipment
    (so it drives the quote) and reopen the forwarder quoting on that figure."""
    shipment = get_object_or_404(Shipment, pk=pk)
    # A counter-proposal/confirmation can land on EITHER the 'pre' or 'date' readiness —
    # pick whichever actually carries a count, not just 'pre'.
    wrs = list(shipment.wh_confirmations.all())
    wr = (next((w for w in wrs if w.suggested_pallets), None)
          or next((w for w in wrs if w.confirmed_pallets), None)
          or next((w for w in wrs if w.asked_pallets), None)
          or (wrs[0] if wrs else None))
    count = 0
    if wr:
        count = wr.suggested_pallets or wr.confirmed_pallets or wr.asked_pallets or 0
    # Explicit override from the form takes precedence (planner may type a final figure).
    try:
        count = int(request.POST.get("count") or count)
    except (ValueError, TypeError):
        pass
    if count <= 0:
        messages.error(request, "Brak liczby palet od magazynu do zatwierdzenia.")
        return redirect("ui:planner_shipment_detail", pk=pk)
    shipment.warehouse_pallets = count
    shipment.save(update_fields=["warehouse_pallets"])
    # The planner acted on the warehouse response → close its alert task.
    from ui.notifications import close_warehouse_response_task
    close_warehouse_response_task(shipment)
    # Reopen quoting so the forwarder is asked for the warehouse-agreed pallet count.
    shipment.quote_offers.update(selected=False, submitted_at=None)
    if shipment.status == "confirmed":   # wybór oferty wyczyszczony → znów robocza (Q-41);
        shipment.mark_draft()            # anulowanej/wysłanej nie ruszamy
    messages.success(request, f"Ustalono {count} palet z magazynem — przeliczono i otwarto wycenę spedycji.")
    return redirect("ui:planner_shipment_quote", pk=pk)

__all__ = [
    "planner_shipment_wh_request",
    "planner_shipment_wh_send",
    "_wh_pallet_viz",
    "wh_readiness_response",
    "planner_shipment_apply_wh_count",
]
