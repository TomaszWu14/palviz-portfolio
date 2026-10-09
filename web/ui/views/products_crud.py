# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from .core import (
    _planner, Product, Q, PalletizationInstruction, ProductCategory, Paginator, render,
    _md_role, get_object_or_404, Carton, ProductForm, _save_instruction_from_carton,
    messages, redirect, MaterialReference, require_POST, HttpResponse, _parse_float,
    _parse_int, InnerPack, _recalculate_instruction
)


@_planner
def planner_products(request):
    q = request.GET.get("q", "").strip()
    supplier = request.GET.get("supplier", "").strip()
    category_id = request.GET.get("category", "").strip()
    no_instr = request.GET.get("no_instr", "").strip()
    qs = (Product.objects
          .select_related("category")
          .prefetch_related("instructions__carton__inner_pack")
          .order_by("code"))
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(ean__icontains=q))
    if supplier:
        qs = qs.filter(supplier_short__icontains=supplier)
    if category_id:
        qs = qs.filter(category_id=category_id)
    if no_instr:
        products_with_instr = PalletizationInstruction.objects.filter(
            is_active=True).values_list("product_id", flat=True).distinct()
        qs = qs.filter(is_active=True).exclude(pk__in=products_with_instr)
    suppliers = Product.objects.exclude(supplier_short="").values_list("supplier_short", flat=True).distinct().order_by("supplier_short")
    categories = ProductCategory.objects.all()
    page_obj = Paginator(qs, 25).get_page(request.GET.get("page", 1))
    return render(request, "ui/planner/products.html",
                  {"page_obj": page_obj, "query": q, "supplier": supplier,
                   "suppliers": suppliers, "categories": categories,
                   "category_id": category_id, "no_instr": no_instr,
                   "active_tab": "products"})

