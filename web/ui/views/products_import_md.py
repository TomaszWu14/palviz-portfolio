# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
import logging
from .core import (
    _md_role, require_POST, messages, redirect, _read_table, Product,
    PalletizationInstruction, InnerPack, Carton, PALLET_BASE_HEIGHT_CM, ShipmentLine, _planner,
    get_object_or_404, _recalculate_instruction, render
)

from .core.xlsx import MAX_IMPORT_ROWS
from .products_import_md_parse import fractional_counters, import_size_error, parse_master_data

log = logging.getLogger(__name__)


_IMPORT_NAME = "Import migracji"
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _upload_error(f):
    """Komunikat walidacji przesłanego pliku albo None."""
    if not f:
        return "Nie wybrano pliku."
    if f.size > _MAX_UPLOAD_BYTES:
        return "Plik zbyt duży (max 20 MB)."
    return None


def _split_products(parsed, codes):
    """Rekordy → (nowe Product, istniejące Product z nadpisanymi polami)."""
    existing = {p.code: p for p in Product.objects.filter(code__in=codes)}
    to_create, to_update = [], []
    for p in parsed:
        ul, uw, uh = (p["unit"] or (None, None, None))
        obj = existing.get(p["code"])
        if obj:
            obj.name = p["name"]
            # Puste pole w pliku NIE nadpisuje istniejącej wartości (MARM nie ma
            # kolumny dostawcy, EAN bywa pusty) — reimport nie może zerować danych.
            obj.ean = p["ean"] or obj.ean
            obj.supplier_short = p["supplier"] or obj.supplier_short
            obj.unit_length_cm, obj.unit_width_cm, obj.unit_height_cm = ul, uw, uh
            obj.is_active = True
            to_update.append(obj)
        else:
            to_create.append(Product(
                code=p["code"], name=p["name"], ean=p["ean"], supplier_short=p["supplier"],
                unit_length_cm=ul, unit_width_cm=uw, unit_height_cm=uh, is_active=True))
    return to_create, to_update


def _inner_pack_fields(p):
    """Pola InnerPack z opakowania zbiorczego (opz) rekordu; jednostka sprzedaży = op."""
    ol, ow, oh = p["opz"]
    su = p["op"] or (None, None, None)
    su_per = (round(p["opz_pcs"] / p["op_pcs"]) if (p["op"] and p["op_pcs"] > 0) else 1) or 1
    return dict(
        length_cm=round(ol, 1), width_cm=round(ow, 1), height_cm=round(oh, 1),
        units_per_pack=max(1, p["opz_pcs"] or 1),
        sales_unit_l_cm=su[0], sales_unit_w_cm=su[1], sales_unit_h_cm=su[2],
        sales_units_per_pack=su_per, is_active=True)


def _upsert_inner_packs(opz_rows):
    """Opakowanie zbiorcze (opz) → InnerPack nazwany '<kod>-opz'; zwraca {kod: InnerPack}."""
    if not opz_rows:
        return {}
    ip_names = {f"{p['code']}-opz" for p in opz_rows}
    existing_ip = {ip.name: ip for ip in InnerPack.objects.filter(name__in=ip_names)}
    ip_create, ip_update = [], []
    for p in opz_rows:
        name = f"{p['code']}-opz"
        fields = _inner_pack_fields(p)
        ip = existing_ip.get(name)
        if ip:
            for k, v in fields.items():
                setattr(ip, k, v)
            ip_update.append(ip)
        else:
            ip_create.append(InnerPack(name=name, **fields))
    InnerPack.objects.bulk_create(ip_create, batch_size=1000)
    InnerPack.objects.bulk_update(
        ip_update, ["length_cm", "width_cm", "height_cm", "units_per_pack",
                    "sales_unit_l_cm", "sales_unit_w_cm", "sales_unit_h_cm",
                    "sales_units_per_pack", "is_active"], batch_size=1000)
    ip_objs = {ip.name: ip for ip in InnerPack.objects.filter(name__in=ip_names)}
    return {p["code"]: ip_objs[f"{p['code']}-opz"] for p in opz_rows}


def _own_inner_pack_ids():
    """Id opakowań zbiorczych utworzonych przez import: nazwa ``<kod>-opz`` i powiązanie
    z instrukcją „Import migracji” TEGO produktu. Pomija opakowania używane też
    poza importem (ręczne instrukcje, kartony) — model nie ma znacznika źródła."""
    own = {ip_id for ip_id, ip_name, code in (
        PalletizationInstruction.objects
        .filter(name=_IMPORT_NAME, inner_pack__isnull=False)
        .values_list("inner_pack_id", "inner_pack__name", "product__code"))
        if ip_name == f"{code}-opz"}
    shared = set(PalletizationInstruction.objects
                 .filter(inner_pack_id__in=own).exclude(name=_IMPORT_NAME)
                 .values_list("inner_pack_id", flat=True))
    shared |= set(Carton.objects.filter(inner_pack_id__in=own)
                  .values_list("inner_pack_id", flat=True))
    return own - shared


