# Klienci transportu: lista, import, formularz, reguly pakowania.
from ui.views.core.xlsx import MAX_IMPORT_ROWS
from ui.views.core import (
    Count, Customer, GROUP_ADMIN, GROUP_MASTER_DATA, Q, _after_import_redirect, _customer_mgr,
    _md_role, _read_table, get_object_or_404, has_role, messages, module_required,
    redirect, render, require_POST, transaction,
)


@module_required("klienci")
def planner_customers(request):
    """Customer / consignee database with per-customer delivery requirements."""
    q = request.GET.get("q", "").strip()
    customers = Customer.objects.annotate(n_shipments=Count("shipments"))
    if q:
        customers = customers.filter(Q(name__icontains=q) | Q(code__icontains=q)
                                     | Q(city__icontains=q))
    return render(request, "ui/planner/customers.html",
                  {"customers": customers, "q": q})


def _map_customer_columns(header):
    """Map a SAP KNA1 (or similar) export's Polish headers to Customer fields.

    Matches the FIRST column whose lowercased header contains a keyword, so the
    duplicate 'Nazwa 1'/'Nazwa 2' columns in a KNA1 dump don't clobber the address."""
    specs = [
        ("code",    ("klient", "nr klienta", "numer klienta", "kunnr", "odbiorca")),
        ("country", ("kraj", "land")),
        # Accept either a single combined "Nazwa klienta" column or the split
        # "Nazwa 1"/"Nazwa 2" pair — the leftmost "nazwa…" column is the name.
        ("name1",   ("nazwa klienta", "nazwa 1", "nazwa1", "name 1", "name1", "nazwa", "name")),
        ("name2",   ("nazwa 2", "nazwa2", "name 2", "name2")),
        ("city",    ("miasto", "miejscowość", "ort", "city")),
        ("postal",  ("kod pocztowy", "kod poczt", "plz", "postal")),
        ("street",  ("ulica", "street", "stras")),
        ("phone",   ("telefon 1", "telefon", "phone", "tel")),
        # Rozszerzenie (import odbiorców do kontroli): kategoria klienta, przewoźnik,
        # standardowa wysokość wysyłki — kolumny opcjonalne, stary plik działa bez zmian.
        ("category", ("kategoria", "category", "typ klienta")),
        ("carrier",  ("przewoźnik", "przewoznik", "carrier", "spedytor")),
        ("height",   ("wysokość", "wysokosc", "height", "wys. palety")),
    ]
    idx = {}
    for field, keys in specs:
        for i, h in enumerate(header):
            if field in idx:
                break
            if any(k in h for k in keys):
                idx[field] = i
    return idx


@_customer_mgr
@require_POST
def planner_customer_import(request):
    """Import / update customers from a SAP KNA1 (or similar) CSV/XLSX export.

    Upserts by customer number (`code`): existing customers are matched and only their
    identity/address fields refreshed — per-customer requirements (pallet height,
    fumigation, …) set in PalViz are never overwritten."""
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Nie wybrano pliku.")
        return redirect("ui:planner_customers")
    if f.size > 10 * 1024 * 1024:
        messages.error(request, "Plik zbyt duży (max 10 MB).")
        return redirect("ui:planner_customers")

    header, rows = _read_table(f)
    idx = _map_customer_columns(header)
    if "code" not in idx or "name1" not in idx:
        messages.error(request, "Brak rozpoznanych kolumn (Klient + Nazwa 1). Nagłówki: "
                       + ", ".join(header))
        return redirect("ui:planner_customers")
    if len(rows) > MAX_IMPORT_ROWS:
        messages.error(request, f"Plik zbyt duży (max {MAX_IMPORT_ROWS:,} wierszy).".replace(",", " "))
        return redirect("ui:planner_customers")

    def cell(row, field):
        i = idx.get(field)
        return str(row[i]).strip() if i is not None and i < len(row) and row[i] is not None else ""

    # Collect one record per code (last row wins) before touching the DB.
    parsed = {}
    for row in rows:
        code = cell(row, "code")
        name = " ".join(p for p in (cell(row, "name1"), cell(row, "name2")) if p).strip()
        if not code or not name:
            continue
        data = {
            "name": name[:200],
            "country": cell(row, "country")[:2].upper(),
            "city": cell(row, "city")[:100],
            "postal": cell(row, "postal")[:20],
            "street": cell(row, "street")[:200],
            "phone": cell(row, "phone")[:40],
        }
        # Opcjonalne kolumny (puste w pliku → nie nadpisujemy istniejących wartości):
        cat_raw = cell(row, "category").lower().replace("-", "_").replace(" ", "_")
        cat = {"vip": "vip", "delta": "delta", "beta": "beta",
               "export": "export", "standard": ""}.get(cat_raw)
        if cat is not None and cat_raw:
            data["category"] = cat
            data["is_vip"] = (cat == "vip")
        carrier = cell(row, "carrier")
        if carrier:
            data["carrier"] = carrier[:40]
        try:
            h = int(float(cell(row, "height").replace(",", ".")))
            if 50 <= h <= 280:
                data["max_pallet_height_cm"] = h
        except ValueError:
            pass
        parsed[code[:40]] = data

    fields = ["name", "country", "city", "postal", "street", "phone",
              "category", "is_vip", "carrier", "max_pallet_height_cm"]
    existing = {c.code: c for c in Customer.objects.filter(code__in=parsed.keys())}
    to_create, to_update = [], []
    for code, data in parsed.items():
        cust = existing.get(code)
        if cust is None:
            to_create.append(Customer(code=code, kind="customer", **data))
        else:
            for k, v in data.items():
                setattr(cust, k, v)
            to_update.append(cust)

    with transaction.atomic():
        Customer.objects.bulk_create(to_create, batch_size=1000)
        if to_update:
            Customer.objects.bulk_update(to_update, fields, batch_size=1000)

    messages.success(request, f"Import klientów: dodano {len(to_create)}, "
                     f"zaktualizowano {len(to_update)}.")
    from ui.models import ImportRun
    ImportRun.record("customers", rows=len(to_create) + len(to_update), user=request.user)
    return _after_import_redirect(request, "ui:planner_customers")


