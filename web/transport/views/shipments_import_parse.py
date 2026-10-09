"""Czyste parsowanie importu przesyłek (``planner_shipments_import``) — bez Django/ORM.

Wykrywanie kolumn, normalizacja wierszy do rekordów, grupowanie (po dokumencie albo
odbiorcy), agregacja linii, pola odbiorcy i komunikat końcowy. Moduł NIE jest
star-eksportowany z ``transport.views`` — importuje go wprost ``imports_excel``.
"""
import re
from collections import OrderedDict

# Wzorce nagłówków + domyślny indeks, gdy żaden nagłówek nie pasuje. Kolejność kluczy =
# kolejność pól rekordu. Dopasowanie NIE zależy od kolejności kolumn — patrz detect_columns.
_COLUMNS = (
    ("doc", ("dokument", "dostaw", "delivery", "nr "), 0),
    ("prod", ("produkt", "ref", "kod", "code", "indeks", "sku", "materiał"), 1),
    ("qty", ("ilość", "ilosc", "qty", "quant"), 2),
    ("unit", ("js", "jednostk", "pjm", "unit"), 3),
    ("mail", ("mail", "e-mail", "email"), None),
    ("author", ("autor", "author", "twórca", "osoba"), None),
    # ISO country key (SAP "Klucz kraju/regionu", e.g. IS) → carrier-zone matching.
    ("country", ("klucz kraju", "kraj/region", "kraju", "kraj", "country", "region", "land"), None),
    ("recip_no", ("odbiorca materiał", "odbiorca materialow", "nr odbiorcy",
                  "numer odbiorcy", "kod odbiorcy", "ship-to", "ship to", "sold-to", "kunnr"), None),
    # strict name column first; "recipient" is a broad fallback (incl. bare
    # "odbiorca") guarded later so the number column is never stored as a name.
    ("recip_name", ("opis odbiorcy", "nazwa odbiorcy", "recipient name"), None),
    ("recipient", ("opis odbiorcy", "odbiorca", "nazwa odbiorcy", "klient", "recipient"), None),
    ("city", ("miejscowość", "miejscowosc", "miasto", "city", "stadt"), None),
    ("postal", ("kod pocztowy", "kod poczt", "postal", "zip", "plz"), None),
    ("req", ("wymagania", "wymóg", "wymog", "requirement", "special"), None),
    # SAP header field "Dost.Liczba jednostek obsługi" — real HU/pallet count.
    ("hu", ("liczba jednostek obsługi", "liczba jednostek obslugi",
            "liczba ho", "liczba hu", "ilość ho", "ilosc ho"), None),
)


def map_unit(s):
    u = (s or "").strip().upper()
    if u in ("KAR", "KRT", "CTN", "CS", "BOX"):
        return "kar"
    if u in ("PAZ", "PAL", "PLT", "PL"):
        return "pal"
    return "szt"


def _match_rank(header, pattern):
    """Siła dopasowania wzorca do nagłówka: 3 = cała nazwa, 2 = od początku słowa, 1 = podciąg."""
    pat = pattern.strip()
    if header == pat:
        return 3
    if re.search(r"(?<!\w)" + re.escape(pat), header):
        return 2
    return 1 if pat in header else 0


def detect_columns(header):
    """Mapa pole → indeks kolumny (per plik, więc różne układy plików się łączą).

    Najpierw przypisywane są dopasowania najbardziej specyficzne (cała nazwa > słowo >
    podciąg, potem dłuższy wzorzec), a kolumna przypisana do jednego pola nie trafia do
    innego — więc „Kod pocztowy” przed „Produkt” nie zostaje kolumną produktu."""
    cands = []
    for order, (key, names, _default) in enumerate(_COLUMNS):
        for i, h in enumerate(header):
            best = max(((_match_rank(h, n), len(n.strip())) for n in names), default=(0, 0))
            if best[0]:
                cands.append((-best[0], -best[1], order, i, key))
    idx, taken = {}, set()
    for *_rank, i, key in sorted(cands):
        if key not in idx and i not in taken:
            idx[key], taken = i, taken | {i}
    for key, _names, default in _COLUMNS:
        if key not in idx:
            idx[key] = default if default is not None and default not in taken else None
    return {key: idx[key] for key, _n, _d in _COLUMNS}


# ISO 3166-1 alfa-3 → alfa-2 (UE/EOG + najczęstsze kierunki spoza UE).
ISO3_TO_ISO2 = {
    "AUT": "AT", "BEL": "BE", "BGR": "BG", "HRV": "HR", "CYP": "CY", "CZE": "CZ",
    "DNK": "DK", "EST": "EE", "FIN": "FI", "FRA": "FR", "DEU": "DE", "GRC": "GR",
    "HUN": "HU", "IRL": "IE", "ITA": "IT", "LVA": "LV", "LTU": "LT", "LUX": "LU",
    "MLT": "MT", "NLD": "NL", "POL": "PL", "PRT": "PT", "ROU": "RO", "SVK": "SK",
    "SVN": "SI", "ESP": "ES", "SWE": "SE", "ISL": "IS", "LIE": "LI", "NOR": "NO",
    "GBR": "GB", "CHE": "CH", "UKR": "UA", "SRB": "RS", "BIH": "BA", "MNE": "ME",
    "MKD": "MK", "ALB": "AL", "MDA": "MD", "BLR": "BY", "TUR": "TR",
}


