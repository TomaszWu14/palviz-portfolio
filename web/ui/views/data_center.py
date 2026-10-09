# Data Center — the master-data hub (GROOVE module). One place to manage and upload all
# master data: products, packaging (cartons / inner packs), categories, current stock,
# customers, reference materials. Links into the existing master-data screens.
from .core import (
    module_required, Product, Carton, InnerPack, ProductCategory, Customer,
    HandlingUnit, MaterialReference, has_role, GROUP_ADMIN, render, Q, _md_role,
    PalletizationInstruction, require_POST, messages, redirect, _ART_MAX_BYTES,
    _ART_CONTENT_TYPES
)
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.urls import reverse

from ..hierarchy import build_hierarchy, LEVEL_KEYS, LEVEL_NAMES
from ..models import InnerPackArtwork, ProductArtwork, CartonArtwork, DictionaryEntry

# Poziomy z własnym modelem grafiki (render do wgrania). Reszta (paleta/handlowe/JU)
# jest tylko wyliczana z wymiarów — nie ma osobnej grafiki do wsadu.
# Wartość = (nazwa url_name uploadu, nazwa atrybutu ownera na wierszu macierzy).
_ART_LEVELS = {
    "carton":     "ui:carton_artwork_upload",
    "inner_pack": "ui:inner_pack_artwork_upload",
    "unit":       "ui:product_artwork_upload",
}


@module_required("data_center")
def data_center(request):
    """Landing for the Data Center module — cards linking to each master-data area,
    with quick counts so it doubles as an at-a-glance overview."""
    cards = [
        {"key": "products",  "label": "Produkty",            "icon": "package", "color": "#3b82f6",
         "desc": "Indeksy / SKU, hierarchia opakowań",        "url": "ui:planner_products",
         "count": Product.objects.count()},
        {"key": "cartons",   "label": "Kartony / opakowania", "icon": "archive", "color": "#0ea5e9",
         "desc": "Rozmiary kartonów i opakowań zbiorczych",   "url": "ui:planner_cartons",
         "count": Carton.objects.count()},
        {"key": "inner",     "label": "Opakowania zbiorcze",  "icon": "ruler", "color": "#06b6d4",
         "desc": "Jednostki zbiorcze i przeliczniki",          "url": "ui:planner_inner_packs",
         "count": InnerPack.objects.count()},
        {"key": "categories","label": "Kategorie",            "icon": "tag", "color": "#8b5cf6",
         "desc": "Kategorie produktów",                        "url": "ui:planner_categories",
         "count": ProductCategory.objects.count()},
        {"key": "customers", "label": "Klienci",              "icon": "handshake", "color": "#f59e0b",
         "desc": "Baza klientów i wymagania dostaw",           "url": "ui:planner_customers",
         "count": Customer.objects.count()},
        {"key": "stock",     "label": "Stock magazynowy",     "icon": "chart-column", "color": "#10b981",
         "desc": "Aktualny stan (zaimportowane HU)",           "url": "ui:planner_stock",
         "count": HandlingUnit.objects.count()},
        {"key": "materials", "label": "Materiały referencyjne","icon": "blocks", "color": "#64748b",
         "desc": "Słownik materiałów / wymiarów",              "url": "ui:planner_ref_materials",
         "count": MaterialReference.objects.count()},
        {"key": "matrix",    "label": "Biblioteka grafik",     "icon": "image", "color": "#a855f7",
         "desc": "Macierz REF × jednostki + render kartonów",  "url": "ui:packaging_matrix",
         "count": None},
        {"key": "dictionary","label": "Słownik pojęć",         "icon": "book-open", "color": "#14b8a6",
         "desc": "Kody SAP i ich znaczenie (rodzaj procesu…)", "url": "ui:data_center_dictionary",
         "count": DictionaryEntry.objects.count()},
        {"key": "templates", "label": "Szablony / import",    "icon": "upload", "color": "#ef4444",
         "desc": "Szablony Excel i import master data",        "url": "ui:planner_excel_templates",
         "count": None},
        {"key": "producer_dims", "label": "Wymiary producenta", "icon": "ruler", "color": "#f97316",
         "desc": "Porównanie wymiarów kartonu wg dostawców z master data", "url": "ui:producer_dims",
         "count": None},
    ]

    # Unified import/update center — gather the scattered template + upload endpoints.
    # tpl = download-template url_name (or None), imp = upload endpoint (POST, file).
    imports = [
        {"label": "SAP MARM (jeden plik: produkty + kartony + objętość)", "icon": "factory",
         "tpl": "ui:excel_template_marm", "imp": "ui:excel_import_marm"},
        {"label": "Produkty", "icon": "package", "tpl": "ui:excel_template_products",
         "imp": "ui:excel_import_products"},
        {"label": "Kartony / opakowania", "icon": "archive", "tpl": "ui:excel_template_cartons",
         "imp": "ui:excel_import_cartons"},
        {"label": "Opakowania zbiorcze", "icon": "ruler", "tpl": "ui:excel_template_inner_packs",
         "imp": "ui:excel_import_inner_packs"},
        {"label": "Kategorie", "icon": "tag", "tpl": "ui:excel_template_categories",
         "imp": "ui:excel_import_categories"},
        {"label": "Klienci", "icon": "handshake", "tpl": "ui:excel_template_customers",
         "imp": "ui:planner_customer_import"},
        {"label": "Stock magazynowy (HU, plik PowerBI/SAP)", "icon": "chart-column",
         "tpl": "ui:excel_template_stock_hu", "imp": "ui:planner_hu_import"},
        # Komplet danych do kontroli HU (zastępuje feed SAP, dopóki nie ma integracji).
        # Ten sam importer co stock — wzór zawiera dodatkowo partię producenta,
        # datę produkcji i kompletującego.
        {"label": "Dane do kontroli HU (partia producenta, picker, lokalizacja)", "icon": "search",
         "tpl": "ui:excel_template_control_data", "imp": "ui:planner_hu_import"},
        # Fixy (stałe lokalizacje pickingowe) z SAP — zasila „Zapas / min" na karcie produktu.
        {"label": "Fixy — stałe lokalizacje pickingowe (min/maks z SAP)", "icon": "map-pin",
         "tpl": "ui:excel_template_fix", "imp": "ui:excel_import_fix"},
    ]
    # Import kont widoczny tylko dla adminów (tworzy użytkowników i nadaje role).
    if request.user.is_superuser or has_role(request.user, GROUP_ADMIN):
        imports.append({"label": "Użytkownicy (konta, role, strefy, dostęp do modułów)",
                        "icon": "users", "tpl": "ui:excel_template_users",
                        "imp": "ui:excel_import_users"})
    from .. import powerbi
    return render(request, "ui/data_center.html", {
        "cards": cards, "imports": imports, "powerbi_configured": powerbi.is_configured(),
        "health": _completeness_metrics()})


