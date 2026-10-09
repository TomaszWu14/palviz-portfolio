# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from .core import (
    ProductCategory, _xlsx_float, Product, _xlsx_int, Carton, PalletizationInstruction,
    messages, _md_role, redirect, _read_xlsx_as_dicts, _after_import_redirect,
    InnerPack, _planner, _make_xlsx_response, _style_xlsx_header, _add_example_rows,
    _finalize_xlsx
)


def _apply_md_product_rows(rows, strict):
    """Create/update Product + Carton + PalletizationInstruction from PalViz-format row
    dicts — the shared core of the full-template importer and the MARM importer.
    Returns (created, updated, errors, row_errors, aborted)."""
    from django.db import transaction

    class _AbortImport(Exception):
        pass

    created = updated = errors = 0
    row_errors = []
    categories_by_code = {c.code.upper(): c for c in ProductCategory.objects.all()}
    try:
        # Outer transaction only matters in strict mode: per-row savepoints commit into
        # it, then we roll the whole thing back if any row failed.
        with transaction.atomic():
          for i, row in enumerate(rows, 1):
            try:
                with transaction.atomic():
                    code = row.get("product_code", "").strip()
                    if not code:
                        row_errors.append(f"Wiersz {i+2}: brak product_code — pominięto.")
                        errors += 1
                        continue

                    cat = None
                    cat_code = row.get("category_code", "").strip()
                    if cat_code:
                        cat = categories_by_code.get(cat_code.upper())

                    prod_defaults = {
                        "name":           row.get("product_name", "").strip() or code,
                        "ean":            row.get("product_ean", "").strip(),
                        "supplier_short": row.get("supplier_short", "").strip(),
                        "unit_length_cm": _xlsx_float(row.get("unit_l_cm")) if row.get("unit_l_cm") else None,
                        "unit_width_cm":  _xlsx_float(row.get("unit_w_cm")) if row.get("unit_w_cm") else None,
                        "unit_height_cm": _xlsx_float(row.get("unit_h_cm")) if row.get("unit_h_cm") else None,
                        "is_active":      True,
                    }
                    if cat is not None:
                        prod_defaults["category"] = cat

                    product, was_new = Product.objects.update_or_create(
                        code=code, defaults=prod_defaults
                    )
                    if was_new:
                        created += 1
                    else:
                        updated += 1

                    carton_name = row.get("carton_name", "").strip()
                    if carton_name:
                        cl, cw, ch = (_xlsx_int(row.get("carton_l_cm")),
                                      _xlsx_int(row.get("carton_w_cm")),
                                      _xlsx_int(row.get("carton_h_cm")))
                        # Puste/zero wymiary → nie twórz kartonu 1×1×1 (śmieciowa geometria).
                        if cl <= 0 or cw <= 0 or ch <= 0:
                            row_errors.append(f"Wiersz {i+2}: karton „{carton_name}” bez wymiarów — pominięto karton.")
                            errors += 1
                            continue
                        c_defaults = {
                            "ean":              row.get("carton_ean", "").strip(),
                            "length_cm":        cl,
                            "width_cm":         cw,
                            "height_cm":        ch,
                            "unit_weight_kg":   _xlsx_float(row.get("unit_weight_kg")),
                            "pieces_per_carton":_xlsx_int(row.get("pieces_per_carton"), 1),
                            "tare_kg":          _xlsx_float(row.get("carton_tare_kg")),
                            "is_active":        True,
                        }
                        carton, _ = Carton.objects.update_or_create(name=carton_name, defaults=c_defaults)

                        pallet_code = row.get("pallet_code", "").strip() or "EU"
                        max_h = _xlsx_int(row.get("max_height_cm"), 215)
                        max_w = _xlsx_int(row.get("max_weight_kg"), 1000)
                        demand = _xlsx_int(row.get("demand_pcs"), 1000)
                        last_v = (PalletizationInstruction.objects.filter(product=product)
                                  .order_by("-version").values_list("version", flat=True).first() or 0)
                        # Per-OP volume straight from SAP MARM (optional) — when set it
                        # drives the volume calc instead of recomputing from L×W×H.
                        uv = (_xlsx_float(row.get("unit_volume_m3"))
                              if row.get("unit_volume_m3") else None)
                        instr, _created = PalletizationInstruction.objects.get_or_create(
                            product=product, carton=carton,
                            defaults={
                                "version": last_v + 1,
                                "pallet_code": pallet_code,
                                "max_height_total_cm": max_h,
                                "max_weight_kg": max_w,
                                "demand_pcs": demand,
                                "carton_l": carton.length_cm,
                                "carton_w": carton.width_cm,
                                "carton_h": carton.height_cm,
                                "unit_weight": carton.unit_weight_kg,
                                "pcs_per_carton": carton.pieces_per_carton,
                                "carton_tare": carton.tare_kg,
                                "unit_volume_m3": uv,
                                "is_active": True,
                            }
                        )
                        # get_or_create fills NEW rows only, so a re-import would never
                        # refresh the SAP volume on an existing instruction — backfill it.
                        if not _created and uv is not None and instr.unit_volume_m3 != uv:
                            instr.unit_volume_m3 = uv
                            instr.save(update_fields=["unit_volume_m3"])
            except Exception as e:
                row_errors.append(f"Wiersz {i+2}: {e}")
                errors += 1

          if strict and errors:
              raise _AbortImport()        # roll back the whole outer transaction
    except _AbortImport:
        return (0, 0, errors, row_errors, True)
    return (created, updated, errors, row_errors, False)


