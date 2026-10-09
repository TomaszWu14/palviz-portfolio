"""Moduł „Hierarchia opakowań" (PHV — Packaging Hierarchy Viewer).

Pracownik magazynu wpisuje/skanuje REF (skaner sprzętowy = klawiatura, pole ma autofocus)
i widzi hierarchię opakowań z master daty: sztuka → OPZ → karton → paleta, z ilościami
i wagami na każdym poziomie oraz alertami o brakujących przelicznikach. Z tego samego
ekranu zgłasza błąd master daty (5 typów, foto) — mail idzie na PHV_ISSUE_EMAIL,
status śledzi po #ID / w „Moich zgłoszeniach".

Dane wprost z istniejącej master daty (PalletizationInstruction + Product) — zero nowych
integracji (SAP jest read-only; feed już zasila te modele).
"""
import logging
import re

from .core import (Q)
import datetime as _dt   # po `from .core import *` — inaczej `datetime` = klasa z core (shadow)
from django.db.models import Count
from ..models import (PickerActivity, HandlingUnitItem, MaterialReference)
from ..location_codes import LOCATION_CODE_RE as _LOC_RE  # noqa: F401  # re-eksport dla phv_views

log = logging.getLogger(__name__)

# Kształt kodu lokalizacji — wspólne źródło prawdy (ui/location_codes.py),
# to samo co w imporcie lokalizacji.

# Typy PackagingIssue obsługiwane przez moduł „Optymalizacja kartonów" (skrzynka +
# routing powiadomień). Jedno źródło prawdy — reużywane w carton_opt.py.
# „carton_too_heavy" wycofany z pickera (zastąpiony „change_location" → Master Data).
OPTIMIZATION_ISSUE_TYPES = ("carton_underfilled", "carton_fit_pallet")

# „Brak przelicznika": której jednostki dotyczy brak — wskazywane w zgłoszeniu (wymagane).
MISSING_CONVERSION_UNITS = ("PAZ", "KAR", "OPZ")

# „Zmień lokalizację": uproszczony słownik STREF (B5) — poprzednia lista klas
# strefa+wysokość była za długa do wyboru na skanerze. Dotyczy tylko NOWYCH zgłoszeń;
# historyczne correct_value zostają w starym formacie (decyzja: bez migracji danych).
# Zapisywane w correct_value; routing domyślny → Master Data (slotting).
LOCATION_SUGGESTIONS = [
    ("A", "Strefa A"),
    ("B", "Strefa B"),
    ("C", "Strefa C"),
    ("D", "Strefa D"),
    ("ANTRESOLA", "Antresola"),
]

# Typy wycofane z pickera zgłoszeń (wartość zostaje w modelu dla starych wierszy).
_RETIRED_ISSUE_TYPES = {"carton_too_heavy"}

# „Zmień sposób paletyzacji" (B6/E1): słownik powodów — wymagany przy zgłoszeniu,
# zapisywany w correct_value. Propozycja na bazie istniejących typów zgłoszeń.
CHANGE_PALLETIZATION_REASONS = [
    ("carton_size", "Zmiana rozmiaru kartonu"),
    ("carton_qty", "Zmiana ilości w kartonie"),
    ("pallet_height", "Zmiana wysokości palety (wymóg klienta)"),
    ("underfilled", "Karton niewypełniony"),
    ("too_heavy", "Karton/paleta za ciężka"),
    ("inner_pack", "Zmiana opakowania zbiorczego (OPZ)"),
    ("other", "Inny powód (opisz)"),
]


def _locnorm(x):
    """Kanoniczny klucz lokalizacji (wielkość liter/spacje nieistotne) — HU, fixy i master
    data pochodzą z różnych źródeł, więc porównujemy po znormalizowanej postaci."""
    return (x or "").strip().upper()


def _valid_image(photo):
    """True gdy `photo` to PRAWDZIWY obraz rastrowy do 12 MB (albo brak zdjęcia). NIE ufamy
    `content_type` z nagłówka — klient go podrabia (SVG z JS przechodził jako image/* →
    stored XSS gdy lider otworzy foto). Weryfikujemy magiczne bajty przez Pillow, co odrzuca
    SVG/HTML/skrypty. Kursor pliku wracamy na 0, żeby zapis ImageField działał."""
    if not photo:
        return True                                   # zdjęcie jest opcjonalne
    if photo.size > 12 * 1024 * 1024:
        return False
    try:
        from PIL import Image
        photo.seek(0)
        Image.open(photo).verify()                    # rzuca dla nie-obrazu (SVG/HTML/…)
        photo.seek(0)
        return True
    except Exception:
        return False


