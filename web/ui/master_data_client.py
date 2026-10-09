"""Client for the master-data reference data (products, customers, HU registry).

The whole point of Faza 3: call sites read reference data through THIS module, not the ORM
directly. When ``MASTER_DATA_URL`` is empty (the monolith today) it reads the local
database; set it to the master-data service's API base and the same calls fetch over HTTP.
Either way callers get the SAME normalised dict shape, so a service can be extracted by
flipping one env var — no call-site changes.

Returns plain dicts (matching the /api/v2 schema) so nothing downstream depends on Django
model instances. Detail lookups return ``None`` when not found.
"""
from django.conf import settings


def is_remote():
    return bool((getattr(settings, "MASTER_DATA_URL", "") or "").strip())


def _get(path, params=None):
    """HTTP GET against the master-data service; None on 404, raises on other errors."""
    import requests
    base = (getattr(settings, "MASTER_DATA_URL", "") or "").rstrip("/")
    key = getattr(settings, "MASTER_DATA_API_KEY", "") or ""
    resp = requests.get(f"{base}{path}", params=params or {},
                        headers={"X-API-Key": key}, timeout=5)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


# ── ORM → normalised dict (identical shape to the API schemas) ───────────────────
def _product_dict(p):
    return {"code": p.code, "name": p.name, "ean": p.ean, "supplier_short": p.supplier_short,
            "unit_length_cm": p.unit_length_cm, "unit_width_cm": p.unit_width_cm,
            "unit_height_cm": p.unit_height_cm, "stackable": p.stackable, "is_active": p.is_active}


def _customer_dict(c):
    return {"id": c.pk, "name": c.name, "code": c.code, "kind": c.kind, "country": c.country,
            "city": c.city, "max_pallet_height_cm": c.max_pallet_height_cm,
            "max_pallet_weight_kg": c.max_pallet_weight_kg,
            "requires_fumigated_pallet": c.requires_fumigated_pallet,
            "requires_adr": c.requires_adr, "pallet_type": c.pallet_type,
            "temp_control": c.temp_control, "is_active": c.is_active}


def _hu_dict(h):
    return {"ref": h.ref, "code": h.code, "status": h.status, "location": h.location,
            "warehouse_type": h.warehouse_type, "recipient_type": h.recipient_type,
            "shipment_id": h.shipment_id, "last_seen_at": h.last_seen_at}


# ── Public API — same signatures whether local or remote ─────────────────────────
def get_product(code):
    if is_remote():
        return _get(f"/products/{code}")
    from .models import Product
    p = Product.objects.filter(code__iexact=code).first()
    return _product_dict(p) if p else None


def list_products(q="", active=None, limit=100, offset=0):
    if is_remote():
        params = {"q": q, "limit": limit, "offset": offset}
        if active is not None:
            params["active"] = active
        return _get("/products", params) or []
    from .models import Product
    from django.db.models import Q
    qs = Product.objects.all()
    if active is not None:
        qs = qs.filter(is_active=active)
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(ean__icontains=q))
    return [_product_dict(p) for p in qs.order_by("code")[max(0, offset):max(0, offset) + max(1, min(limit, 1000))]]


def iter_products(q="", active=None, page_size=1000):
    """Yield every matching product across pages — for "give me all of them" call sites
    (e.g. an autocomplete datalist). A single ``list_products`` call is capped at 1000
    (locally and by the remote API), so this pages until a short/empty batch, keeping the
    same "all products" behaviour whether local or remote."""
    page_size = max(1, min(page_size, 1000))
    offset = 0
    while True:
        batch = list_products(q=q, active=active, limit=page_size, offset=offset)
        if not batch:
            return
        yield from batch
        if len(batch) < page_size:
            return
        offset += len(batch)


def get_customer(customer_id):
    if is_remote():
        return _get(f"/customers/{customer_id}")
    from .models import Customer
    # Fasada „czytaj przez ten moduł" — niezaufany nienumeryczny arg dałby na Postgresie
    # DataError (500) zamiast None. Waliduj int.
    if not str(customer_id).isdigit():
        return None
    c = Customer.objects.filter(pk=customer_id).first()
    return _customer_dict(c) if c else None


def get_handling_unit(code):
    if is_remote():
        return _get(f"/handling-units/{code}")
    from .models import HandlingUnit
    h = HandlingUnit.objects.filter(code=code).first()
    return _hu_dict(h) if h else None