def _md_import_messages(request, created, updated, errors, row_errors, aborted, what="produktów",
                        kind=None, label=""):
    # BLOK F: ślad importu (panel statusu importów) — sukces/abort z licznikami.
    if kind:
        from ..models import ImportRun
        ImportRun.record(kind, rows=created + updated, label=label, user=request.user,
                         error=(f"{errors} błędów — import wycofany" if aborted
                                else (f"{errors} błędów w wierszach" if errors else "")))
    if aborted:
        details = ("; ".join(row_errors[:5])) if row_errors else ""
        messages.error(request, f"Tryb „wszystko albo nic”: wykryto {errors} błędów — "
                       f"import wycofany, nie zapisano żadnych zmian. {details}".strip())
        return
    msg = f"Import {what}: {created} nowych, {updated} zaktualizowanych, {errors} błędów."
    if row_errors:
        msg += " Szczegóły: " + "; ".join(row_errors[:5])
    (messages.warning if errors else messages.success)(request, msg)


@_md_role
def excel_import_products(request):
    """Import products + cartons + instructions from the full xlsx template."""
    if request.method != "POST":
        return redirect("ui:planner_excel_templates")
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Nie wybrano pliku.")
        return redirect("ui:planner_excel_templates")
    if f.size > 10 * 1024 * 1024:
        messages.error(request, "Plik zbyt duży (max 10 MB).")
        return redirect("ui:planner_excel_templates")
    strict = request.POST.get("all_or_nothing") == "1"
    try:
        rows = _read_xlsx_as_dicts(f)
        _md_import_messages(request, *_apply_md_product_rows(rows, strict),
                            kind="products", label=getattr(f, "name", ""))
    except Exception as e:
        messages.error(request, f"Błąd wczytywania pliku: {e}")
    return _after_import_redirect(request, "ui:planner_products")