def _hierarchy(product):
    """Hierarchia z WSPÓLNEGO serwisu (ui/hierarchy.py) — te same liczby i te same
    rendery 3D co widok desktopowy. Leniwe przeliczenie layoutu jak na desktopie."""
    from ..hierarchy import build_hierarchy
    instr = product.latest_instruction()
    if instr and not instr.layouts:
        try:
            from .core.packing import _recalculate_instruction
            _recalculate_instruction(instr)
        except Exception:
            log.exception("Leniwe przeliczenie instrukcji paletyzacji nie powiodło się")
    return build_hierarchy(product, instr)


def _issue_stat(product):
    """Podpowiedź logistyczna z reguł (bez AI): w jakiej JM materiał jest NAJCZĘŚCIEJ
    wydawany, z historii pobrań (PickerActivity). Zwraca {unit, pct, total} albo None,
    gdy brak historii (część importów heatmapy nie ma JM — traktujemy jak brak danych)."""
    rows = list(PickerActivity.objects
                .filter(material_code__iexact=product.code).exclude(unit="")
                .values("unit").annotate(n=Count("id")).order_by("-n"))
    if not rows:
        return None
    total = sum(r["n"] for r in rows)
    top = rows[0]
    return {"unit": top["unit"], "pct": round(100 * top["n"] / total), "total": total}


# Procesy magazynowe (typ magazynu w SAP → nazwa procesu pickingowego). Indeks bez
# stocku w żadnym z nich nie ma nadanego procesu → skaner proponuje zgłoszenie.
PICK_PROCESSES = [
    ("0050", "Fix", "Stała lokalizacja pickingowa"),
    ("0052", "Near to bin", "Zapas tuż przy lokalizacji pickingowej"),
    ("0070", "Antresola", "Picking z antresoli"),
]
PROCESS_CHOICES = [(code, f"{code} — {name}") for code, name, _d in PICK_PROCESSES]

# Agregacja typów magazynu (SAP) do filtrów panelu „Dane magazynowe".
# Kolejność = kolejność chipów. „inne" to kubeł domyślny.
WAREHOUSE_CATEGORIES = [
    ("wydawcze", "Wydawcze"),
    ("zapasowe", "Zapasowe"),
    ("dlt",      "DLT"),
    ("wysylka",  "Wysyłka"),
    ("inne",     "Inne"),
]
_CAT_BY_TYPE = {}
for _c in ("0050", "0051", "0052", "0070"):
    _CAT_BY_TYPE[_c] = "wydawcze"
for _c in ("0010", "0011"):
    _CAT_BY_TYPE[_c] = "zapasowe"
for _c in ("92EX", "92GE", "92GL", "92JU", "92T3", "94GL", "WCEX", "WCGE", "WCGL"):
    _CAT_BY_TYPE[_c] = "wysylka"
# DLT = magazyn zewnętrzny; kody do potwierdzenia (na razie puste → trafiają do „Inne").
# ponytail: uzupełnić zestaw, gdy znane kody typów DLT.
_DLT_TYPES = set()

# Sentinel do sortowania FEFO: palety bez daty ważności lądują na końcu listy.
_DATE_MAX = _dt.date(9999, 12, 31)

# Status zapasu SAP → rodzaj dla drill-downu. Kod w HandlingUnit.stock_status to 1. kolumna
# raportu SAP (np. B6, Q4, C6); ostatnia kolumna raportu grupuje: BB=zablokowane,
# QQ=kontrola jakości, RR=zwroty (zablokowane), FF=wolne. Nieznane/puste → wolne.
_STOCK_STATUS_KIND = {}
for _c in ("AB", "B5", "B6", "BB", "C5", "C6", "P6", "S5", "S6"):
    _STOCK_STATUS_KIND[_c] = "blocked"          # BB — zapasy zablokowane
for _c in ("AQ", "BQ", "C3", "C4", "P4", "Q3", "Q4"):
    _STOCK_STATUS_KIND[_c] = "quality"          # QQ — kontrola jakości (niepobieralne)
for _c in ("C7", "C8", "R7", "R8"):
    _STOCK_STATUS_KIND[_c] = "returns"          # RR — zablokowany zapas zwrotów