@module_required("data_center")
def data_center_dictionary(request):
    """Słownik pojęć — katalog referencyjny (tylko odczyt). Kody SAP i ich znaczenie,
    pogrupowane. Domyślna kategoria: 'Rodzaj procesu'. `?cat=` przełącza kategorię,
    `?q=` filtruje po kodzie/opisie/grupie."""
    valid = {c[0] for c in DictionaryEntry.CATEGORY}
    category = request.GET.get("cat") or "process_type"
    if category not in valid:
        category = "process_type"
    q = (request.GET.get("q") or "").strip()
    entries = DictionaryEntry.objects.filter(category=category)
    if q:
        entries = entries.filter(Q(code__icontains=q) | Q(label__icontains=q)
                                 | Q(group__icontains=q))
    rows = list(entries)
    groups = {}
    for e in rows:
        groups.setdefault(e.group or "—", []).append(e)
    grouped = [{"name": name, "entries": items} for name, items in groups.items()]
    return render(request, "ui/data_center/dictionary.html", {
        "grouped": grouped, "category": category, "categories": DictionaryEntry.CATEGORY,
        "q": q, "total": len(rows)})


def _completeness_metrics():
    """At-a-glance master-data health for the Data Center landing. Returns counts of gaps
    and an overall completeness score (% of active products that are calculation-ready,
    i.e. have an active palletization instruction and an EAN)."""
    products = list(Product.objects.filter(is_active=True).prefetch_related("instructions"))
    total = len(products)
    no_instr = sum(1 for p in products if p.latest_instruction() is None)
    no_ean = sum(1 for p in products if not (p.ean or "").strip())

    by_ean = {}
    for p in products:
        ean = (p.ean or "").strip()
        if ean:
            by_ean.setdefault(ean, 0)
            by_ean[ean] += 1
    dup_ean = sum(1 for n in by_ean.values() if n > 1)

    bad_cartons = Carton.objects.filter(is_active=True).filter(
        Q(length_cm__lte=0) | Q(width_cm__lte=0) | Q(height_cm__lte=0) | Q(unit_weight_kg__lte=0)
    ).count()

    ready = sum(1 for p in products if p.latest_instruction() is not None and (p.ean or "").strip())
    score = round(100 * ready / total) if total else 100
    gaps = no_instr + no_ean + dup_ean + bad_cartons
    return {"total": total, "ready": ready, "score": score, "gaps": gaps,
            "no_instr": no_instr, "no_ean": no_ean, "dup_ean": dup_ean,
            "bad_cartons": bad_cartons}


