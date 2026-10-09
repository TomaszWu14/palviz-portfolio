"""Moduł „Wysyłka UKRAINA".

Monitoring wskazanych przez klientów z Ukrainy partii (LOT) do wysyłki: dla każdej linii
zlecenia (indeks + partia + żądana ilość) pokazujemy, ile mamy GOTOWE na stanie, z podziałem
na nasz magazyn ACME i zewnętrzny DLT.

Stanu magazynowego nie przechowujemy — liczymy go w locie z istniejącego stocku
(`HandlingUnitItem` pod `HandlingUnit` z `is_stock=True`), kubełkując `warehouse_type` na
ACME/DLT wg kodów z ustawień (`UKRAINE_WAREHOUSE_ACME` / `UKRAINE_WAREHOUSE_DLT`).
"""
from .core import (
    settings, module_required, render, _md_or_tr, get_object_or_404, messages,
    redirect, require_POST, _read_table, _detect_column
)
from django.db.models import Sum
from ..models import UkraineOrderLine, HandlingUnitItem, Product


def _wh_buckets():
    """(zbior_kodow_ACME, zbior_kodow_DLT) z ustawień — porównywane wielkością liter bez znaczenia."""
    acme = {c.upper() for c in getattr(settings, "UKRAINE_WAREHOUSE_ACME", [])}
    dlt = {c.upper() for c in getattr(settings, "UKRAINE_WAREHOUSE_DLT", [])}
    return acme, dlt


def stock_by_batch(pairs):
    """Stan magazynowy dla zbioru (indeks, lot) — JEDNO zapytanie po istniejącym stocku.
    Zwraca {(index, lot): {"acme": q, "dlt": q, "other": q}} (sumy base_qty).

    Join po LOT jest normalizowany do 16 znaków: feed HU obcina partię do 16
    (HandlingUnitItem.lot max_length=16), a linia zlecenia trzyma do 32 — bez tej
    normalizacji dłuższy LOT nigdy nie trafiał w stan („wieczny brak").
    # ponytail: porównanie w jednostce bazowej (base_qty); dodać przelicznik dopiero gdy jednostka żądana rozjedzie się z base_unit."""
    pairs = {(c, (l or "")[:16]) for c, l in pairs}
    codes = {p[0] for p in pairs}
    lots = {p[1] for p in pairs}
    out = {}
    if not codes:
        return out
    acme, dlt = _wh_buckets()
    rows = (HandlingUnitItem.objects
            .filter(hu__shipment__is_stock=True, ref_code__in=codes, lot__in=lots)
            .values("ref_code", "lot", "hu__warehouse_type")
            .annotate(q=Sum("base_qty")))
    for r in rows:
        key = (r["ref_code"], (r["lot"] or "")[:16])
        if key not in pairs:
            continue                       # ten sam indeks, ale inna para (indeks,lot) — pomiń
        bucket = out.setdefault(key, {"acme": 0.0, "dlt": 0.0, "other": 0.0})
        wt = (r["hu__warehouse_type"] or "").upper()
        qty = float(r["q"] or 0)
        if wt in acme:
            bucket["acme"] += qty
        elif wt in dlt:
            bucket["dlt"] += qty
        else:
            bucket["other"] += qty
    return out


@module_required("wysylka_ukraina")
def ukraine_home(request):
    """Ekran monitoringu: linie zleceń × stan ACME/DLT/brak. Filtry: klient / zlecenie /
    status / tylko z brakiem."""
    lines = UkraineOrderLine.objects.select_related("product").all()
    q_customer = (request.GET.get("customer") or "").strip()
    q_order = (request.GET.get("order") or "").strip()
    q_status = (request.GET.get("status") or "").strip()
    only_short = request.GET.get("short") == "1"
    if q_customer:
        lines = lines.filter(customer__icontains=q_customer)
    if q_order:
        lines = lines.filter(order_ref__icontains=q_order)
    if q_status in ("open", "closed"):
        lines = lines.filter(status=q_status)
    lines = list(lines)

    pairs = {(ln.index_code, ln.lot) for ln in lines}
    stock = stock_by_batch(pairs)
    rows, n_short, has_other = [], 0, False
    for ln in lines:
        st = stock.get((ln.index_code, (ln.lot or "")[:16]),
                       {"acme": 0.0, "dlt": 0.0, "other": 0.0})
        ready = st["acme"] + st["dlt"] + st["other"]
        shortfall = max(0.0, float(ln.requested_qty or 0) - ready)
        if st["other"]:
            has_other = True
        row = {"line": ln, "acme": st["acme"], "dlt": st["dlt"], "other": st["other"],
               "ready": ready, "shortfall": shortfall}
        if shortfall > 0:
            n_short += 1
        if only_short and shortfall <= 0:
            continue
        rows.append(row)

    acme, dlt = _wh_buckets()
    return render(request, "ui/ukraine/home.html", {
        "rows": rows,
        "n_lines": len(lines),
        "n_short": n_short,
        "has_other": has_other,
        "warehouses_configured": bool(acme or dlt),
        "f_customer": q_customer, "f_order": q_order, "f_status": q_status, "f_short": only_short,
    })