def _stock_status_kind(code):
    """Rodzaj statusu zapasu (blocked/quality/returns/free) wg słownika SAP."""
    return _STOCK_STATUS_KIND.get((code or "").strip().upper(), "free")


def _wh_category(wt):
    """Typ magazynu (SAP) → kubeł filtra (Wydawcze/Zapasowe/DLT/Wysyłka/Inne)."""
    wt = (wt or "").strip().upper()
    if wt in _DLT_TYPES:
        return "dlt"
    return _CAT_BY_TYPE.get(wt, "inne")


def _wh_bucket(wt, location):
    """(kod_grupy, kategoria) dla wiersza stocku. Typ 9010 rozdziela się po LOKALIZACJI:
    9010 z lokalizacją zawierającą „DLT" → magazyn zewnętrzny DLT; pozostałe 9010 →
    GR-Zone (strefa przyjęć) → Inne. Reszta typów: kubeł wg samego kodu."""
    wt = (wt or "").strip().upper()
    if wt == "9010":
        # „DLT" jako CAŁY segment kodu (word-boundary), nie luźny substring — inaczej
        # „SPLIT…"/„LITER…" wpadłyby do DLT. Łapie „DLT", „DLT-01", „05.DLT" itp.
        if re.search(r"\bDLT\b", (location or "").upper()):
            return ("9010 (DLT)", "dlt")
        return ("9010 (GR-Zone)", "inne")
    return (wt, _wh_category(wt))


def _hu_volume_m3(hu):
    """Objętość palety z wymiarów feedu (cm→m³) albo None, gdy brak wymiarów."""
    if hu.length_cm and hu.width_cm and hu.height_cm:
        return round(hu.length_cm * hu.width_cm * hu.height_cm / 1_000_000, 3)
    return None


def _fefo_update(hu_fefo, it):
    """HU może mieć wiele lotów → FEFO bierze NAJKRÓTSZĄ ważność (i jej LOT)."""
    if it.expiry is None:
        return
    cur = hu_fefo.get(it.hu_id)
    if cur is None or it.expiry < cur["expiry"]:
        hu_fefo[it.hu_id] = {"expiry": it.expiry, "lot": (it.lot or "").strip()}


def _collect_stock(product):
    """Unikalne HU zawierające ten indeks (dane palety) + sumy bazowej ilości.
    base_qty = suma expected_qty pozycji (jedn. podstawowa, np. szt/OP; import zapisuje
    ją w tej jednostce), żeby obok liczby palet pokazać ile sztuk łącznie.
    total = cały stock on-hand (proces + kubły); blocked = z tego niepobieralne."""
    st = {"hus": {}, "qty_by_loc": {}, "qty_by_bucket": {}, "hu_fefo": {}, "total": 0.0, "blocked": 0.0}
    for it in (HandlingUnitItem.objects
               .filter(Q(product=product) | Q(ref_code__iexact=product.code),
                       hu__shipment__is_stock=True)
               .select_related("hu")):
        st["hus"][it.hu_id] = it.hu
        q = it.expected_qty or 0
        st["total"] += q
        if _stock_status_kind(it.hu.stock_status) != "free":
            st["blocked"] += q
        loc = _locnorm(it.hu.location)                # klucz znormalizowany (case/spacje)
        st["qty_by_loc"][loc] = st["qty_by_loc"].get(loc, 0) + q
        gc, _cat = _wh_bucket(it.hu.warehouse_type, it.hu.location)
        st["qty_by_bucket"][gc] = st["qty_by_bucket"].get(gc, 0) + q
        _fefo_update(st["hu_fefo"], it)
    return st


def _process_rows(hus, qty_by_loc):
    """Procesy pickingowe (Fix/Near/Antresola) z listą lokalizacji (max 12) i ilością."""
    by_type = {}
    for hu in hus.values():
        by_type.setdefault((hu.warehouse_type or "").strip(), []).append(hu)
    processes = []
    for code, name, desc in PICK_PROCESSES:
        if not by_type.get(code):
            continue
        # Grupowanie po kluczu _locnorm (ten sam co w qty_by_loc) — warianty pisowni to jedna
        # lokalizacja (bez podwójnego liczenia); HU bez lokalizacji → klucz "" / etykieta „—".
        locs = sorted({_locnorm(h.location) for h in by_type[code]}, key=lambda k: (k == "", k))[:12]
        loc_rows = [{"code": loc or "—", "qty": round(qty_by_loc.get(loc, 0)) or None} for loc in locs]
        processes.append({"code": code, "name": name, "desc": desc, "locations": loc_rows})
    return processes