@_customer_mgr
def planner_customer_form(request, pk=None):
    from ui.forms import CustomerForm
    instance = get_object_or_404(Customer, pk=pk) if pk else None
    form = CustomerForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        customer = form.save()
        messages.success(request, f'Klient "{customer.name}" zapisany.')
        return redirect("ui:planner_customers")
    return render(request, "ui/planner/customer_form.html", {
        "form": form, "instance": instance,
        # Reguły pakowania per indeks (edycja Master Data — patrz widoki customer_rule_*).
        "packaging_rules": (instance.packaging_rules.select_related("product")
                            .order_by("product__code") if instance else []),
        # B-007: te same role co widoki customer_rule_add/delete (@_md_role: Admin + MD)
        "can_edit_rules": has_role(request.user, GROUP_ADMIN, GROUP_MASTER_DATA),
    })


@_md_role
@require_POST
def customer_rule_add(request, pk):
    """Master Data dodaje regułę pakowania klient×indeks (np. 25 szt/opak. zamiast
    luzem w kartonie). Duplikat pary → podmiana wartości (update_or_create)."""
    from ui import product_codes
    from ui.models import CustomerPackagingRule
    customer = get_object_or_404(Customer, pk=pk)
    ref = (request.POST.get("ref_code") or "").strip()[:50]
    product = product_codes.resolve_product_code(ref) if ref else None
    if product is None:
        messages.error(request, f"Nie znaleziono indeksu „{ref}”.")
        return redirect("ui:planner_customer_edit", pk=pk)
    try:
        upp = int(request.POST.get("units_per_pack") or 0)
    except ValueError:
        upp = 0
    if upp <= 0:
        messages.error(request, "Podaj dodatnią liczbę sztuk na opakowanie.")
        return redirect("ui:planner_customer_edit", pk=pk)
    CustomerPackagingRule.objects.update_or_create(
        customer=customer, product=product,
        defaults={"units_per_pack": upp, "is_active": True,
                  "note": (request.POST.get("note") or "").strip()[:200],
                  "alert_email": (request.POST.get("alert_email") or "").strip()[:254]})
    messages.success(request, f"Reguła: {product.code} → {upp} szt/opak. dla {customer.name}.")
    return redirect("ui:planner_customer_edit", pk=pk)


@_md_role
@require_POST
def customer_rule_delete(request, rule_id):
    from ui.models import CustomerPackagingRule
    rule = get_object_or_404(CustomerPackagingRule, pk=rule_id)
    cpk = rule.customer_id
    rule.delete()
    messages.success(request, "Reguła pakowania usunięta.")
    return redirect("ui:planner_customer_edit", pk=cpk)


@_customer_mgr
def planner_customer_delete(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == "POST":
        name = customer.name
        customer.delete()
        messages.success(request, f'Klient "{name}" usunięty.')
        return redirect("ui:planner_customers")
    return render(request, "ui/planner/customer_confirm_delete.html", {"customer": customer})

__all__ = [
    "planner_customers",
    "_map_customer_columns",
    "planner_customer_import",
    "planner_customer_form",
    "customer_rule_add",
    "customer_rule_delete",
    "planner_customer_delete",
]
