"""ZARIA RAG-lite (roadmapa, Fala 5): odpowiedzi wzbogacane danymi GROOVE — TYLKO odczyt.

Bez embeddingów i bez wywołań narzędziowych (działa z każdym dostawcą, także Ollama):
z pytania użytkownika wyciągamy kandydatów na kody (SKU / pickHU / lokalizacja) i nazwy
klientów, dociągamy pasujące wiersze z bazy i sklejamy krótki polski blok kontekstu,
który czat dokleja do promptu systemowego. Model dostaje fakty zamiast zgadywać.

ponytail: dopasowanie regex+icontains zamiast wyszukiwania semantycznego — podnieść
do embeddingów, gdy realne pytania pokażą, że słowa kluczowe nie wystarczają.
"""
import re

from django.db.models import Q

# Kody typu ZR-1001, HU123456, B0-01-100A — litera(y) + cyfry z separatorami.
CODE_RE = re.compile(r"\b[A-Za-z]{1,6}[-_/]?\d[\w\-/]{1,30}\b")
MAX_CODES = 8      # ile kandydatów z pytania bierzemy pod uwagę
MAX_ROWS = 5       # ile wierszy na sekcję trafia do kontekstu
MAX_WORDS = 5      # ile słów-kandydatów na nazwę klienta


def _codes(text):
    """Unikalne kandydackie kody z pytania, w kolejności wystąpienia."""
    return list(dict.fromkeys(m.group(0) for m in CODE_RE.finditer(text)))[:MAX_CODES]


def _name_words(text):
    """Słowa mogące być nazwą klienta (≥4 litery, bez cyfr) — do icontains."""
    words = re.findall(r"[A-Za-zÀ-žąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{4,}", text)
    return list(dict.fromkeys(words))[:MAX_WORDS]


def _products(codes):
    from .models import Product
    if not codes:
        return []
    q = Q()
    for c in codes:
        q |= Q(code__iexact=c) | Q(ean__iexact=c)
    out = []
    for p in Product.objects.filter(q, is_active=True).select_related("category")[:MAX_ROWS]:
        dims = ""
        if p.unit_length_cm and p.unit_width_cm and p.unit_height_cm:
            dims = f", wymiary szt. {p.unit_length_cm}×{p.unit_width_cm}×{p.unit_height_cm} cm"
        cat = f", kategoria: {p.category.name}" if p.category else ""
        stack = "" if p.stackable else ", NIE piętrować"
        out.append(f"Produkt {p.code}: {p.name}{cat}{dims}{stack}")
    return out


def _product_locations(codes):
    """Gdzie leży dany indeks: lokalizacje palet (HU) zawierających ten kod."""
    from .models import HandlingUnitItem
    if not codes:
        return []
    q = Q()
    for c in codes:
        q |= Q(ref_code__iexact=c) | Q(product__code__iexact=c)
    rows = (HandlingUnitItem.objects.filter(q)
            .exclude(hu__location="").select_related("hu")
            .order_by("-hu__created_at")[:MAX_ROWS])
    return [f"Indeks {i.ref_code}: paleta {i.hu.code or f'#{i.hu_id}'} "
            f"w lokalizacji {i.hu.location} (status: {i.hu.get_status_display()}, "
            f"{i.expected_qty:g} {i.unit})" for i in rows]


def _handling_units(codes):
    from .models import HandlingUnit
    if not codes:
        return []
    hus = (HandlingUnit.objects.filter(code__in=codes)
           .select_related("shipment").prefetch_related("items")[:MAX_ROWS])
    out = []
    for h in hus:
        n = h.items.count()
        loc = f", lokalizacja {h.location}" if h.location else ""
        out.append(f"Paleta (HU) {h.code}: status {h.get_status_display()}{loc}, "
                   f"{n} pozycji, wysyłka #{h.shipment_id}")
    return out


def _locations(codes):
    """Stan lokalizacji z NAJNOWSZEGO snapshotu magazynu."""
    from .models import WarehouseSnapshot, WarehouseSnapshotRow
    if not codes:
        return []
    snap = WarehouseSnapshot.objects.first()   # ordering: -uploaded_at
    if not snap:
        return []
    rows = WarehouseSnapshotRow.objects.filter(
        snapshot=snap, location_code__in=[c.upper() for c in codes])[:MAX_ROWS]
    out = []
    for r in rows:
        state = "wolna" if r.is_empty else "zajęta"
        blocks = []
        if r.blocked_pick:
            blocks.append("blokada pobrania")
        if r.blocked_put:
            blocks.append("blokada wstawienia")
        extra = f" ({', '.join(blocks)})" if blocks else ""
        out.append(f"Lokalizacja {r.location_code}: {state}{extra}, "
                   f"typ {r.warehouse_type or '—'} (snapshot: {snap.uploaded_at:%d.%m.%Y})")
    return out


def _customers(codes, words):
    from .models import Customer
    q = Q()
    for c in codes:
        q |= Q(code__iexact=c)
    for w in words:
        q |= Q(name__icontains=w)
    if not q:
        return []
    out = []
    for c in Customer.objects.filter(q)[:MAX_ROWS]:
        reqs = []
        if c.max_pallet_height_cm:
            reqs.append(f"maks. wys. palety {c.max_pallet_height_cm} cm")
        if c.max_pallet_weight_kg:
            reqs.append(f"maks. waga palety {c.max_pallet_weight_kg} kg")
        if c.requires_fumigated_pallet:
            reqs.append("paleta fumigowana (IPPC)")
        if c.requires_adr:
            reqs.append("ADR")
        if c.temp_control:
            reqs.append(f"temperatura {c.temp_control}")
        if c.min_shelf_life_months:
            reqs.append(f"min. ważność {c.min_shelf_life_months} mies.")
        if c.delivery_hours:
            reqs.append(f"okna dostaw: {c.delivery_hours}")
        addr = ", ".join(x for x in [c.city, c.country] if x)
        out.append(f"Klient {c.name}" + (f" ({addr})" if addr else "")
                   + (": " + "; ".join(reqs) if reqs else ": brak specjalnych wymagań"))
    return out