def _bucket_hus(hus):
    """Kubły stocku (poza procesami pickingowymi); 9010 rozdzielone: DLT vs GR-Zone (po lokalizacji)."""
    known = {c for c, _n, _d in PICK_PROCESSES}
    by_bucket = {}
    for hu in hus.values():
        wt = (hu.warehouse_type or "").strip()
        if not wt or wt in known:
            continue
        gc, cat = _wh_bucket(wt, hu.location)
        by_bucket.setdefault(gc, {"hus": [], "cat": cat})["hus"].append(hu)
    return by_bucket


def _blocked_locations(hus):
    """Lokalizacje zablokowane (wydanie/umieszczanie) z aktywnej master daty — do znacznika
    „zablokowana" w drill-downie. Jedno zapytanie po kodach lokalizacji tego indeksu.
    Zwraca klucze _locnorm — porównanie bez względu na wielkość liter/spacje po obu stronach."""
    from django.db.models.functions import Trim, Upper
    from ..models import WarehouseLocationMaster
    all_locs = {_locnorm(h.location) for h in hus.values()} - {""}
    if not all_locs:
        return set()
    return {_locnorm(c) for c in WarehouseLocationMaster.objects
            .annotate(_loc=Upper(Trim("location_code")))
            .filter(batch__is_active=True, _loc__in=all_locs)
            .filter(Q(blocked_pick=True) | Q(blocked_put=True))
            .values_list("location_code", flat=True)}


def _group_row(h, hu_fefo, blocked_locs):
    """Wiersz drill-downu: lokalizacja, objętość m³, nr HU, status zapasu, FEFO, blokada."""
    fefo = hu_fefo.get(h.id) or {}
    return {"location": (h.location or "—").strip(), "hu_code": (h.code or "—"),
            "volume_m3": _hu_volume_m3(h), "status": (h.stock_status or "").strip(),
            "status_kind": _stock_status_kind(h.stock_status),
            "expiry": fefo.get("expiry"), "lot": fefo.get("lot", ""),
            "blocked": _locnorm(h.location) in blocked_locs}


def _stock_group(gc, bucket, st, blocked_locs):
    """Kubeł stocku: liczba palet (HU), suma ilości/objętości i drill-down FEFO (cap 60)."""
    hu_list = bucket["hus"]
    rows = [_group_row(h, st["hu_fefo"], blocked_locs) for h in hu_list]
    # FEFO: najkrótsza ważność u góry; palety bez daty na koniec.
    rows.sort(key=lambda r: (r["expiry"] is None, r["expiry"] or _DATE_MAX))
    # Suma objętości liczona po WSZYSTKICH paletach kubła (nie po przyciętych 60).
    vols = [_hu_volume_m3(h) for h in hu_list]
    total_vol = round(sum(v for v in vols if v), 2) if any(vols) else None
    # ponytail: drill-down cap 60 palet/kubeł — wystarczy do orientacji, chroni render.
    return {"code": gc, "count": len(hu_list), "category": bucket["cat"], "total_volume_m3": total_vol,
            "base_qty": round(st["qty_by_bucket"].get(gc, 0)) or None, "rows": rows[:60]}


def _fix_row(fx, qty_by_loc):
    """below_min → poniżej minimum (do uzupełnienia); minimum=0 = brak progu."""
    cur = round(qty_by_loc.get(_locnorm(fx.location_code), 0))   # dopasowanie bez względu na case
    mn = round(fx.min_qty) if fx.min_qty else 0
    return {"location": fx.location_code, "wh": fx.warehouse_type or "0050",
            "min_qty": mn, "max_qty": round(fx.max_qty) if fx.max_qty else 0,
            "uom": fx.uom or "szt", "current": cur, "below_min": bool(mn and cur < mn)}


def _fix_rows(product, qty_by_loc):
    """Fixy (stałe lokalizacje pickingowe) z master daty SAP: bieżący zapas / minimum."""
    from ..models import FixLocation
    fixes = [_fix_row(fx, qty_by_loc) for fx in FixLocation.objects.filter(ref_code__iexact=product.code)]
    fixes.sort(key=lambda f: (not f["below_min"], f["location"]))  # braki na górze
    return fixes