def _matrix_owner_pk(key, product, instr, carton, ip):
    """PK obiektu-właściciela grafiki dla danego poziomu (albo None gdy brak obiektu)."""
    if key == "carton":
        return carton.pk if carton else None
    if key == "inner_pack":
        return ip.pk if ip else None
    if key == "unit":
        return product.pk
    return None


def _matrix_artwork_owners(rows):
    """PK-i właścicieli, którzy MAJĄ już wgraną grafikę — trzy zapytania na stronę
    zamiast `.exists()` na komórkę (przy 40 wierszach było ich do 120).
    → {"carton": {pk…}, "inner_pack": {pk…}, "unit": {pk…}}"""
    carton_pks = {r["carton"].pk for r in rows if r["carton"]}
    ip_pks = {r["ip"].pk for r in rows if r["ip"]}
    product_pks = {r["product"].pk for r in rows}
    return {
        "carton": set(CartonArtwork.objects.filter(carton_id__in=carton_pks)
                      .values_list("carton_id", flat=True)),
        "inner_pack": set(InnerPackArtwork.objects.filter(inner_pack_id__in=ip_pks)
                          .values_list("inner_pack_id", flat=True)),
        "unit": set(ProductArtwork.objects.filter(product_id__in=product_pks)
                    .values_list("product_id", flat=True)),
    }