def _max_versions(target_ids):
    """Usuwa instrukcje z poprzedniego importu (ręcznych nie rusza) i zwraca
    {product_id: najwyższa pozostała wersja}."""
    PalletizationInstruction.objects.filter(
        product_id__in=target_ids, name=_IMPORT_NAME).delete()
    max_ver = {}
    for pid, ver in (PalletizationInstruction.objects
                     .filter(product_id__in=target_ids)
                     .values_list("product_id", "version")):
        max_ver[pid] = max(max_ver.get(pid, 0), ver)
    return max_ver


def _build_instruction(p, prod, ver, ip):
    cl, cw, ch = (max(1, int(x + 0.5)) for x in p["carton"])   # round half up
    # opz/karton → how many inner packs per carton, pieces per inner pack
    has_opz = bool(ip and p["opz_pcs"] > 0)
    pcs_ip = p["opz_pcs"] if has_opz else None
    packs = round(p["pcs"] / p["opz_pcs"]) if has_opz else None
    # Per-piece weight already derived in the parse step (MARM waga brutto).
    # Fall back to a negligible 0.001 kg so the calculator validates and the
    # palletization stays height-limited (weight never binds).
    unit_weight = round(p["weight"], 4) if p["weight"] > 0 else 0.001
    return PalletizationInstruction(
        product=prod, version=ver, name=_IMPORT_NAME,
        pallet_code="EU", pallet_length_cm=120, pallet_width_cm=80,
        max_height_total_cm=p["pallet_h"] or 215, pallet_base_height_cm=PALLET_BASE_HEIGHT_CM,
        max_weight_kg=p["pallet_w"] or 1000,
        carton_l=cl, carton_w=cw, carton_h=ch,
        unit_weight=unit_weight, pcs_per_carton=max(1, p["pcs"]),
        carton_tare=0.0, demand_pcs=p["demand"] or 1000, is_active=True,
        units_per_piece=p.get("units_per_piece", 1) or 1,
        inner_pack=ip, pcs_per_inner_pack=pcs_ip, packs_per_carton=packs)


def _write_instructions(carton_rows, prod_by_code, ip_by_code):
    """Nowe instrukcje „Import migracji” (kolejna wersja per produkt), bulk_create."""
    max_ver = _max_versions([prod_by_code[p["code"]].id for p in carton_rows])
    instrs = []
    for p in carton_rows:
        prod = prod_by_code[p["code"]]
        ver = max_ver.get(prod.id, 0) + 1
        max_ver[prod.id] = ver
        instrs.append(_build_instruction(p, prod, ver, ip_by_code.get(p["code"])))
    PalletizationInstruction.objects.bulk_create(instrs, batch_size=1000)
    return instrs


def _persist_import(parsed, overwrite):
    """Zapis rekordów w jednej transakcji → (to_create, to_update, instrs, carton_rows)."""
    from django.db import transaction as _tx

    codes = [p["code"] for p in parsed]
    to_create, to_update = _split_products(parsed, codes)
    with _tx.atomic():
        if overwrite:
            own_ip = _own_inner_pack_ids()      # przed kasowaniem instrukcji (powiązanie)
            PalletizationInstruction.objects.filter(name=_IMPORT_NAME).delete()
            InnerPack.objects.filter(pk__in=own_ip).delete()
        Product.objects.bulk_create(to_create, batch_size=1000)
        Product.objects.bulk_update(
            to_update, ["name", "ean", "supplier_short",
                        "unit_length_cm", "unit_width_cm", "unit_height_cm", "is_active"],
            batch_size=1000)

        prod_by_code = {p.code: p for p in Product.objects.filter(code__in=codes)}
        carton_rows = [p for p in parsed if p["carton"]]
        ip_by_code = _upsert_inner_packs([p for p in carton_rows if p["opz"]])
        instrs = _write_instructions(carton_rows, prod_by_code, ip_by_code)
    return to_create, to_update, instrs, carton_rows


