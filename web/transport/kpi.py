"""KPI transportu — obliczenia i snapshot (serwis, nie widok). Wołane z zadania
Celery w rdzeniu `ui` i leniwie z dashboardu. ARCH-001."""
from ui.views.core import Shipment
from transport.pallet_count import shipment_pallet_calc


def compute_transport_kpi():
    """Policz KPI transportu (kpi + charts) — O(N) po shipmentach z silnikiem pakowania.
    Wołane z zadania Celery (beat) i leniwie z widoku, gdy snapshot przeterminowany."""
    from ui import nbp
    shipments = list(Shipment.objects.filter(is_stock=False)
                     .prefetch_related("quote_offers", "driver", "lines__product"))
    status_counts = {}
    tot_pallets = 0.0
    tot_vol = 0.0
    tot_weight = 0.0
    costs = []                       # (cost_pln, pallets, weight_kg)
    by_country = {}
    monthly = {}
    n_with_driver = n_confirmed = 0
    for s in shipments:
        status_counts[s.get_status_display()] = status_counts.get(s.get_status_display(), 0) + 1
        if s.status == "cancelled":
            continue
        try:
            calc, sc = shipment_pallet_calc(s, with_packing=False)   # BIZ-007
            n_pal = sc["n_pallets"] if sc else 0
            vol, wt = calc["total_vol_m3"], calc["total_weight_kg"]
        except Exception:
            n_pal, vol, wt = 0, 0.0, 0.0
        tot_pallets += n_pal; tot_vol += vol; tot_weight += wt
        offer = next((o for o in s.quote_offers.all() if o.selected), None)
        cost_pln = None
        if offer and offer.amount is not None:
            cv = nbp.convert(offer.total_amount, offer.currency, "PLN")
            cost_pln = float(cv if cv is not None else offer.total_amount)
        if cost_pln is not None:
            costs.append((cost_pln, n_pal, wt))
            c = s.destination_country or "—"
            by_country[c] = by_country.get(c, 0.0) + cost_pln
        mk = s.created_at.strftime("%Y-%m")
        mm = monthly.setdefault(mk, {"n": 0, "cost": 0.0})
        mm["n"] += 1
        if cost_pln is not None:
            mm["cost"] += cost_pln
        da = getattr(s, "driver", None)
        if da and da.filled_at:
            n_with_driver += 1
            if da.pickup_status == "confirmed":
                n_confirmed += 1

    total_cost = sum(c for c, _, _ in costs)
    sum_pal = sum(p for _, p, _ in costs)
    sum_wt = sum(w for _, _, w in costs)
    kpi = {
        "n_active": sum(1 for s in shipments if s.status != "cancelled"),
        "n_pallets": int(round(tot_pallets)),
        "vol": round(tot_vol, 1),
        "weight_t": round(tot_weight / 1000, 1),
        "avg_cost": round(total_cost / len(costs)) if costs else 0,
        "cost_per_pallet": round(total_cost / sum_pal) if sum_pal else 0,
        "cost_per_kg": round(total_cost / sum_wt, 2) if sum_wt else 0,
        "ontime_pct": round(100 * n_confirmed / n_with_driver, 1) if n_with_driver else 0,
        "n_quoted": len(costs),
    }
    country_sorted = sorted(by_country.items(), key=lambda kv: kv[1], reverse=True)[:12]
    charts = {
        "status": {"labels": list(status_counts.keys()), "values": list(status_counts.values())},
        "country": {"labels": [c for c, _ in country_sorted],
                    "values": [round(v) for _, v in country_sorted]},
        "monthly": {"labels": sorted(monthly),
                    "counts": [monthly[k]["n"] for k in sorted(monthly)],
                    "costs": [round(monthly[k]["cost"]) for k in sorted(monthly)]},
    }
    return {"kpi": kpi, "charts": charts}


def refresh_transport_kpi_snapshot():
    """Przelicz KPI i zapisz snapshot (utwórz singleton przy pierwszym uruchomieniu)."""
    from ui.models import TransportKpiSnapshot
    data = compute_transport_kpi()
    snap = TransportKpiSnapshot.load() or TransportKpiSnapshot()
    snap.data = data
    snap.save()
    return snap