@_md_role
def packaging_matrix(request):
    """Biblioteka grafik opakowań — macierz REF × jednostki. Dla każdego indeksu pokazuje,
    które poziomy opakowań (paleta/karton/OPZ/handlowe/sztuka/JU) są używane, a które nie,
    i czy poziom z możliwością grafiki (karton/OPZ/sztuka) ma już wgrany render.

    Stan komórki:
      • absent — poziom nieużywany przez ten REF (wg build_hierarchy + kategorii) → szary,
      • used   — poziom używany, ale bez slotu na grafikę (paleta/handlowe/JU) → neutralny,
      • todo   — poziom używany z możliwą grafiką, brak renderu → śliwkowy, klikalny (upload),
      • done   — poziom używany i ma wgrany render → zielony + ✓ (potwierdzone).

    Który poziom jest „używany" bierzemy z build_hierarchy (jedno źródło prawdy — ten sam
    filtr kategorii co MatInfo), więc macierz nigdy nie rozjedzie się z kartą hierarchii.
    """
    q = (request.GET.get("q") or "").strip()
    # Prefetch instrukcji (z kartonem i OPZ) — `latest_instruction()` korzysta z cache
    # prefetcha, więc cała strona kosztuje stałą liczbę zapytań zamiast rosnąć z liczbą
    # wierszy. Bez tego każdy wiersz dokładał instrukcję + karton + OPZ osobno.
    qs = (Product.objects.filter(is_active=True)
          .select_related("category")
          # Tylko aktywne wersje — dokładnie to, co bierze pod uwagę latest_instruction().
          # Wersje narastają przy każdym imporcie Excela i nic ich nie dezaktywuje, a każda
          # niesie tłusty JSON `layouts`; bez filtra strona dekodowałaby setki wierszy, żeby
          # użyć czterdziestu — czyli oszczędność na zapytaniach opłacona pamięcią.
          .prefetch_related(Prefetch(
              "instructions",
              queryset=PalletizationInstruction.objects.filter(is_active=True)
              .select_related("carton", "inner_pack", "carton__inner_pack")))
          .order_by("code"))
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))

    page = Paginator(qs, 40).get_page(request.GET.get("page"))

    columns = [{"key": k, "name": LEVEL_NAMES[k]} for k in LEVEL_KEYS]
    # Najpierw zbierz wiersze (bez stanów komórek), potem JEDNYM zapytaniem na poziom
    # sprawdź, gdzie render już jest — stan komórki to potem lookup w secie.
    raw_rows = []
    for product in page:
        instr = product.latest_instruction()
        carton = instr.carton if instr else None
        ip = (instr.inner_pack or (carton.inner_pack if carton else None)) if instr else None
        # with_artwork=False: macierz potrzebuje tylko ZESTAWU poziomów, a nie grafik —
        # ich serializacja i tak lądowała w koszu.
        used = {l["key"] for l in build_hierarchy(product, instr, with_artwork=False)["levels"]}
        raw_rows.append({"product": product, "instr": instr, "carton": carton,
                         "ip": ip, "used": used})

    has_art = _matrix_artwork_owners(raw_rows)

    rows = []
    for r in raw_rows:
        cells = []
        for key in LEVEL_KEYS:
            cell = {"key": key}
            if key not in r["used"]:
                cell["state"] = "absent"
            elif key == "ju":
                # SZTUKA (JU): media na produkcie (ju_glb_model/ju_image) — jeden
                # endpoint, format po rozszerzeniu (.glb / PNG / JPG).
                p = r["product"]
                cell["state"] = "done" if (p.ju_glb_model or p.ju_image) else "todo"
                cell["ju_url"] = reverse("ui:product_ju_upload", args=[p.pk])
            elif key not in _ART_LEVELS:
                cell["state"] = "used"
            else:
                owner_pk = _matrix_owner_pk(key, r["product"], r["instr"], r["carton"], r["ip"])
                if owner_pk is None:                      # poziom liczony, ale bez obiektu-ownera
                    cell["state"] = "used"
                else:
                    # .get(): nowy poziom w _ART_LEVELS bez wpisu w _matrix_artwork_owners
                    # ma degradować do „todo", a nie wywalać całą stronę na KeyError.
                    cell["state"] = "done" if owner_pk in has_art.get(key, ()) else "todo"
                    # URL także dla „done" — kafelek z ✓ ma w szablonie podpowiedź
                    # „kliknij, aby podmienić", a bez adresu klik otwierał wybór pliku
                    # i po cichu go wyrzucał.
                    cell["upload_url"] = reverse(_ART_LEVELS[key], args=[owner_pk])
                    # Model 3D (.glb) — tylko poziomy z polem glb_model (karton/produkt).
                    if key == "carton":
                        cell["glb_url"] = reverse("ui:carton_glb_upload", args=[owner_pk])
                    elif key == "unit":
                        cell["glb_url"] = reverse("ui:product_glb_upload", args=[owner_pk])
            cells.append(cell)
        # `has_gap` = wiersz ma choć jeden poziom z możliwą grafiką bez renderu (todo) —
        # napędza badge „braki" i filtr „tylko braki" w panelu.
        has_gap = any(c["state"] == "todo" for c in cells)
        rows.append({"product": r["product"], "cells": cells, "has_gap": has_gap})

    done = sum(1 for r in rows for c in r["cells"] if c["state"] == "done")
    todo = sum(1 for r in rows for c in r["cells"] if c["state"] == "todo")
    gap_rows = sum(1 for r in rows if r["has_gap"])
    summary = {"done": done, "todo": todo, "gap_rows": gap_rows}

    return render(request, "ui/data_center/matrix.html", {
        "columns": columns, "rows": rows, "page": page, "q": q, "summary": summary,
        "active_tab": "artwork"})


# Poziomy z możliwą grafiką → (model grafiki, nazwa pola ownera). Słowa-klucze z nazwy
# pliku (REF_poziom_rewizja): karton / opz / sztuka.
_BULK_LEVELS = {
    "karton": (CartonArtwork, "carton"),
    "opz":    (InnerPackArtwork, "inner_pack"),
    "sztuka": (ProductArtwork, "product"),
}


def _bulk_owner(level, product, instr):
    """Obiekt-właściciel grafiki dla danego poziomu (albo None, gdy indeks go nie ma)."""
    carton = instr.carton if instr else None
    if level == "karton":
        return carton
    if level == "opz":
        return (instr.inner_pack if instr else None) or (carton.inner_pack if carton else None)
    if level == "sztuka":
        return product
    return None