def _stock_total(st):
    """Suma zbiorcza CAŁEGO stocku on-hand (proces pickingowy + kubły), zawsze gdy jest
    jakikolwiek zapas — inaczej headline gubił sztuki z Fixa albo znikał przy 1 kuble.
    `blocked_qty` = z tego niepobieralne (blocked/quality/returns) — sygnał dla operatora."""
    if not st["hus"]:
        return None
    return {"count": len(st["hus"]), "base_qty": round(st["total"]) or None,
            "blocked_qty": round(st["blocked"]) or None}


def _storage_strategy(product):
    """Dane magazynowe indeksu TERAZ z aktualnego stocku (HU z SAP/PowerBI), bez nowych
    tabel. Zwraca {processes, stock_groups, other_types, has_process}:
      • processes    — procesy pickingowe (Fix/Near/Antresola) z listą lokalizacji,
      • stock_groups — pozostałe typy magazynu z LICZBĄ palet (HU) i drill-downem
                       (lokalizacja, objętość m³, nr HU, status zapasu),
      • other_types  — same kody typów (do komunikatu „brak procesu"),
      • has_process  — False → skaner proponuje zgłoszenie nadania procesu.
    """
    st = _collect_stock(product)
    processes = _process_rows(st["hus"], st["qty_by_loc"])
    blocked_locs = _blocked_locations(st["hus"])
    stock_groups = [_stock_group(gc, b, st, blocked_locs) for gc, b in _bucket_hus(st["hus"]).items()]
    # Wydawcze zawsze na górze, potem najwięcej palet.
    stock_groups.sort(key=lambda g: (g["category"] != "wydawcze", -g["count"]))
    # Kubły filtra obecne w stocku (do chipów) — w stałej kolejności WAREHOUSE_CATEGORIES.
    present = {g["category"] for g in stock_groups}
    stock_categories = [{"key": k, "label": lbl} for k, lbl in WAREHOUSE_CATEGORIES if k in present]
    fixes = _fix_rows(product, st["qty_by_loc"])
    return {"processes": processes, "fixes": fixes, "stock_groups": stock_groups,
            "stock_categories": stock_categories, "stock_total": _stock_total(st),
            "other_types": [g["code"] for g in stock_groups],
            "has_process": bool(processes) or bool(fixes)}


def _location_card(code):
    """Karta lokalizacji dla skanera z ISTNIEJĄCYCH danych: master (wymiary/objętość/
    waga/typ/blokady) z aktywnej partii, strefa z prefiksu kodu (brak pola → wyliczamy),
    zawartość = stock-HU w tej lokalizacji (to samo zapytanie co panel mapy)."""
    from wh3d.locations import active_master_qs
    row = active_master_qs().filter(location_code__iexact=code).first()
    items = (HandlingUnitItem.objects
             .filter(hu__shipment__is_stock=True, hu__location__iexact=code)
             .select_related("hu", "product").order_by("hu__seq", "id")[:200])
    contents = [{
        "hu_ref": it.hu.ref,
        "code": it.product.code if it.product else it.ref_code,
        "name": (it.product.name if it.product else it.description or "")[:80],
        "lot": it.lot, "qty": it.alt_qty or it.expected_qty or 0,
        "unit": it.alt_unit or it.unit, "status": it.hu.get_status_display(),
    } for it in items]
    zone = code.split("-")[0].upper() if "-" in code else ""

    # BLOK C: widok produktów w formacie MATINFO — per DISTINCT produkt mini-karta
    # z przelicznikami AJM z TEGO SAMEGO serwisu co karta materiału (hierarchy.
    # unit_factors — zero duplikacji logiki) + grafika kartonu (CartonArtwork) i link
    # do pełnej karty. ponytail: pełne rendery 3D tylko na karcie materiału (koszt
    # silnika per produkt); tu grafika + liczby. Cap 30 produktów z jawnym komunikatem.
    from ..hierarchy import unit_factors
    LOC_PRODUCTS_CAP = 30
    seen_products, products_info, skipped = set(), [], 0
    for it in items:
        p = it.product
        if not p or p.pk in seen_products:
            continue
        seen_products.add(p.pk)
        if len(products_info) >= LOC_PRODUCTS_CAP:
            skipped += 1
            continue
        instr = p.latest_instruction()
        f = unit_factors(instr)
        art = None
        if instr and instr.carton_id:
            first_art = instr.carton.artworks.first()
            art = first_art.image.url if (first_art and first_art.image) else None
        products_info.append({
            "product": p, "factors": f, "artwork_url": art,
            "ju_image_url": p.ju_image.url if p.ju_image else None,
        })
    return {"code": code, "zone": zone, "master": row, "contents": contents,
            "in_master": row is not None,
            "products": products_info, "products_skipped": skipped}


