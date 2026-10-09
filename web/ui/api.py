"""Typed REST API (django-ninja) for the external HU scanner / integrations.

Auth: nagłówek ``X-API-Key`` → nazwany klient z zakresami (SEC-017, ``ui/api_auth.py``).
Settings to nadal dwa stringi: ``PALVIZ_API_TOKEN`` (zwykły token = klient ``legacy``,
pełny dostęp) i ``PALVIZ_API_TOKENS`` (CSV: zwykłe tokeny jak dotąd ALBO wpisy
``nazwa|zakres1+zakres2|token``). Brak tokenów = API wyłączone (każde wywołanie → 401).
Nieznany klucz → 401; znany klient bez zakresu endpointu → 403. Każde wywołanie loguje
(INFO, logger ``ui.api``) nazwę klienta + endpoint — nigdy token.

Zakresy endpointów (``auth=`` przy dekoratorze; domyślnie pełny dostęp = fail-closed):

=====================================================  =======================
``GET /health``                                        dowolny ważny klient
``GET /locations``, ``/locations/{code}``              ``read:locations``
``GET /products``, ``/products/{code}``                ``read:products``
``GET /customers``, ``/customers/{customer_id}``       ``read:customers``
``GET /handling-units``, ``/handling-units/{code}``    ``read:handling-units``
``POST /tasks``                                        ``write:tasks``
=====================================================  =======================

Auto-generated OpenAPI docs are served at ``/api/v2/docs`` (staff only).
"""
import logging
from datetime import datetime
from typing import List, Optional

from django.db import IntegrityError, transaction
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from ninja import NinjaAPI, Schema
from ninja.throttling import AuthRateThrottle

from .api_auth import (ANY_CLIENT, SCOPE_READ_CUSTOMERS, SCOPE_READ_HU, SCOPE_READ_LOCATIONS,
                       SCOPE_READ_PRODUCTS, SCOPE_WRITE_TASKS, ApiKey)
from .models import (WarehouseLocationMasterBatch,
                     Product, Customer, HandlingUnit, Task)

log = logging.getLogger("ui.api")


# Rate-limit per API key (keyed on str(request.auth) = klient@źródło, unikalne per token)
# so a misbehaving/compromised scanner client can't hammer the endpoints. HTTP 429 over the limit.
# Dokumentacja i schemat (/docs, /openapi.json) tylko dla staff (ACL-002) — publicznie
# zdradzały pełną mapę endpointów i modeli API skanerów.
api = NinjaAPI(title="GROOVE API", version="2.0.0", urls_namespace="ninja_api",
               auth=ApiKey(), throttle=[AuthRateThrottle("120/m")],
               docs_decorator=staff_member_required)


class LocationOut(Schema):
    location_code: str
    level: int
    warehouse_type: str
    width_mm: int
    depth_mm: int
    height_mm: int
    max_volume_m3: float
    max_weight_kg: float


class HealthOut(Schema):
    status: str
    active_batch: Optional[str] = None
    locations: int


@api.get("/health", response=HealthOut, auth=ApiKey(ANY_CLIENT))
def health(request):
    batch = WarehouseLocationMasterBatch.objects.filter(is_active=True).first()
    return {
        "status": "ok",
        "active_batch": batch.name if batch else None,
        "locations": batch.location_count if batch else 0,
    }


@api.get("/locations", response=List[LocationOut], auth=ApiKey(SCOPE_READ_LOCATIONS))
def list_locations(request, q: str = "", limit: int = 100, offset: int = 0):
    """List active-batch locations, optionally filtered by code substring.

    Backward-compatible paging: `limit` (1–1000) + `offset` let a client page past the
    first window without changing the bare-list response shape the scanner expects."""
    batch = WarehouseLocationMasterBatch.objects.filter(is_active=True).first()
    if not batch:
        return []
    qs = batch.locations.all()
    if q:
        qs = qs.filter(location_code__icontains=q)
    offset, limit = _page(offset, limit)
    return list(qs.order_by("location_code")[offset:offset + limit])


@api.get("/locations/{code}", response={200: LocationOut, 404: dict},
         auth=ApiKey(SCOPE_READ_LOCATIONS))
def get_location(request, code: str):
    batch = WarehouseLocationMasterBatch.objects.filter(is_active=True).first()
    if batch:
        loc = batch.locations.filter(location_code__iexact=code).first()
        if loc:
            return 200, loc
    return 404, {"detail": "Nie znaleziono lokalizacji."}


# ── Master data (Faza 3) ─────────────────────────────────────────────────────
# Read-only reference data that other GROOVE services consume instead of reaching into
# this database directly — the first step toward a standalone `master-data` service.

def _page(offset, limit):
    return max(0, offset), max(1, min(limit, 1000))


class ProductOut(Schema):
    code: str
    name: str
    ean: str = ""
    supplier_short: str = ""
    unit_length_cm: Optional[float] = None
    unit_width_cm: Optional[float] = None
    unit_height_cm: Optional[float] = None
    stackable: bool = True
    is_active: bool = True


class CustomerOut(Schema):
    id: int
    name: str
    code: str = ""
    kind: str = "customer"
    country: str = ""
    city: str = ""
    max_pallet_height_cm: Optional[int] = None
    max_pallet_weight_kg: Optional[int] = None
    requires_fumigated_pallet: bool = False
    requires_adr: bool = False
    pallet_type: str = ""
    temp_control: str = ""
    is_active: bool = True


class HandlingUnitOut(Schema):
    ref: str
    code: str = ""               # pickHU
    status: str
    location: str = ""
    warehouse_type: str = ""
    recipient_type: str = ""
    shipment_id: Optional[int] = None
    last_seen_at: Optional[datetime] = None