def _read_marm_rows(f):
    """Read a raw SAP MARM export (xlsx or csv) → (headers, list-of-row-dicts). Row 1 =
    headers, rows 2+ = data (no skipped description row, unlike the PalViz template)."""
    name = (getattr(f, "name", "") or "").lower()
    if name.endswith(".csv"):
        import csv
        raw = f.read()
        text = None
        for enc in ("utf-8-sig", "cp1250", "iso-8859-2", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("utf-8", "replace")
        first = text.split("\n", 1)[0]
        sep = ";" if first.count(";") > first.count(",") else ","
        reader = csv.DictReader(text.splitlines(), delimiter=sep)
        headers = [(h or "").strip() for h in (reader.fieldnames or [])]
        return headers, [dict(r) for r in reader]
    import openpyxl
    wb = openpyxl.load_workbook(f, data_only=True, read_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    headers = [str(c).strip() if c is not None else "" for c in next(it)]
    rows = [dict(zip(headers, r)) for r in it if any(v is not None for v in r)]
    return headers, rows


@_md_role
def excel_import_marm(request):
    """One-step master-data import straight from a raw SAP MARM export (Materiał /
    Alternatywna jednostka miary / Mianownik / Licznik / wymiary / Waga brutto /
    Objętość) — no CLI converter. Each material's OP/KAR/PAZ rows fold into a product +
    carton + instruction, and the MARM Objętość becomes the per-OP volume the shipment
    calc uses, so volumes match SAP 1:1."""
    if request.method != "POST":
        return redirect("ui:planner_excel_templates")
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Nie wybrano pliku.")
        return redirect("ui:planner_excel_templates")
    if f.size > 25 * 1024 * 1024:        # MARM exports are large (tens of thousands of rows)
        messages.error(request, "Plik zbyt duży (max 25 MB).")
        return redirect("ui:planner_excel_templates")
    strict = request.POST.get("all_or_nothing") == "1"
    try:
        from tools import sap_marm_to_palviz as marm
        headers, raw_rows = _read_marm_rows(f)
        col = marm._resolve_columns(headers)
        missing = [k for k in ("material", "unit") if k not in col]
        if missing:
            messages.error(request, "To nie wygląda na eksport MARM — brakuje kolumn "
                           f"{missing}. Wymagane m.in.: Materiał, Alternatywna jednostka miary, "
                           "Mianownik, Licznik, Szerokość/Wysokość/Długość, Waga brutto, Objętość.")
            return redirect("ui:planner_excel_templates")
        unit_map = {k: list(v) for k, v in marm.DEFAULT_UNIT_MAP.items()}
        palviz_rows = marm.convert(raw_rows, col, unit_map)
        _md_import_messages(request, *_apply_md_product_rows(palviz_rows, strict),
                            what="z MARM (materiałów)", kind="marm",
                            label=getattr(f, "name", ""))
    except Exception as e:
        messages.error(request, f"Błąd wczytywania MARM: {e}")
    return _after_import_redirect(request, "ui:planner_products")

@_md_role
def excel_import_inner_packs(request):
    """Import inner packs (opakowania zbiorcze) from xlsx template."""
    if request.method != "POST":
        return redirect("ui:planner_excel_templates")

    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Nie wybrano pliku.")
        return redirect("ui:planner_excel_templates")
    if f.size > 5 * 1024 * 1024:
        messages.error(request, "Plik zbyt duży (max 5 MB).")
        return redirect("ui:planner_excel_templates")

    try:
        rows = _read_xlsx_as_dicts(f)
        created = updated = errors = 0
        for row in rows:
            name = row.get("name", "").strip()
            if not name:
                errors += 1
                continue
            try:
                upp = _xlsx_int(row.get("units_per_pack"))
                defaults = {
                    "length_cm":          _xlsx_float(row.get("length_cm"), 1),
                    "width_cm":           _xlsx_float(row.get("width_cm"), 1),
                    "height_cm":          _xlsx_float(row.get("height_cm"), 1),
                    "tare_kg":            _xlsx_float(row.get("tare_kg")),
                    "units_per_pack":     upp if upp else None,
                    "sales_unit_l_cm":    _xlsx_float(row.get("sales_unit_l_cm")) if row.get("sales_unit_l_cm") else None,
                    "sales_unit_w_cm":    _xlsx_float(row.get("sales_unit_w_cm")) if row.get("sales_unit_w_cm") else None,
                    "sales_unit_h_cm":    _xlsx_float(row.get("sales_unit_h_cm")) if row.get("sales_unit_h_cm") else None,
                    "sales_units_per_pack":_xlsx_int(row.get("sales_units_per_pack"), 1),
                    "notes":              row.get("notes", "").strip(),
                    "is_active":          row.get("is_active", "1").strip() not in ("0", "false", "False", "nie"),
                }
                _, was_new = InnerPack.objects.update_or_create(name=name, defaults=defaults)
                if was_new:
                    created += 1
                else:
                    updated += 1
            except Exception:
                errors += 1

        messages.success(request, f"Import opakowań zbiorczych: {created} nowych, {updated} zaktualizowanych, {errors} błędów.")
        from ..models import ImportRun
        ImportRun.record("inner_packs", rows=created + updated,
                         label=getattr(f, "name", ""), user=request.user,
                         error=f"{errors} błędów w wierszach" if errors else "")
    except Exception as e:
        messages.error(request, f"Błąd wczytywania pliku: {e}")

    return _after_import_redirect(request, "ui:planner_inner_packs")

@_md_role
def excel_import_categories(request):
    """Import product categories from xlsx template."""
    if request.method != "POST":
        return redirect("ui:planner_excel_templates")

    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Nie wybrano pliku.")
        return redirect("ui:planner_excel_templates")
    if f.size > 2 * 1024 * 1024:
        messages.error(request, "Plik zbyt duży (max 2 MB).")
        return redirect("ui:planner_excel_templates")

    try:
        rows = _read_xlsx_as_dicts(f)
        created = updated = errors = 0
        for row in rows:
            name = row.get("name", "").strip()
            code = row.get("code", "").strip().upper()
            if not name or not code:
                errors += 1
                continue
            try:
                color = row.get("color", "#3b82f6").strip() or "#3b82f6"
                defaults = {
                    "description": row.get("description", "").strip(),
                    "color":       color,
                }
                _, was_new = ProductCategory.objects.update_or_create(
                    code=code,
                    defaults={"name": name, **defaults}
                )
                if was_new:
                    created += 1
                else:
                    updated += 1
            except Exception:
                errors += 1

        messages.success(request, f"Import kategorii: {created} nowych, {updated} zaktualizowanych, {errors} błędów.")
        from ..models import ImportRun
        ImportRun.record("categories", rows=created + updated,
                         label=getattr(f, "name", ""), user=request.user,
                         error=f"{errors} błędów w wierszach" if errors else "")
    except Exception as e:
        messages.error(request, f"Błąd wczytywania pliku: {e}")

    return _after_import_redirect(request, "ui:planner_categories")

@_planner
def excel_template_products(request):
    wb, ws, response = _make_xlsx_response("PalViz_produkty_wzor.xlsx")
    ws.title = "Produkty"
    cols = [
        # (name, description, width, example)
        ("product_code",   "Kod materiału / SKU — unikalny identyfikator", 18, "SKU-001"),
        ("product_name",   "Pełna nazwa produktu",                          28, "Kubek ceramiczny 300ml"),
        ("product_ean",    "Kod EAN / barcode (opcjonalnie)",               16, "5901234123457"),
        ("supplier_short", "Skrót dostawcy (opcjonalnie)",                  14, "DEMODOST"),
        ("category_code",  "Kod kategorii z PalViz (opcjonalnie)",          14, "KUBKI"),
        ("unit_l_cm",      "Długość sztuki [cm]",                            10, 8),
        ("unit_w_cm",      "Szerokość sztuki [cm]",                          10, 8),
        ("unit_h_cm",      "Wysokość sztuki [cm]",                           10, 10),
        ("carton_name",    "Nazwa kartonu zbiorczego",                       22, "Karton 40×30×25"),
        ("carton_ean",     "EAN kartonu (opcjonalnie)",                      16, ""),
        ("carton_l_cm",    "Długość kartonu [cm]",                           11, 40),
        ("carton_w_cm",    "Szerokość kartonu [cm]",                         11, 30),
        ("carton_h_cm",    "Wysokość kartonu [cm]",                          11, 25),
        ("unit_weight_kg", "Waga netto sztuki [kg]",                         14, 0.45),
        ("pieces_per_carton","Sztuk produktu w kartonie",                    14, 24),
        ("carton_tare_kg", "Tara kartonu [kg]",                              13, 0.2),
        ("unit_volume_m3", "Objętość 1 szt/OP [m³] z MARM (opcj.)",          18, ""),
        ("pallet_code",    "Kod palety: EU=120×80",                          11, "EU"),
        ("max_height_cm",  "Max wys. ładunku na palecie [cm]",               14, 215),
        ("max_weight_kg",  "Max waga palety z ładunkiem [kg]",               14, 800),
        ("demand_pcs",     "Zamawiana ilość [szt] do kalkulacji",             14, 2400),
    ]
    _style_xlsx_header(ws, cols, "1A56DB")
    rows = [
        ["SKU-001","Kubek ceramiczny 300ml","5901234123457","DEMODOST","KUBKI",8,8,10,"Karton A 40x30x25","5901234000001",40,30,25,0.45,24,0.2,"EU",215,800,2400],
        ["SKU-002","Talerz głęboki 24cm","","CERAMEX","TALERZE",24,24,4,"Karton B 60x40x20","",60,40,20,0.60,12,0.3,"EU",215,800,1200],
        ["SKU-003","Misa sałatkowa 28cm","5901234123459","CERAMEX","MISKI",28,28,8,"Karton C 45x35x30","",45,35,30,0.80,9,0.25,"EU",215,800,900],
    ]
    _add_example_rows(ws, cols, rows)
    return _finalize_xlsx(wb, ws, response)

@_planner
def excel_template_inner_packs(request):
    wb, ws, response = _make_xlsx_response("PalViz_opakowania_zbiorcze_wzor.xlsx")
    ws.title = "Opakowania zbiorcze"
    cols = [
        ("name",               "Unikalna nazwa opakowania zbiorczego",    28, "Blister 6 szt"),
        ("length_cm",          "Długość [cm]",                             11, 25),
        ("width_cm",           "Szerokość [cm]",                           11, 17),
        ("height_cm",          "Wysokość [cm]",                            11, 12),
        ("tare_kg",            "Tara opakowania zbiorczego [kg]",          16, 0.05),
        ("units_per_pack",     "Sztuk produktu w opakowaniu (opcjonalnie)",18, 6),
        ("sales_unit_l_cm",    "L jednostki sprzedaży [cm] (opcj.)",       16, 8),
        ("sales_unit_w_cm",    "W jednostki sprzedaży [cm] (opcj.)",       16, 8),
        ("sales_unit_h_cm",    "H jednostki sprzedaży [cm] (opcj.)",       16, 10),
        ("sales_units_per_pack","Szt jedn. sprzedaży w opak. (opcj.)",     18, 1),
        ("notes",              "Uwagi",                                    20, ""),
        ("is_active",          "1=aktywny, 0=nieaktywny",                  12, 1),
    ]
    _style_xlsx_header(ws, cols, "7C3AED")
    rows = [
        ["Blister 6 szt",25,17,12,0.05,6,8,8,10,1,"",1],
        ["Display 12 szt",30,22,20,0.08,12,8,8,10,1,"Ekspozycja",1],
        ["Tray 4 szt",22,18,9,0.04,4,8,8,10,1,"",1],
    ]
    _add_example_rows(ws, cols, rows)
    return _finalize_xlsx(wb, ws, response)

@_planner
def excel_template_categories(request):
    wb, ws, response = _make_xlsx_response("PalViz_kategorie_wzor.xlsx")
    ws.title = "Kategorie"
    cols = [
        ("name",        "Pełna nazwa kategorii — unikalna",     24, "Kubki i filiżanki"),
        ("code",        "Kod kategorii — krótki identyfikator", 16, "KUBKI"),
        ("color",       "Kolor w formacie HEX np. #3b82f6",     14, "#3b82f6"),
        ("description", "Opis kategorii (opcjonalnie)",         30, "Wyroby ceramiczne do picia"),
    ]
    _style_xlsx_header(ws, cols, "F59E0B")
    rows = [
        ["Kubki i filiżanki","KUBKI","#3b82f6","Wyroby ceramiczne do picia"],
        ["Talerze","TALERZE","#10b981","Talerze płaskie i głębokie"],
        ["Miski i salaterki","MISKI","#f59e0b","Miski do sałatek i zup"],
        ["Kieliszki","KIELISZKI","#8b5cf6","Szkło stołowe"],
        ["Garnki i rondle","GARNKI","#ef4444","Naczynia do gotowania"],
    ]
    _add_example_rows(ws, cols, rows)
    return _finalize_xlsx(wb, ws, response)

__all__ = [
    'excel_import_products',
    'excel_import_marm',
    'excel_import_inner_packs',
    'excel_import_categories',
    'excel_template_products',
    'excel_template_inner_packs',
    'excel_template_categories',
]