def country_code(value):
    """(kod ISO-2, nieznany kod ISO-3 | None). ISO-3 mapowany, nieznany → pusty."""
    v = (value or "").strip().upper()
    if len(v) == 3 and v.isalpha():
        iso2 = ISO3_TO_ISO2.get(v)
        return (iso2, None) if iso2 else ("", v)
    return v[:2], None


def _cell(r, i):
    return str(r[i]).strip() if (i is not None and i < len(r) and r[i] is not None) else ""


def file_records(header, body):
    """Wiersze jednego pliku → płaska lista rekordów {pole: tekst}."""
    idx = detect_columns(header)
    return [{k: _cell(r, i) for k, i in idx.items()} for r in body]


def recipient_no(value):
    """Numer odbiorcy (≤40 zn.); numer ze spacją nie jest kodem → ""."""
    no = value[:40]
    return "" if " " in no else no


def group_key(rec, consolidate):
    if consolidate:
        no = recipient_no(rec["recip_no"])
        if no:
            return ("r", no)
        return ("r", "|".join((rec["recip_name"], rec["city"], rec["postal"])))
    return ("d", rec["doc"] or "bez numeru")


def group_records(records, consolidate):
    grouped = OrderedDict()
    for rec in records:
        grouped.setdefault(group_key(rec, consolidate), []).append(rec)
    return grouped


def first(rs, key):
    return next((rec[key] for rec in rs if rec[key]), "")


def _qty(value):
    try:
        return float(value.replace(",", ".") or 0)
    except ValueError:
        return 0.0


def aggregate_lines(rs, prod_by_code, missing):
    """Sumuje ilości per kod produktu → OrderedDict code → [qty, unit, source_unit].

    Nieznane (niepuste) kody dopisuje do ``missing``; ilości ≤ 0 / nieliczbowe pomija."""
    agg = OrderedDict()
    for rec in rs:
        if rec["prod"] not in prod_by_code:
            if rec["prod"]:
                missing.add(rec["prod"])
            continue
        qty = _qty(rec["qty"])
        if qty <= 0:
            continue
        if rec["prod"] in agg:
            agg[rec["prod"]][0] += qty
        else:
            agg[rec["prod"]] = [qty, map_unit(rec["unit"]), rec["unit"].upper()]
    return agg


def _hu_int(v):
    try:
        return int(float(str(v).replace(",", ".")))
    except (ValueError, TypeError):
        return 0


def actual_hu_count(rs):
    """Real HU/pallet count (SAP header field, repeated on every line of a document).

    Per document take the max seen; when consolidating several documents, sum their
    per-document counts. 0 → unknown (None)."""
    hu_by_doc = {}
    for rec in rs:
        d = rec["doc"] or ""
        hu_by_doc[d] = max(hu_by_doc.get(d, 0), _hu_int(rec.get("hu")))
    return sum(hu_by_doc.values()) or None


def recipient_fields(rs):
    """(numer, nazwa) odbiorcy grupy.

    Nazwa równa numerowi (porównanie po tej samej normalizacji — także dla numeru ze
    spacją) jest czyszczona. Bez żadnej kolumny nazwy wartość ze spacją z kolumny numeru
    to w praktyce nazwa (np. „PHARMO DEMO SRL”) — wtedy trafia do nazwy."""
    raw_no = first(rs, "recip_no")
    recip_no = recipient_no(raw_no)
    recip_nm = (first(rs, "recip_name") or first(rs, "recipient"))[:200]
    if not recip_nm:
        return recip_no, ("" if recip_no else raw_no[:200])
    if recip_nm == raw_no[:40]:
        recip_nm = ""
    return recip_no, recip_nm


def shipment_name_notes(rs, consolidate, recip_nm, recip_no, max_len=200):
    docs_uniq = list(OrderedDict.fromkeys(rec["doc"] for rec in rs if rec["doc"]))
    if consolidate:
        name = f"Konsolidacja — {recip_nm or recip_no or 'odbiorca'}"[:max_len]
        notes = ("Konsolidacja dokumentów: " + ", ".join(docs_uniq)) if docs_uniq else ""
        return name, notes
    return f"Dostawa {docs_uniq[0] if docs_uniq else 'bez numeru'}"[:max_len], ""


def summary_message(stats, consolidate, n_files, missing, bad_countries=()):
    mode = " skonsolidowanych" if consolidate else ""
    msg = (f"Zaimportowano {stats['shipments']} przesyłek{mode} ({stats['lines']} linii) "
           f"z {n_files} plik(ów).")
    if stats["linked"]:
        msg += f" Podpięto odbiorcę w {stats['linked']} przesyłkach"
        msg += (f" (nowych klientów: {stats['created_customers']})."
                if stats["created_customers"] else ".")
    if missing:
        msg += f" Pominięto {len(missing)} nieznanych kodów: {', '.join(sorted(missing)[:10])}."
    if bad_countries:
        msg += (" Nieznane kody krajów (pole kraju pozostawiono puste): "
                f"{', '.join(sorted(bad_countries)[:10])}.")
    return msg
