# core/ — shared kernel split into layers (base ◅ figures/xlsx/helpers ◅ packing).
# Re-exported wholesale by core/__init__.py so `from .core import *` is unchanged.
# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
import csv
import io
import json
import logging
from datetime import datetime, date

from django.conf import settings
from ...roles import (
    has_role,
    GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_CONTROLLER, GROUP_LEADER, _admin_only, _master_data as _md_role, _transport_mgr, _controller, _leader, _md_or_tr,
    _md_or_control, _customer_mgr, _optimizer, _any_role,
)
from ...platform_modules import module_required, can_open_module   # noqa: F401
from django.contrib import messages
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.utils.http import url_has_allowed_host_and_scheme


def _safe_next(request, default):
    """Redirect do `next` z żądania, ale TYLKO gdy wskazuje na nasz host.

    `next` przychodzi z formularza, więc jest danymi od użytkownika — `redirect()` na
    surowej wartości to open redirect: link do widoku z podstawionym
    `next=https://obcy.example/login` odsyła kontrolera na cudzą stronę logowania już
    po zalogowaniu (klasyczny phishing na zaufanej domenie). `url_has_allowed_host_and_scheme`
    przepuszcza ścieżki względne i nasz host; `require_https` pilnuje, by po HTTPS nie
    zejść na http."""
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(
            nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(nxt)
    return redirect(default)


def _safe_referer(request, default):
    """Redirect z powrotem na Referer, ale TYLKO gdy wskazuje na nasz host (inaczej `default`).
    Referer jest ustawiany przez przeglądarkę, więc ryzyko niższe niż przy `next`, ale surowy
    `redirect(referer)` to wciąż open redirect — walidujemy host tak jak w `_safe_next`."""
    ref = request.META.get("HTTP_REFERER") or ""
    if ref and url_has_allowed_host_and_scheme(
            ref, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(ref)
    return redirect(default)


def _after_import_redirect(request, default):
    """Redirect to a safe same-host `next` (e.g. back to Data Center) if the import was
    launched with one; otherwise to the view's default page. The count message set by the
    importer rides along, so Data Center shows the import summary."""
    return _safe_next(request, default)

import openpyxl
import plotly.graph_objects as go
from plotly.utils import PlotlyJSONEncoder

import math


def _fig_json(obj):
    """Serialize a Plotly figure to a JSON string that is SAFE to embed inside an
    HTML ``<script>…</script>`` via ``|safe``.

    Plain ``json.dumps`` leaves ``<``/``>``/``&`` literal, so user-controlled text
    that ends up in a figure (e.g. a location/product name containing ``</script>``)
    could break out of the script element → stored XSS. Escaping these to their
    ``\\uXXXX`` form (plus the JS line separators U+2028/U+2029) is transparent to
    JSON.parse / Plotly but cannot terminate the script tag — same approach Django's
    ``json_script`` uses.
    """
    s = json.dumps(obj, cls=PlotlyJSONEncoder)
    s = s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def safe_json(obj):
    """Like ``_fig_json`` but for plain data (dicts/lists). JSON that is SAFE to embed in
    a ``<script>`` via ``|safe`` \u2014 escapes ``<``/``>``/``&`` and the JS line separators so
    user text (e.g. a product code containing ``</script>``) can't break out of the tag.
    Transparent to ``JSON.parse``."""
    from django.core.serializers.json import DjangoJSONEncoder
    s = json.dumps(obj, cls=DjangoJSONEncoder)
    s = s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

from ...forms import (
    ManualForm, UploadCSVForm, OptimizerForm,
    ProductForm, CartonForm, InnerPackForm, InstructionForm,
    ErrorReportForm, ErrorReportStatusForm,
    CarrierForm, CarrierZoneForm, CarrierRateForm,
    ShipmentForm,
)
from ...models import (
    Batch, Palletization,
    Product, ProductCategory, Carton, CartonArtwork, InnerPack, PalletizationInstruction, ErrorReport,
    WarehouseLocationType,
    WarehouseSnapshot, WarehouseSnapshotRow,
    WarehouseLayout, WarehouseLayoutCell, WarehouseAisleConfig,
    WarehouseLocationMasterBatch, WarehouseLocationMaster,
    WarehouseRackType,
    MaterialReference,
    Carrier, CarrierZone, CarrierRate, QuoteRecipient, ShipmentQuoteOffer, Customer,
    WarehouseReadiness, DriverAssignment, Shipment, ShipmentLine, SiteInfo,
    HandlingUnit, HandlingUnitItem, HUQualityIssue, HUControlAttempt, ControlledWarehouseType,
    ControllerZone, HUStatusEvent,
    WarehouseModel, WarehouseModelRack, WarehouseHallFeature,
    PickerActivityBatch, PickerActivity,
)

from palletizer.config import get_pallet_preset
from palletizer.domain import PalletType, Dimensions, CartonVariant
from palletizer.services import PalletCalculator
from django.db import transaction


from .base_tables import (  # noqa: F401  (tabele stalych — re-exported, not consumed here)
    SUFFIX_LEVEL,
    PALLET_BASE_HEIGHT_CM, _CARTON_FC, _CARTON_LAYER_PALETTES, _COL_CODE_MAP,
    _COL_IDX, _COMBINED_COL_ALIASES, _DT_FORMATS, _ED3D_COMPACT, _ED3D_FLOOR,
    _ED3D_LOC_PAT, _ED3D_PICKING_TYPES, _ED3D_STANDARD, _ED3D_SUFFIX_LEVEL,
    _FACE_I, _FACE_J, _FACE_K, _INNER_PACK_FC, _PALLET_BOARD_FC,
    _PALLET_STRINGER_FC, _PRODUCT_COLORS, _RACK_FC, _SALES_FC, _SAP_COLS,
    _UNIT_FC,
)













































# ═══════════════════════════════════════════════════════════════════════════════
#  Warehouse worker views
# ═══════════════════════════════════════════════════════════════════════════════







# ═══════════════════════════════════════════════════════════════════════════════
#  Planner views — Products
# ═══════════════════════════════════════════════════════════════════════════════

# Ekrany planera (odczyt): wymagają członkostwa w DOWOLNEJ z 9 ról (superuser przechodzi).
# Samo zalogowanie nie wystarcza — konto bez grupy (np. świeżo z SSO) dostaje 403 (SEC-004).
# Wąskich ról celowo nie zawężamy (decyzja właściciela Q-30). Profil/hasło/wylogowanie
# mają własne login_required, więc konto bez roli nadal może z nich korzystać.
_planner = _any_role




# ─── Product categories ────────────────────────────────────────────────────────







# ─── Stock movements ───────────────────────────────────────────────────────────







# ─── Warehouse model builder ───────────────────────────────────────────────────

import re as _re

# ── Location-code suffix → 3D-editor beam index ──
# SUFFIX_LEVEL is the 3D EDITOR's beam index (0 = floor, X/G/H = first beam, …).
# The physical level 1..5 of a location (master `level`, map cells, height backfill)
# comes from ONE place: ui.views.core.ewm_levels.letter_slot (zone-aware; B/C/D are
# shelves inside level 1, G/H split X vertically, S–W = 1–5, hall A: A–E = 1–5).


























# ═══════════════════════════════════════════════════════════════════════════════
#  Planner views — Cartons
# ═══════════════════════════════════════════════════════════════════════════════









# ── Carton artwork (face prints + placeable labels/stickers) ────────────────────

_ART_FACES = {"front", "back", "left", "right", "top", "bottom"}
_ART_KINDS = {"print", "label"}
_ART_MAX_BYTES = 8 * 1024 * 1024
_ART_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg"}















# ─── Excel import helpers ─────────────────────────────────────────────────────



















# ─── Excel template helpers ────────────────────────────────────────────────────



































# ═══════════════════════════════════════════════════════════════════════════════
#  Planner views — Instructions
# ═══════════════════════════════════════════════════════════════════════════════



















# ═══════════════════════════════════════════════════════════════════════════════
#  Planner views — Error reports
# ═══════════════════════════════════════════════════════════════════════════════





# ═══════════════════════════════════════════════════════════════════════════════
#  Packaging optimizer — fit carton → pallet, fill %, overhang tolerance
# ═══════════════════════════════════════════════════════════════════════════════











# ═══════════════════════════════════════════════════════════════════════════════
#  Legacy planner (calculator) views — kept under /planner/calc/
# ═══════════════════════════════════════════════════════════════════════════════















# ═══════════════════════════════════════════════════════════════════════════════
#  Planner views — Inner packs (opakowania zbiorcze)
# ═══════════════════════════════════════════════════════════════════════════════







# ═══════════════════════════════════════════════════════════════════════════════
#  Planner views — Warehouse locations
# ═══════════════════════════════════════════════════════════════════════════════




























# Export every name (incl. single-underscore helpers) so feature modules and
# external callers (tests, tasks) keep importing them from ui.views.


def _pk4(value):
    """Bezpieczny PK (int4) z wejścia użytkownika: nienumeryczny LUB przepełnienie zakresu
    PostgreSQL integer → 0 (brak dopasowania → 404), zamiast DataError 500. SQLite tolerował."""
    s = str(value or "").strip()
    return int(s) if s.isdigit() and int(s) < 2_147_483_648 else 0


__all__ = [
    # Django/stdlib/palletizer re-exports (own explicit imports above)
    'Batch', 'Carrier', 'CarrierForm', 'CarrierRate', 'CarrierRateForm', 'CarrierZone',
    'CarrierZoneForm', 'Carton', 'CartonArtwork', 'CartonForm', 'CartonVariant',
    'ControlledWarehouseType', 'ControllerZone', 'Count', 'Customer', 'Dimensions',
    'DriverAssignment', 'ErrorReport', 'ErrorReportForm', 'ErrorReportStatusForm',
    'GROUP_ADMIN', 'GROUP_CONTROLLER', 'GROUP_LEADER', 'GROUP_MASTER_DATA',
    'HUControlAttempt', 'HUQualityIssue', 'HUStatusEvent', 'HandlingUnit',
    'HandlingUnitItem', 'HttpResponse', 'InnerPack', 'InnerPackForm', 'InstructionForm',
    'JsonResponse', 'ManualForm', 'MaterialReference', 'OptimizerForm', 'Paginator',
    'PalletCalculator', 'PalletType', 'Palletization', 'PalletizationInstruction',
    'PickerActivity', 'PickerActivityBatch', 'PlotlyJSONEncoder', 'Product',
    'ProductCategory', 'ProductForm', 'Q', 'QuoteRecipient', 'Shipment', 'ShipmentForm',
    'ShipmentLine', 'ShipmentQuoteOffer', 'SiteInfo', 'UploadCSVForm',
    'WarehouseAisleConfig', 'WarehouseHallFeature', 'WarehouseLayout',
    'WarehouseLayoutCell', 'WarehouseLocationMaster', 'WarehouseLocationMasterBatch',
    'WarehouseLocationType', 'WarehouseModel', 'WarehouseModelRack', 'WarehouseRackType',
    'WarehouseReadiness', 'WarehouseSnapshot', 'WarehouseSnapshotRow', 'csv', 'date',
    'datetime', 'get_object_or_404', 'get_pallet_preset', 'go', 'has_role', 'io',
    'json', 'logging', 'login_required', 'mark_safe', 'math', 'messages',
    'module_required', 'openpyxl', 'redirect', 'render', 'render_to_string',
    'require_POST', 'send_mail', 'settings', 'transaction',
    'url_has_allowed_host_and_scheme',
    # base.py's own definitions
    '_ART_CONTENT_TYPES', '_ART_FACES', '_ART_KINDS', '_ART_MAX_BYTES', '_admin_only',
    '_after_import_redirect', '_any_role', '_controller', '_customer_mgr', '_fig_json',
    '_leader', '_md_or_control', '_md_or_tr', '_md_role', '_optimizer', '_pk4',
    '_planner', '_re', '_safe_next', '_safe_referer', '_transport_mgr', 'safe_json',
    # re-exported from base_tables.py (only base.py forwards these to core/__init__.py)
    'SUFFIX_LEVEL',
    'PALLET_BASE_HEIGHT_CM', '_CARTON_FC', '_CARTON_LAYER_PALETTES', '_COL_CODE_MAP',
    '_COL_IDX', '_COMBINED_COL_ALIASES', '_DT_FORMATS', '_ED3D_COMPACT', '_ED3D_FLOOR',
    '_ED3D_LOC_PAT', '_ED3D_PICKING_TYPES', '_ED3D_STANDARD', '_ED3D_SUFFIX_LEVEL',
    '_FACE_I', '_FACE_J', '_FACE_K', '_INNER_PACK_FC', '_PALLET_BOARD_FC',
    '_PALLET_STRINGER_FC', '_PRODUCT_COLORS', '_RACK_FC', '_SALES_FC', '_SAP_COLS',
    '_UNIT_FC',
]
