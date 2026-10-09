"""Czyste parsowanie wsadu HU (``import_hu_rows``) — bez Django/ORM.

Aliasy nagłówków → indeksy kolumn, odczyt komórki, liczby/daty z feedu SAP/Power BI,
dopasowanie produktu po REF i przeliczenie OP→KAR. Moduł NIE jest star-eksportowany —
importuje go wprost ``huctl.hu_import`` (który dalej wystawia stare nazwy)."""
import re
from datetime import datetime

_HU_ALIASES = [
    # (canonical, [header substrings]) — order = matching priority; one column each.
    # Nowe kolumny feedu (WZ/KUNNR/daty/kompletacja/wagi/wymiary) są OPCJONALNE —
    # stary feed bez nich importuje się bez zmian (wymagane tylko pickhu+ref).
    # Sink: „Typ dokumentu" (feed PowerBI) MUSI stać przed shipment — inaczej alias
    # „dokument" (dla SAP „Dokument"=dostawa) połknąłby „Typ dokumentu". Nieużywane dalej.
    ("doc_type",  ["typ dokument"]),
    ("shipment",  ["dostawa", "delivery", "przesył", "wysyłk", "shipment", "vbeln", "lifex",
                   "dokument"]),
    ("pickhu",    ["jednostka obsługi", "obsług", "pickhu", "sscc", "handling", "nr palety", "paleta"]),
    # KUNNR przed 'ref' i 'recipient' — SAP „Odbiorca materiałów" niesie KOD klienta (nie nazwę);
    # alias 'kod' refa skonsumowałby "kod klienta", a 'odbiorca' recipienta — kolumnę kodu.
    ("kunnr",     ["kunnr", "kod klienta", "nr klienta", "odbiorca materiał"]),
    ("ref",       ["produkt", "product", "materiał", "material", "asortyment", "sku", "matnr", "kod", "ref"]),
    ("desc",      ["opis", "nazwa", "description", "maktx"]),
    # Partia producenta (SAP MCH1/LICHA) MUSI stać przed 'lot' — alias „partia" jest
    # luźniejszy i połknąłby kolumnę „Partia producenta".
    ("vendor_batch", ["partia producenta", "partia dostawcy", "nr partii producenta",
                      "licha", "vendor batch"]),
    ("lot",       ["partia", "lot", "seria", "batch", "charg"]),
    # Data utworzenia PRZED datą dostawy i expiry, żeby "data utworzenia" nie
    # została skonsumowana przez luźniejsze aliasy dat.
    ("out_created", ["data utworzenia", "utworzen", "erdat"]),
    ("out_date",  ["data dostawy", "data wysyłki", "lfdat", "wadat"]),
    ("expiry",    ["termin ważnoś", "ważnoś", "waznosc", "expiry", "vfdat", "exp", "termin"]),
    ("qty",       ["ilość", "ilosc", "dostępna", "quantity", "menge", "qty"]),
    ("unit",      ["jedn. miary", "miary", "jednostka miary", "jm", "uom", "meins"]),
    ("location",  ["miejsce składow", "miejsce", "lokaliz", "location", "regał", "bin", "lgpla"]),
    ("warehouse", ["typ magazynu", "magazyn", "warehouse", "lgnum"]),
    # Nazwa odbiorcy (SAP „Nazwa odbiorcy 1") — KOD odbiorcy bierze wcześniej `kunnr`,
    # więc tu zostaje nazwa. „nazwa odbiorcy" przed luźnym „odbiorca" dla jasności.
    ("recipient", ["nazwa odbiorcy", "odbiorca", "recipient", "klient", "customer"]),
    ("wz",        ["nr wz", "numer wz", "wz"]),
    ("country",   ["kraj", "land1", "country"]),
    # Kompletacja przed 'picker' — alias 'komplet' pickera łapałby "skompletowana".
    ("completed", ["skompletowan", "kompletacj", "status kompl"]),
    # Status zapasu SAP (B6/F2/Q4…) — specyficzne nagłówki, by nie połknąć „status kompl".
    ("stock_status", ["status zapasu", "stock status", "sobkz", "bestand", "status zap"]),
    # Picking CAŁEJ przesyłki (nie pojedynczej palety) — SAP header not yet confirmed;
    # zbieramy szeroko (picking_done/picking done/kompletacja zakończona).
    ("picking_done", ["picking_done", "picking done", "kompletacja zakończona",
                      "kompletacja zakonczona", "kompletacja przesyłki"]),
    # SAP „Potwierdzone przez" = osoba potwierdzająca pobranie = kompletujący, którego
    # kontroler woła przy niezgodności. „Autor" to twórca dokumentu — celowo NIE picker.
    ("picker",    ["potwierdzone przez", "potwierdził", "picker", "kompletuj", "komplet",
                   "operator", "packer", "zbierający", "zbieracz"]),
    ("weight",    ["waga", "brgew", "weight"]),
    ("length",    ["długo", "dlugo", "laeng", "length"]),
    ("width",     ["szeroko", "breit", "width"]),
    ("height",    ["wysoko", "hoehe", "height"]),
]

_TRUTHY = {"x", "tak", "yes", "y", "true", "1", "c", "skompletowana", "skompletowano"}


def parse_float(val):
    """Liczba z komórki feedu (przecinek europejski, spacje tysięcy) albo None."""
    s = str(val or "").strip().replace(",", ".").replace(" ", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _map_hu_columns(header):
    """Map canonical HU fields → column index by fuzzy header matching (one each)."""
    idx, used = {}, set()
    for field, aliases in _HU_ALIASES:
        for i, h in enumerate(header):
            if i in used:
                continue
            if any(a in h for a in aliases):
                idx[field] = i
                used.add(i)
                break
    return idx


def _match_product_in_ref(ref, code_set):
    """Find a known product code inside a composite REF like '(48301)V(BP-30F)V(...)'."""
    for tok in re.split(r"[^A-Za-z0-9\-]+", ref or ""):
        if len(tok) >= 3 and tok in code_set:
            return tok
    return None


def _parse_date_any(val):
    s = str(val or "").strip()[:10]
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y%m%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def cell(row, idx, field):
    """Wartość komórki pola ``field`` (strip) albo "" — brak kolumny/krótki wiersz/None."""
    i = idx.get(field)
    return str(row[i]).strip() if i is not None and i < len(row) and row[i] is not None else ""


def is_truthy(raw):
    """Flaga z feedu (X/tak/1/skompletowana…) → bool."""
    return raw.lower() in _TRUTHY


def parse_qty(raw):
    """Ilość z feedu: pusta → 0.0, nieliczbowa → 0.0 (przecinek europejski, spacje)."""
    try:
        return float((raw or "0").replace(",", ".").replace(" ", ""))
    except ValueError:
        return 0.0


def resolve_product_code(ref, products, code_set):
    """Kod produktu: dokładny REF albo kod znaleziony w REF złożonym (albo None)."""
    return ref if ref in products else _match_product_in_ref(ref, code_set)


def item_units(qty, unit, ppc):
    """(alt_unit, alt_qty): przy znanym OP/KAR > 1 liczymy w KAR, inaczej jak w bazie."""
    if ppc and ppc > 1:
        return "KAR", round(qty / ppc, 4)
    return unit, qty
