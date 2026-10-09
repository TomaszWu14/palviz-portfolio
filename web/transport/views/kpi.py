# KPI transportu i plan odbiorow.
from ui.views.core import (
    Shipment, _transport_mgr, render, safe_json,
)
from django.utils import timezone
from transport.kpi import compute_transport_kpi, refresh_transport_kpi_snapshot  # noqa: F401

# Snapshot KPI starszy niż tyle sekund jest przeliczany przy wejściu na dashboard
# (niezależnie od beat-owego odświeżania co godzinę).
TRANSPORT_KPI_TTL_SEC = 3600


@_transport_mgr
def planner_transport_kpi(request):
    """Transport KPI dashboard — renderuje ze snapshotu (cache w DB) zamiast liczyć
    packing wszystkich shipmentów per request. Snapshot odświeża beat (co godzinę)
    albo leniwie to wejście, gdy jest starszy niż TRANSPORT_KPI_TTL_SEC."""
    from django.utils import timezone as _tz
    from ui.models import TransportKpiSnapshot
    snap = TransportKpiSnapshot.load()
    if (snap is None or not snap.data
            or (_tz.now() - snap.updated_at).total_seconds() > TRANSPORT_KPI_TTL_SEC):
        snap = refresh_transport_kpi_snapshot()
    return render(request, "ui/planner/transport_kpi.html",
                  {"kpi": snap.data["kpi"], "charts_json": safe_json(snap.data["charts"]),
                   "kpi_updated_at": snap.updated_at})


@_transport_mgr
def planner_pickup_schedule(request):
    """Mini harmonogram odbiorów: every shipment with a confirmed warehouse-ready time
    and/or a forwarder pickup date, ordered by time — so everyone sees what leaves when."""
    shipments = (Shipment.objects.filter(is_stock=False)
                 .exclude(status="cancelled")
                 .prefetch_related("quote_offers", "wh_confirmations"))
    rows = []
    for sh in shipments:
        offer = next((o for o in sh.quote_offers.all() if o.selected), None)
        wh = {w.kind: w for w in sh.wh_confirmations.all()}
        ready = wh.get("date") or wh.get("pre")
        ready_at = getattr(ready, "ready_at", None)
        pickup_date = (offer.truck_date if offer else None) or getattr(ready, "pickup_date", None)
        # Only schedule rows that actually have a time anchor.
        if not (ready_at or pickup_date):
            continue
        rows.append({
            "shipment": sh, "offer": offer,
            "pickup_date": pickup_date, "ready_at": ready_at,
            "delivery_date": offer.delivery_date if offer else None,
            "wh_ready": (sh.hu_checked_ready()
                         or any(getattr(wh.get(k), "status", "") == "yes" for k in ("date", "pre"))),
        })
    # Sort by the soonest anchor (pickup date, else ready time). None last.
    import datetime as _dt
    def _anchor_date(r):
        # ready_at is an aware datetime — convert to the warehouse-local date so it matches
        # timezone.localdate() below and lands under the right day heading (not the UTC day).
        if r["pickup_date"]:
            return r["pickup_date"]
        return timezone.localtime(r["ready_at"]).date() if r["ready_at"] else None
    rows.sort(key=lambda r: (_anchor_date(r) is None, _anchor_date(r) or _dt.date.max))
    # Default to upcoming only (from today forward); ?all=1 reveals past odbiory too.
    show_all = request.GET.get("all") == "1"
    today = timezone.localdate()
    if not show_all:
        rows = [r for r in rows if (_anchor_date(r) is None or _anchor_date(r) >= today)]
    # Group by day (rows are already date-sorted) so the calendar reads day-by-day.
    _DOW = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
    groups = []
    for r in rows:
        d = _anchor_date(r)
        if not groups or groups[-1]["date"] != d:
            label = f"{_DOW[d.weekday()].capitalize()}, {d.strftime('%d.%m.%Y')}" if d else "Bez terminu"
            is_today = bool(d and d == today)
            groups.append({"date": d, "label": label, "is_today": is_today, "rows": []})
        groups[-1]["rows"].append(r)
    return render(request, "ui/planner/pickup_schedule.html",
                  {"groups": groups, "show_all": show_all})

__all__ = [
    "TRANSPORT_KPI_TTL_SEC",
    "compute_transport_kpi",
    "refresh_transport_kpi_snapshot",
    "planner_transport_kpi",
    "planner_pickup_schedule",
]