@_md_role
def planner_product_form(request, pk=None):
    obj = get_object_or_404(Product, pk=pk) if pk else None
    cartons_qs = Carton.objects.filter(is_active=True).order_by("name")

    ref_data = None
    if request.method == "POST":
        # request.FILES — pole glb_model (model 3D poziomu OP/sztuka).
        form = ProductForm(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            product = form.save()

            # -- Optional: create or link a carton + instruction ----------------
            carton_mode = request.POST.get("carton_mode", "none")  # "none"|"existing"|"new"
            instr = None

            if carton_mode == "existing":
                carton_pk = request.POST.get("carton_pk", "").strip()
                if carton_pk:
                    try:
                        linked_carton = Carton.objects.get(pk=int(carton_pk))
                        instr = _save_instruction_from_carton(product, linked_carton, request.POST)
                    except (Carton.DoesNotExist, ValueError):
                        pass

            elif carton_mode == "new":
                try:
                    c_weight_raw = float(request.POST.get("c_weight") or 0.0)
                    if c_weight_raw <= 0:
                        raise ValueError("Waga jednostkowa kartonu musi być większa od 0.")
                    from django.db import transaction as _tx
                    with _tx.atomic():
                        linked_carton = Carton.objects.create(
                            name=request.POST.get("c_name", "").strip() or f"Karton {product.code}",
                            length_cm=max(1, int(request.POST.get("c_l") or 40)),
                            width_cm=max(1, int(request.POST.get("c_w") or 30)),
                            height_cm=max(1, int(request.POST.get("c_h") or 25)),
                            unit_weight_kg=c_weight_raw,
                            pieces_per_carton=max(1, int(request.POST.get("c_pcs") or 1)),
                            tare_kg=float(request.POST.get("c_tare") or 0.0),
                        )
                        instr = _save_instruction_from_carton(product, linked_carton, request.POST)
                except Exception as exc:
                    messages.warning(request, f"Produkt zapisany, błąd kartonu: {exc}")

            if instr:
                messages.success(request, f"Produkt i instrukcja paletyzacji {product.code} v{instr.version} zapisane.")
                return redirect("ui:planner_instruction_detail", pk=instr.pk)

            messages.success(request, f"Produkt {'zaktualizowany' if pk else 'dodany'} pomyślnie.")
            return redirect("ui:planner_products")
    else:
        form = ProductForm(instance=obj)
        if not obj:
            code = request.GET.get("code", "").strip()
            if code:
                try:
                    ref_data = MaterialReference.objects.get(code=code)
                    form = ProductForm(initial={
                        "code": ref_data.code,
                        "name": ref_data.name,
                        "supplier_short": ref_data.supplier_short,
                    })
                except MaterialReference.DoesNotExist:
                    pass

    return render(request, "ui/planner/product_form.html", {
        "form": form, "obj": obj,
        "cartons_qs": cartons_qs,
        "ref_data": ref_data,
    })

@require_POST
@_md_role
def planner_product_delete(request, pk: int):
    get_object_or_404(Product, pk=pk).delete()
    messages.success(request, "Produkt usunięty.")
    return redirect("ui:planner_products")

@_planner
def planner_product_csv_template(request):
    """Download full-hierarchy CSV template for product master data import."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="produkty_master_wzor.csv"'
    response.write("﻿")  # UTF-8 BOM for Excel
    import csv as csv_mod
    w = csv_mod.writer(response)
    w.writerow([
        # Product
        "product_code", "product_name", "product_ean", "product_description",
        "unit_l_cm", "unit_w_cm", "unit_h_cm",
        # Inner pack (optional — leave blank to skip)
        "inner_name", "inner_l_cm", "inner_w_cm", "inner_h_cm",
        "inner_units_per_pack", "inner_tare_kg",
        "sales_unit_l_cm", "sales_unit_w_cm", "sales_unit_h_cm", "sales_units_per_pack",
        # Carton
        "carton_name", "carton_ean", "carton_l_cm", "carton_w_cm", "carton_h_cm",
        "unit_weight_kg", "pieces_per_carton", "carton_tare_kg", "packs_per_carton",
        # Palletization instruction (optional — leave blank to skip)
        "pallet_code", "max_height_cm", "max_weight_kg", "demand_pcs",
    ])
    # Example row — product with inner pack, carton, and instruction
    w.writerow([
        "SKU-001", "Kubek ceramiczny 300ml", "5901234123457", "Kubek biały z logo",
        8, 8, 10,
        "Blister 6 szt", 25, 17, 12, 6, 0.05, 8, 8, 10, 1,
        "Karton A 40x30x25", "5901234000001", 40, 30, 25, 0.35, 24, 0.2, 4,
        "EU", 215, 1000, 2400,
    ])
    # Example row — product without inner pack
    w.writerow([
        "SKU-002", "Talerz głęboki 24cm", "", "",
        24, 24, 4,
        "", "", "", "", "", "", "", "", "", "",
        "Karton B 60x40x35", "", 60, 40, 35, 0.80, 12, 0.3, "",
        "EU", 215, 800, 1200,
    ])
    # Example row — carton only (no instruction)
    w.writerow([
        "SKU-003", "Filiżanka 200ml", "", "",
        "", "", "",
        "", "", "", "", "", "", "", "", "", "",
        "Karton C 30x20x20", "", 30, 20, 20, 0.20, 36, 0.15, "",
        "", "", "", "",
    ])
    return response

@_md_role
def planner_product_import(request):
    """Import full product hierarchy (Product + InnerPack + Carton + Instruction) from CSV."""
    if request.method != "POST":
        return redirect("ui:planner_products")

    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Nie wybrano pliku.")
        return redirect("ui:planner_products")

    def _f(row, *keys, default=None):
        """Get first non-empty value from row by multiple key aliases."""
        for k in keys:
            v = row.get(k, "").strip()
            if v:
                return v
        return default

    _float = _parse_float          # wspólny helper (views/core) — tolerancja przecinka
    _int = _parse_int

    try:
        import csv as csv_mod
        from django.db import transaction
        from django.db.models import Max as _Max
        decoded = f.read().decode("utf-8-sig")
        reader = csv_mod.DictReader(decoded.splitlines())

        products_created = products_updated = 0
        inner_packs_created = inner_packs_updated = 0
        cartons_created = cartons_updated = 0
        instructions_created = 0
        row_errors = []

        for i, row in enumerate(reader, 1):
            try:
                with transaction.atomic():
                    # ── 1. Product ────────────────────────────────────────
                    code = _f(row, "product_code", "code", "sku")
                    if not code:
                        row_errors.append(f"Wiersz {i}: brak product_code — pominięto.")
                        continue

                    prod_defaults = {
                        "name":           _f(row, "product_name", "name") or code,
                        "ean":            _f(row, "product_ean") or "",
                        "description":    _f(row, "product_description", "description") or "",
                        "unit_length_cm": _float(_f(row, "unit_l_cm")) or None,
                        "unit_width_cm":  _float(_f(row, "unit_w_cm")) or None,
                        "unit_height_cm": _float(_f(row, "unit_h_cm")) or None,
                        "is_active":      True,
                    }
                    product, was_new = Product.objects.update_or_create(
                        code=code, defaults=prod_defaults
                    )
                    if was_new:
                        products_created += 1
                    else:
                        products_updated += 1

                    # ── 2. InnerPack (optional) ───────────────────────────
                    inner_pack = None
                    inner_name = _f(row, "inner_name", "innerpack_name")
                    if inner_name:
                        ip_defaults = {
                            "length_cm":          _float(_f(row, "inner_l_cm"), 1),
                            "width_cm":           _float(_f(row, "inner_w_cm"), 1),
                            "height_cm":          _float(_f(row, "inner_h_cm"), 1),
                            "units_per_pack":     _int(_f(row, "inner_units_per_pack"), 1),
                            "tare_kg":            _float(_f(row, "inner_tare_kg")),
                            "sales_unit_l_cm":    _float(_f(row, "sales_unit_l_cm")) or None,
                            "sales_unit_w_cm":    _float(_f(row, "sales_unit_w_cm")) or None,
                            "sales_unit_h_cm":    _float(_f(row, "sales_unit_h_cm")) or None,
                            "sales_units_per_pack": _int(_f(row, "sales_units_per_pack"), 1),
                            "is_active": True,
                        }
                        inner_pack, ip_new = InnerPack.objects.update_or_create(
                            name=inner_name, defaults=ip_defaults
                        )
                        if ip_new:
                            inner_packs_created += 1
                        else:
                            inner_packs_updated += 1

                    # ── 3. Carton ─────────────────────────────────────────
                    carton_name = _f(row, "carton_name", "name")
                    if not carton_name:
                        # No carton data — product row only
                        continue

                    uw = _float(_f(row, "unit_weight_kg", "unit_weight"))
                    if uw <= 0:
                        row_errors.append(f"Wiersz {i} ({code}): unit_weight_kg musi być > 0 — karton pominięto.")
                        continue

                    carton_defaults = {
                        "ean":              _f(row, "carton_ean") or "",
                        "length_cm":        _int(_f(row, "carton_l_cm", "l")),
                        "width_cm":         _int(_f(row, "carton_w_cm", "w")),
                        "height_cm":        _int(_f(row, "carton_h_cm", "h")),
                        "unit_weight_kg":   uw,
                        "pieces_per_carton": _int(_f(row, "pieces_per_carton", "pcs"), 1),
                        "tare_kg":          _float(_f(row, "carton_tare_kg", "tare")),
                        "inner_pack":       inner_pack,
                        "packs_per_carton": _int(_f(row, "packs_per_carton")) or None,
                        "is_active":        True,
                    }
                    carton, c_new = Carton.objects.update_or_create(
                        name=carton_name, defaults=carton_defaults
                    )
                    if c_new:
                        cartons_created += 1
                    else:
                        cartons_updated += 1

                    # ── 4. PalletizationInstruction (optional) ────────────
                    pallet_code = _f(row, "pallet_code") or "EU"
                    demand = _int(_f(row, "demand_pcs"))
                    max_h = _int(_f(row, "max_height_cm"), 215)
                    max_w = _int(_f(row, "max_weight_kg"), 1000)
                    has_instr_data = any([
                        _f(row, "pallet_code"), _f(row, "demand_pcs"),
                        _f(row, "max_height_cm"), _f(row, "max_weight_kg"),
                    ])

                    if has_instr_data and carton.length_cm > 0:
                        product_locked = Product.objects.select_for_update().get(pk=product.pk)
                        last_v = product_locked.instructions.aggregate(m=_Max("version"))["m"] or 0
                        instr = PalletizationInstruction.objects.create(
                            product=product_locked,
                            version=last_v + 1,
                            carton=carton,
                            pallet_code=pallet_code,
                            max_height_total_cm=max_h,
                            max_weight_kg=max_w,
                            carton_l=carton.length_cm,
                            carton_w=carton.width_cm,
                            carton_h=carton.height_cm,
                            unit_weight=carton.unit_weight_kg,
                            pcs_per_carton=carton.pieces_per_carton,
                            carton_tare=carton.tare_kg,
                            demand_pcs=demand,
                        )
                        instructions_created += 1
                        try:
                            _recalculate_instruction(instr)
                        except Exception as exc:
                            import logging
                            logging.getLogger(__name__).warning(
                                "Import: recalculate failed for instr %s: %s", instr.pk, exc
                            )

            except Exception as e:
                row_errors.append(f"Wiersz {i}: {e}")

        summary = (
            f"Import zakończony. "
            f"Produkty: +{products_created} nowych, {products_updated} zaktualizowanych. "
            f"Opak. zbiorcze: +{inner_packs_created} nowych, {inner_packs_updated} zaktualizowanych. "
            f"Kartony: +{cartons_created} nowych, {cartons_updated} zaktualizowanych. "
            f"Instrukcje: +{instructions_created} nowych."
        )
        if row_errors:
            summary += f" Błędy w {len(row_errors)} wierszach."
            for e in row_errors[:5]:
                messages.warning(request, e)
        messages.success(request, summary)

    except Exception as e:
        messages.error(request, f"Błąd wczytywania pliku: {e}")

    return redirect("ui:planner_products")

__all__ = [
    'planner_products',
    'planner_product_form',
    'planner_product_delete',
    'planner_product_csv_template',
    'planner_product_import',
]