@require_POST
@_md_role
def packaging_matrix_bulk_upload(request):
    """Masowy upload grafik: wiele plików naraz. Nazwa pliku = REF_poziom[_rewizja]
    (np. DMOM10001_karton_R2 albo DMOM10001_karton). Poziom ∈ {karton, opz, sztuka}.
    REF rozwiązywany przez resolver kodów (aliasy + normalizacja), więc warianty zapisu
    też trafiają. Rewizja (jeśli jest) ląduje w nazwie grafiki. Raport per plik."""
    from .. import product_codes
    files = request.FILES.getlist("files")
    if not files:
        messages.error(request, "Nie wybrano plików.")
        return redirect("ui:packaging_matrix")
    ok, skipped = 0, []
    for f in files:
        stem = (f.name or "").rsplit(".", 1)[0]
        parts = stem.split("_")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            skipped.append(f"{f.name}: nazwa musi mieć format REF_poziom[_rewizja]")
            continue
        ref, level = parts[0], parts[1].lower()
        revision = parts[2] if len(parts) > 2 else ""
        if level not in _BULK_LEVELS:
            skipped.append(f"{f.name}: nieznany poziom '{level}' (dozwolone: karton/opz/sztuka)")
            continue
        if f.size > _ART_MAX_BYTES:
            skipped.append(f"{f.name}: plik za duży (max 8 MB)")
            continue
        if f.content_type not in _ART_CONTENT_TYPES:
            skipped.append(f"{f.name}: dozwolone tylko PNG/JPG")
            continue
        product = product_codes.resolve_product_code(ref)
        if not product:
            skipped.append(f"{f.name}: nie znaleziono indeksu „{ref}”")
            continue
        art_model, owner_field = _BULK_LEVELS[level]
        owner = _bulk_owner(level, product, product.latest_instruction())
        if owner is None:
            skipped.append(f"{f.name}: indeks {product.code} nie ma poziomu „{level}”")
            continue
        # print = jeden nadruk na ścianę frontową; podmień istniejący (jak single-upload).
        owner.artworks.filter(face="front", kind="print").delete()
        art = art_model(face="front", kind="print", x_pct=0, y_pct=0, w_pct=100, h_pct=100,
                        z=0, name=(revision or "")[:120], **{owner_field: owner})
        art.image = f
        art.save()
        ok += 1
    if ok:
        messages.success(request, f"Wgrano {ok} grafik.")
    for s in skipped:
        messages.warning(request, s)
    if not ok and not skipped:
        messages.info(request, "Nic nie wgrano.")
    return redirect("ui:packaging_matrix")


@_md_role
def data_center_packaging(request):
    """Quick inline edit of packaging sizes — one table, bulk-save changed rows
    (L×W×H, szt/karton, waga jedn., tara) without opening each carton separately."""
    if request.method == "POST":
        changed = 0
        for c in Carton.objects.filter(is_active=True):
            p = f"c{c.pk}_"
            try:
                L, W, H = (int(request.POST[p + "l"]), int(request.POST[p + "w"]), int(request.POST[p + "h"]))
                ppc = int(request.POST[p + "ppc"])
                uw = float((request.POST[p + "uw"]).replace(",", "."))
                tare = float((request.POST.get(p + "tare", "0") or "0").replace(",", "."))
            except (KeyError, ValueError):
                continue
            # ppc to IntegerField (int4) — bez górnego limitu ogromna wartość (paste/fat-finger)
            # wywala Postgres "integer out of range" 500 na save(). Odrzuć jak inne błędne wiersze.
            # uw musi być > 0 (CartonVariant.validate() wymaga wagi), tare nieujemne —
            # inaczej zapiszemy karton, który wywala kalkulator/„review" flaguje jako błędny.
            if L <= 0 or W <= 0 or H <= 0 or ppc <= 0 or ppc > 1_000_000 or uw <= 0 or tare < 0:
                continue
            cur = (c.length_cm, c.width_cm, c.height_cm, c.pieces_per_carton, c.unit_weight_kg, c.tare_kg)
            if cur != (L, W, H, ppc, uw, tare):
                c.length_cm, c.width_cm, c.height_cm = L, W, H
                c.pieces_per_carton, c.unit_weight_kg, c.tare_kg = ppc, uw, tare
                c.save(update_fields=["length_cm", "width_cm", "height_cm",
                                      "pieces_per_carton", "unit_weight_kg", "tare_kg"])
                changed += 1
        if changed:
            # Changing packaging dimensions changes HU volumes → re-run the stock
            # discrepancy engine so over-capacity/no-data tasks reflect the new data.
            from ..notifications import run_stock_discrepancy_checks
            new_disc = run_stock_discrepancy_checks(created_by=request.user)
            extra = f" Wykryto {new_disc} nowych niezgodności stocku." if new_disc else ""
            messages.success(request, f"Zapisano zmiany w {changed} opakowaniach.{extra}")
            # Instrukcje paletyzacji trzymają WŁASNY snapshot wymiarów kartonu — zmiana tutaj
            # ich nie przelicza. Ostrzeż, że karta paletyzacji może być nieświeża do ręcznego
            # utworzenia nowej wersji instrukcji.
            messages.warning(request, "Uwaga: instrukcje paletyzacji korzystające z tych "
                             "kartonów NIE zostały automatycznie przeliczone — utwórz nową "
                             "wersję instrukcji, aby karta paletyzacji uwzględniła nowe wymiary.")
        else:
            messages.success(request, "Brak zmian do zapisania.")
        return redirect("ui:data_center_packaging")

    cartons = Carton.objects.filter(is_active=True).order_by("name")
    return render(request, "ui/data_center_packaging.html",
                  {"cartons": cartons, "active_tab": "cartons"})