@api.get("/products", response=List[ProductOut], auth=ApiKey(SCOPE_READ_PRODUCTS))
def list_products(request, q: str = "", active: Optional[bool] = None,
                  limit: int = 100, offset: int = 0):
    """Reference product master. Filter by `active` and a code/name/EAN substring `q`."""
    qs = Product.objects.all()
    if active is not None:
        qs = qs.filter(is_active=active)
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(ean__icontains=q))
    offset, limit = _page(offset, limit)
    return list(qs.order_by("code")[offset:offset + limit])


@api.get("/products/{code}", response={200: ProductOut, 404: dict},
         auth=ApiKey(SCOPE_READ_PRODUCTS))
def get_product(request, code: str):
    p = Product.objects.filter(code__iexact=code).first()
    return (200, p) if p else (404, {"detail": "Nie znaleziono produktu."})


@api.get("/customers", response=List[CustomerOut], auth=ApiKey(SCOPE_READ_CUSTOMERS))
def list_customers(request, q: str = "", active: Optional[bool] = None,
                   limit: int = 100, offset: int = 0):
    """Reference customer/recipient master + their delivery requirements."""
    qs = Customer.objects.all()
    if active is not None:
        qs = qs.filter(is_active=active)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(city__icontains=q))
    offset, limit = _page(offset, limit)
    return list(qs.order_by("name")[offset:offset + limit])


@api.get("/customers/{customer_id}", response={200: CustomerOut, 404: dict},
         auth=ApiKey(SCOPE_READ_CUSTOMERS))
def get_customer(request, customer_id: int):
    c = Customer.objects.filter(pk=customer_id).first()
    return (200, c) if c else (404, {"detail": "Nie znaleziono klienta."})


@api.get("/handling-units", response=List[HandlingUnitOut], auth=ApiKey(SCOPE_READ_HU))
def list_handling_units(request, q: str = "", status: str = "", warehouse_type: str = "",
                        limit: int = 100, offset: int = 0):
    """The HU registry — the shared source of truth for pickHU / status / location."""
    qs = HandlingUnit.objects.all()
    if status:
        qs = qs.filter(status=status)
    if warehouse_type:
        qs = qs.filter(warehouse_type=warehouse_type)
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(recipient_type__icontains=q))
    offset, limit = _page(offset, limit)
    return list(qs.order_by("-created_at")[offset:offset + limit])


@api.get("/handling-units/{code}", response={200: HandlingUnitOut, 404: dict},
         auth=ApiKey(SCOPE_READ_HU))
def get_handling_unit(request, code: str):
    """Look up a handling unit by its (unique) pickHU code."""
    hu = HandlingUnit.objects.filter(code=code).first()
    return (200, hu) if hu else (404, {"detail": "Nie znaleziono HU."})


# ── Zapis zwrotny z automatów (n8n) — szew IN ────────────────────────────────
# Jedyny endpoint zapisu: pozwala orkiestratorowi (n8n) utworzyć zadanie zespołowe
# z wyniku automatu (np. wzbogacone „braki master data"). Auth X-API-Key z zakresem
# write:tasks (SEC-017), throttle jak reszta. Idempotentny po dedup_key — wzór z
# notifications._raise_task.
class TaskIn(Schema):
    title: str
    description: str = ""
    priority: str = "normal"          # low | normal | high
    dedup_key: str = ""               # podany → nie duplikuj otwartego zadania z tym kluczem
    url: str = ""
    related_product_code: str = ""
    related_location: str = ""


class TaskCreatedOut(Schema):
    id: int
    created: bool                     # False = zwrócono istniejące (dedup)
    dedup_key: str = ""


@api.post("/tasks", response={200: TaskCreatedOut, 400: dict}, auth=ApiKey(SCOPE_WRITE_TASKS))
def create_task(request, data: TaskIn):
    """Create a team task from an external automation. Idempotent: with a `dedup_key`
    that matches an OPEN task, returns that task instead of creating a duplicate."""
    title = (data.title or "").strip()
    if not title:
        return 400, {"detail": "Pole 'title' jest wymagane."}
    dedup = (data.dedup_key or "").strip()[:120]
    priority = data.priority if data.priority in {"low", "normal", "high"} else "normal"
    if dedup:
        existing = Task.objects.filter(dedup_key=dedup).exclude(status="done").first()
        if existing:
            return 200, {"id": existing.id, "created": False, "dedup_key": dedup}
    try:
        # Twarda gwarancja: partial-unique constraint (dedup_key, otwarte) w DB — działa
        # między workerami, gdzie mutex w locmem cache nie domykał okna wyścigu.
        with transaction.atomic():
            task = Task.objects.create(
                title=title[:200], description=data.description or "", category="manual",
                priority=priority, url=(data.url or "")[:300], dedup_key=dedup,
                related_product_code=(data.related_product_code or "")[:80],
                related_location=(data.related_location or "")[:50])
    except IntegrityError:
        # Przegrany wyścig równoległego retry — zwróć zadanie zwycięzcy.
        existing = Task.objects.filter(dedup_key=dedup).exclude(status="done").first()
        if existing:
            return 200, {"id": existing.id, "created": False, "dedup_key": dedup}
        raise
    log.info("API v2: klient=%s utworzył zadanie id=%s dedup_key=%s",
             getattr(request.auth, "name", "?"), task.id, dedup or "-")
    # Powiadom właścicieli master daty (in-app), jak przy niezgodnościach stocku.
    from .notifications import notify, owner_users
    notify(owner_users(), title, (data.description or "")[:400], level="warning", url=data.url or "")
    return 200, {"id": task.id, "created": True, "dedup_key": task.dedup_key}