def _line_from_post(request, obj):
    """Waliduj i zapisz linię z POST (bez ModelForm — konwencja repo). Zwraca (obj, errors)."""
    errors = []
    customer = (request.POST.get("customer") or "").strip()
    index_code = (request.POST.get("index_code") or "").strip()
    lot = (request.POST.get("lot") or "").strip()
    if not customer:
        errors.append("Podaj klienta.")
    if not index_code:
        errors.append("Podaj indeks.")
    if not lot:
        errors.append("Podaj partię (LOT).")
    try:
        requested = max(0.0, float((request.POST.get("requested_qty") or "0").replace(",", ".")))
    except ValueError:
        requested = 0.0
        errors.append("Żądana ilość musi być liczbą.")
    if errors:
        return obj, errors
    obj = obj or UkraineOrderLine()
    obj.customer = customer[:120]
    obj.order_ref = (request.POST.get("order_ref") or "").strip()[:60]
    obj.index_code = index_code[:50]
    obj.lot = lot[:32]
    obj.requested_qty = requested
    obj.unit = (request.POST.get("unit") or "szt").strip()[:20]
    obj.status = request.POST.get("status") if request.POST.get("status") in ("open", "closed") else "open"
    obj.note = (request.POST.get("note") or "").strip()[:300]
    obj.product = Product.objects.filter(code=obj.index_code).first()
    if obj.created_by_id is None:
        obj.created_by = request.user
    from django.db import IntegrityError
    try:
        obj.save()
    except IntegrityError:
        errors.append("Taka linia (klient + zlecenie + indeks + partia) już istnieje.")
    return obj, errors


@_md_or_tr
def ukraine_line_form(request, pk=None):
    obj = get_object_or_404(UkraineOrderLine, pk=pk) if pk else None
    errors = []
    if request.method == "POST":
        obj, errors = _line_from_post(request, obj)
        if not errors:
            messages.success(request, f"Linia „{obj.index_code}/{obj.lot}” zapisana.")
            return redirect("ui:ukraine_home")
    return render(request, "ui/ukraine/line_form.html", {"obj": obj, "errors": errors})


@_md_or_tr
@require_POST
def ukraine_line_delete(request, pk):
    obj = get_object_or_404(UkraineOrderLine, pk=pk)
    label = f"{obj.index_code}/{obj.lot}"
    obj.delete()
    messages.success(request, f"Linia „{label}” usunięta.")
    return redirect("ui:ukraine_home")


@_md_or_tr
@require_POST
def ukraine_import(request):
    """Import zleceń z CSV/xlsx — upsert po (klient, zlecenie, indeks, partia). Kolejny import
    tej samej linii aktualizuje żądaną ilość (ilości rosną w czasie)."""
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Nie wybrano pliku.")
        return redirect("ui:ukraine_home")
    header, data = _read_table(f)
    c_cust = _detect_column(header, ["klient", "customer", "odbiorca"])
    c_ord  = _detect_column(header, ["zlecenie", "order", "nr zam", "zamówienie"])
    c_idx  = _detect_column(header, ["indeks", "materiał", "material", "kod", "sku", "index"])
    c_lot  = _detect_column(header, ["partia", "lot", "seria", "charge", "batch"])
    c_qty  = _detect_column(header, ["ilość", "ilosc", "qty", "quantity", "żądan"])
    c_unit = _detect_column(header, ["jm", "jednostka", "unit"])
    if c_idx is None or c_lot is None or c_qty is None:
        messages.error(request, "Brak wymaganych kolumn: indeks, partia, ilość.")
        return redirect("ui:ukraine_home")

    def cell(row, idx):
        return (str(row[idx]).strip() if idx is not None and idx < len(row) and row[idx] is not None else "")

    added = updated = skipped = 0
    for row in data:
        index_code = cell(row, c_idx)
        lot = cell(row, c_lot)
        if not index_code or not lot:
            skipped += 1
            continue
        from huctl.hu_import import parse_float as _parse_float
        qty = _parse_float(cell(row, c_qty).replace("\xa0", ""))
        if qty is None:
            skipped += 1
            continue
        qty = max(0.0, qty)
        _, created = UkraineOrderLine.objects.update_or_create(
            customer=cell(row, c_cust)[:120] or "—",
            order_ref=cell(row, c_ord)[:60],
            index_code=index_code[:50],
            lot=lot[:32],
            defaults={
                "requested_qty": qty,
                "unit": (cell(row, c_unit) or "szt")[:20],
                "product": Product.objects.filter(code=index_code[:50]).first(),
            },
        )
        added += 1 if created else 0
        updated += 0 if created else 1
    messages.success(request, f"Import: dodano {added}, zaktualizowano {updated}"
                              + (f", pominięto {skipped}" if skipped else "") + ".")
    return redirect("ui:ukraine_home")


# stock_by_batch celowo poza __all__ — helper, nie widok (konwencja: __all__ tylko widoki).
__all__ = [
    "ukraine_home",
    "ukraine_line_form",
    "ukraine_line_delete",
    "ukraine_import",
]