@_md_role
def data_center_review(request):
    """'Do uzupełnienia' — master data gaps that block calculations, with edit links:
    products without an active palletization instruction / without EAN, and cartons
    missing dimensions or weight."""
    products = list(Product.objects.filter(is_active=True).order_by("code")
                    .prefetch_related("instructions"))
    no_instr = [p for p in products if p.latest_instruction() is None]
    no_ean = [p for p in products if not (p.ean or "").strip()]
    bad_cartons = list(Carton.objects.filter(is_active=True).filter(
        Q(length_cm__lte=0) | Q(width_cm__lte=0) | Q(height_cm__lte=0) | Q(unit_weight_kg__lte=0)
    ).order_by("name"))
    # Duplicate EANs — code is DB-unique but EAN is not, so two indexes can share a
    # barcode (a real scanning/identification hazard). Group products by non-empty EAN.
    by_ean = {}
    for p in products:
        ean = (p.ean or "").strip()
        if ean:
            by_ean.setdefault(ean, []).append(p)
    dup_ean = [{"ean": ean, "products": ps} for ean, ps in sorted(by_ean.items())
               if len(ps) > 1]

    # Validate against SAP MARM reference (only when it's been imported): products whose
    # code is absent from MaterialReference are not backed by SAP master data.
    marm_codes = set(MaterialReference.objects.values_list("code", flat=True))
    not_in_marm = [p for p in products if p.code not in marm_codes] if marm_codes else []

    groups = [
        {"key": "no_instr", "label": "Produkty bez instrukcji paletyzacji",
         "hint": "Nie da się policzyć palet/objętości — dodaj instrukcję.",
         "url": "ui:planner_product_edit", "items": no_instr, "kind": "product"},
        {"key": "no_ean", "label": "Produkty bez EAN",
         "hint": "Brak kodu kreskowego utrudnia skanowanie/identyfikację.",
         "url": "ui:planner_product_edit", "items": no_ean, "kind": "product"},
        {"key": "bad_cartons", "label": "Opakowania bez wymiarów / wagi",
         "hint": "Brak L/W/H lub wagi — popraw w szybkiej edycji opakowań.",
         "url": "ui:planner_carton_edit", "items": bad_cartons, "kind": "carton"},
        {"key": "not_in_marm", "label": "Produkty spoza SAP MARM",
         "hint": "Kod indeksu nie występuje w referencji SAP MARM — zweryfikuj poprawność.",
         "url": "ui:planner_product_edit", "items": not_in_marm, "kind": "product"},
    ]
    return render(request, "ui/data_center_review.html", {
        "groups": groups, "dup_ean": dup_ean,
        "ok": not (no_instr or no_ean or bad_cartons or dup_ean or not_in_marm)})

__all__ = [
    'data_center',
    'data_center_dictionary',
    '_completeness_metrics',
    '_matrix_owner_pk',
    '_matrix_artwork_owners',
    'packaging_matrix',
    '_bulk_owner',
    'packaging_matrix_bulk_upload',
    'data_center_packaging',
    'data_center_review',
]