@_md_role
@require_POST
def planner_master_data_import(request):
    """Import SAP-migration master data → Products + palletization instructions.

    Format: ';'-separated CSV, dimensions combined as 'L X W X H cm' (comma decimals),
    duplicated headers (read by column position). No weight column ⇒ unit_weight=0
    (palletization is height-limited). One instruction per product that has carton
    dimensions; layouts are computed lazily when the product is first opened.
    Bulk create/update so 15k+ rows import in one request without timing out.
    Parsowanie: ``products_import_md_parse``; zapis: ``_persist_import`` i helpery."""
    # "Nadpisz" — wipe every prior import (instructions + the opz packs the import
    # itself created) so the new file becomes the single source of truth, even for
    # products it no longer lists. Ręcznie założone opakowania *-opz zostają.
    overwrite = bool(request.POST.get("overwrite"))

    f = request.FILES.get("file")
    error = _upload_error(f)
    if error:
        messages.error(request, error)
        return redirect("ui:planner_products")

    try:
        header, body = _read_table(f)
        if not body:
            messages.error(request, "Plik nie zawiera danych.")
            return redirect("ui:planner_products")
        too_big = import_size_error(header, body, MAX_IMPORT_ROWS)
        if too_big:
            messages.error(request, too_big)
            return redirect("ui:planner_products")
        parsed, dups = parse_master_data(header, body)
        to_create, to_update, instrs, carton_rows = _persist_import(parsed, overwrite)
        messages.success(
            request,
            f"Import master daty: {len(to_create)} nowych produktów, {len(to_update)} zaktualizowanych, "
            f"{len(instrs)} instrukcji paletyzacji ({len([p for p in carton_rows if p['opz']])} z opak. zbiorczym). "
            f"Układy przeliczą się przy pierwszym otwarciu produktu.")
        if dups:
            messages.warning(
                request,
                f"Pominięto {dups} zduplikowanych kodów produktu (także po przycięciu do "
                f"50 znaków) — obowiązuje pierwszy wiersz danego kodu.")
        fractions, n_frac = fractional_counters(header, body)
        if fractions:
            messages.warning(request, f"Niecałkowity licznik/mianownik MARM zaokrąglono do "
                             f"pełnych sztuk ({n_frac}): " + "; ".join(fractions)
                             + (" …" if n_frac > len(fractions) else ""))
    except Exception as exc:
        messages.error(request, f"Błąd importu: {exc}")
    return redirect("ui:planner_products")

@_md_role
@require_POST
def planner_master_data_purge(request):
    """Delete imported master data so it can be rebuilt from a fresh file.

    scope: 'instructions' (import instructions only), 'inner_packs' (opz packs only),
    'products' (all products — cascades their instructions) or 'all' (everything above).
    Products referenced by shipments/movements are protected and skipped."""
    from django.db.models import ProtectedError
    scope = request.POST.get("scope", "")
    done = []
    try:
        # Opakowania „z importu” (ta sama reguła co „Nadpisz”; ręczne *-opz zostają) ustalamy
        # PRZED kasowaniem instrukcji — reguła opiera się na instrukcji „Import migracji”.
        own_packs = _own_inner_pack_ids() if scope in ("inner_packs", "all") else set()
        if scope in ("instructions", "all"):
            n = PalletizationInstruction.objects.filter(name="Import migracji").delete()[0]
            done.append(f"{n} instrukcji z importu")
        if scope in ("inner_packs", "all"):
            n = InnerPack.objects.filter(pk__in=own_packs).delete()[1].get("ui.InnerPack", 0)
            done.append(f"{n} opakowań zbiorczych z importu")
        if scope in ("products", "all"):
            try:
                n = Product.objects.all().delete()[0]
                done.append(f"{n} produktów (z hierarchią)")
            except ProtectedError:
                # Some products are referenced by shipments/movements — delete the rest.
                protected = set(ShipmentLine.objects.values_list("product_id", flat=True))
                n = Product.objects.exclude(pk__in=protected).delete()[0]
                done.append(f"{n} produktów (pominięto {len(protected)} powiązanych z wysyłkami)")
        if done:
            messages.success(request, "Usunięto: " + ", ".join(done) + ".")
        else:
            messages.warning(request, "Nie wybrano zakresu usuwania.")
    except Exception as exc:
        messages.error(request, f"Błąd usuwania: {exc}")
    return redirect("ui:planner_products")

@_planner
def planner_product_hierarchy(request, pk: int):
    """Hierarchia opakowań produktu — dane z WSPÓLNEGO serwisu ui/hierarchy.py
    (to samo źródło co skaner/PHV: identyczne liczby na obu widokach)."""
    from ..hierarchy import build_hierarchy, enrich_pallet_metrics
    product = get_object_or_404(Product, pk=pk)
    instr = product.latest_instruction()
    # Lazily compute the pallet layout the first time an imported instruction is opened
    # (the bulk importer creates instructions without running the calculator).
    if instr and not instr.layouts:
        try:
            _recalculate_instruction(instr)
        except Exception:
            log.exception("Leniwe przeliczenie instrukcji paletyzacji nie powiodło się")
    h = build_hierarchy(product, instr)
    summary = h["summary"]
    levels = h["levels"]

    # Metryki prezentacji (vol_pct + per-poziom mult) — wspólny helper z PHV, jedno
    # źródło = identyczne liczby na obu widokach.
    vol_pct = enrich_pallet_metrics(levels, summary, instr)

    return render(request, "ui/planner/product_hierarchy.html", {
        "product": product,
        "instr": h["instr"],
        "levels": levels,
        "summary": summary,
        "vol_pct": vol_pct,
        "alerts": h["alerts"],
        "active_tab": "products",
    })

__all__ = [
    'planner_master_data_import',
    'planner_master_data_purge',
    'planner_product_hierarchy',
]