def _product_history(codes):
    """Historia zmian kartoteki produktu (django-simple-history) — co i kiedy się
    zmieniło w master dacie. Model dostaje 'REF X zmieniano wtedy to a to'."""
    from .models import Product
    if not codes:
        return []
    TYPE = {"+": "utworzenie", "~": "edycja", "-": "usunięcie"}
    out = []
    for c in codes:
        recs = list(Product.history.filter(code__iexact=c).order_by("-history_date")[:2])
        if not recs:
            continue
        last = recs[0]
        changed = ""
        if len(recs) == 2:                      # co się zmieniło w ostatniej rewizji
            fields = [ch.field for ch in last.diff_against(recs[1]).changes][:6]
            if fields:
                changed = " — pola: " + ", ".join(fields)
        n = Product.history.filter(code__iexact=c).count()
        out.append(f"Historia {c}: {n} wpisów, ostatnia zmiana "
                   f"{last.history_date:%d.%m.%Y} ({TYPE.get(last.history_type, '?')})"
                   f"{changed}")
    return out[:MAX_ROWS]


def _recurring_issues(codes):
    """Powtarzające się błędy jakościowe z kontroli HU dla danych REF — agregat po
    typie (np. '7× uszkodzony towar'). Źródło wzorców, nie pojedynczych zdarzeń."""
    from django.db.models import Count
    from .models import HUQualityIssue
    if not codes:
        return []
    labels = dict(HUQualityIssue.TYPES)
    out = []
    for c in codes:
        rows = (HUQualityIssue.objects
                .filter(Q(ref_code__iexact=c) | Q(item__ref_code__iexact=c)
                        | Q(item__product__code__iexact=c))
                .values("issue_type").annotate(n=Count("id")).order_by("-n"))
        parts = [f"{r['n']}× {labels.get(r['issue_type'], r['issue_type'])}"
                 for r in rows[:4]]
        if parts:
            out.append(f"Powtarzające się błędy REF {c}: " + "; ".join(parts))
    return out[:MAX_ROWS]


def _instructions(codes):
    """Aktywna instrukcja paletyzacji dla REF — kartony/warstwy/wysokość + uwagi
    dla magazyniera. Model odpowiada 'jak paletyzować X' faktami, nie zgadując."""
    from .models import PalletizationInstruction
    if not codes:
        return []
    out = []
    for c in codes:
        instr = (PalletizationInstruction.objects
                 .filter(product__code__iexact=c, is_active=True)
                 .order_by("-version").first())
        if not instr:
            continue
        layout = instr.get_selected_layout() or {}
        cpl = layout.get("cartons_per_pallet")
        layers = layout.get("layers_used")
        bits = [f"karton {instr.carton_l}×{instr.carton_w}×{instr.carton_h} cm"]
        if cpl and layers:
            bits.append(f"{cpl} kart./paletę w {layers} warstwach")
        bits.append(f"maks. wys. {instr.max_height_total_cm} cm")
        note = f" — uwagi: {instr.notes[:120]}" if instr.notes else ""
        out.append(f"Instrukcja {c} (v{instr.version}): " + ", ".join(bits) + note)
    return out[:MAX_ROWS]


# Źródło RAG → moduły, których widoki pokazują te dane (te same reguły dostępu co UI,
# can_open_module = rola + nadpisania per użytkownik). Brak dostępu do żadnego → pomijamy.
SOURCE_MODULES = {
    "products": ("data_center", "paletyzacja", "phv"),
    "locations": ("magazyn", "data_center", "phv"),
    "hu": ("kontrola_hu", "transport", "magazyn"),
    "quality": ("kontrola_hu",),
    "customers": ("klienci", "data_center"),
}


def _allowed_sources(user):
    """Zbiór kluczy SOURCE_MODULES dostępnych dla użytkownika (brak usera → pusty)."""
    from core.platform_modules import can_open_module, _module_overrides
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    ov = {} if user.is_superuser else _module_overrides(user)
    return {src for src, keys in SOURCE_MODULES.items()
            if any(can_open_module(user, k, ov, ignore_variant=True) for k in keys)}


def context_for(message, user=None):
    """Polski blok kontekstu do doklejenia do promptu systemowego, albo "" gdy
    w pytaniu nie rozpoznano nic, co ma pokrycie w danych GROOVE dostępnych dla
    `user` (bez usera → nic: fail-closed)."""
    allowed = _allowed_sources(user)
    if not allowed:
        return ""
    codes = _codes(message)
    words = _name_words(message)
    sections = []
    if "products" in allowed:
        sections += _products(codes) + _product_history(codes) + _instructions(codes)
    if "quality" in allowed:
        sections += _recurring_issues(codes)
    if "hu" in allowed:
        sections += _product_locations(codes) + _handling_units(codes)
    if "locations" in allowed:
        sections += _locations(codes)
    if "customers" in allowed:
        sections += _customers(codes, words)
    if not sections:
        return ""
    return ("DANE GROOVE (aktualne dane firmowe, tylko do odczytu — użyj ich w "
            "odpowiedzi zamiast zgadywać; jeśli czegoś tu nie ma, powiedz, że nie "
            "znalazłaś w systemie):\n- " + "\n- ".join(sections))