def _material_desc(product):
    """Pełny opis materiału do nagłówka. Autorytatywne jest master data (MARA/MAKTX =
    MaterialReference.name, np. „Przyrząd do infuzji…"); nazwa produktu to fallback, gdy
    brak referencji. Wspólne dla ekranu i asystenta AI."""
    code = product.code.strip().lower()
    mref = MaterialReference.objects.filter(code__iexact=product.code).first()
    mara = (mref.name if mref else "").strip()
    if mara and mara.lower() != code:
        return mara
    name = (product.name or "").strip()
    if name and name.lower() != code:
        return name
    return ""   # nazwa == kod i brak MARA → brak opisu (nagłówek pokaże „zgłoś")


def _assistant_context(product, material_desc, hierarchy, strategy):
    """Zwięzły opis PL wszystkiego, co system wie o REF — kontekst dla lokalnego modelu.
    Tylko dane z master daty + stocku (te same, które widać na ekranie MatInfo)."""
    lines = [f"REF (indeks): {product.code}"]
    if material_desc:
        lines.append(f"Opis materiału: {material_desc}")
    if product.ean:
        lines.append(f"EAN sztuki: {product.ean}")
    lines.append("\nPoziomy opakowań (jednostki):")
    for l in hierarchy["levels"]:
        parts = [f"- {l['title']} ({l['key']}): {l.get('dims', '')}"]
        if l.get("qty"):
            parts.append(f"ilość: {l['qty']}")
        if l.get("ean"):
            parts.append(f"EAN: {l['ean']}")
        if l.get("mult"):
            parts.append(f"×{l['mult']} jednostki niżej")
        lines.append(", ".join(parts))
    s = hierarchy.get("summary") or {}
    if s.get("pcs_per_pallet"):
        lines.append(f"Na palecie: {s['pcs_per_pallet']} {s.get('pallet_unit', 'szt')}, "
                     f"waga ~{s.get('pallet_weight_kg', '?')} kg")
    if hierarchy.get("alerts"):
        lines.append("Braki / alerty danych: " + "; ".join(hierarchy["alerts"]))
    if strategy and strategy.get("processes"):
        lines.append("\nLokalizacje / strategia magazynowa:")
        for p in strategy["processes"]:
            # locations = [{code, qty}] (_process_rows); qty None = brak ilości w stocku
            locs = ", ".join(f"{l['code']} ({l['qty']} szt)" if l.get("qty") else l["code"]
                             for l in p["locations"])
            lines.append(f"- {p['code']} {p['name']}: {locs}")
    elif strategy and not strategy.get("has_process"):
        lines.append("Brak przypisanego procesu pickingowego (brak stocku w procesach).")
    if strategy and strategy.get("stock_groups"):
        lines.append("\nDane magazynowe (stock wg typu magazynu):")
        for g in strategy["stock_groups"]:
            qty = f", {g['base_qty']} szt" if g.get("base_qty") else ""
            vol = f", ~{g['total_volume_m3']} m³" if g.get("total_volume_m3") else ""
            locs = ", ".join(sorted({r["location"] for r in g["rows"]})[:8])
            lines.append(f"- {g['code']}: {g['count']} palet{qty}{vol}; lokalizacje: {locs}")
    return "\n".join(lines)

__all__ = [
    'OPTIMIZATION_ISSUE_TYPES',
    'MISSING_CONVERSION_UNITS',
    'LOCATION_SUGGESTIONS',
    '_RETIRED_ISSUE_TYPES',
    'CHANGE_PALLETIZATION_REASONS',
    '_locnorm',
    '_valid_image',
    '_hierarchy',
    '_issue_stat',
    'PICK_PROCESSES',
    'PROCESS_CHOICES',
    'WAREHOUSE_CATEGORIES',
    '_CAT_BY_TYPE',
    '_DLT_TYPES',
    '_DATE_MAX',
    '_STOCK_STATUS_KIND',
    '_stock_status_kind',
    '_wh_category',
    '_wh_bucket',
    '_hu_volume_m3',
    '_storage_strategy',
    '_location_card',
    '_material_desc',
    '_assistant_context',
]
