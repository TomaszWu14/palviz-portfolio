"""Parsowanie pliku importu master daty (widok ``planner_master_data_import``).

Czysta logika (bez ORM): nagłówek + wiersze z ``_read_table`` → lista rekordów
produktów (słowniki). Dwa formaty:

- **A — SAP MARM**: wiersz na jednostkę miary (SZT/OP/OPZ/KAR/PAZ…), grupowane po materiale;
- **B — kolumny łączone**: ``ref_code`` + ``*_wymiar`` jako ``'L X W X H cm'``, ilości
  w zduplikowanych kolumnach „Ilość…” czytane pozycyjnie (``*_wymiar`` + 2).

Moduł NIE jest star-eksportowany przez ``ui.views`` (brak ``__all__`` widoków).
"""
import re
from collections import defaultdict

_DIM_RE = re.compile(r'([\d.,]+)\s*[xX]\s*([\d.,]+)\s*[xX]\s*([\d.,]+)')

# Kod alternatywnej jednostki miary (MARM) → rola w hierarchii opakowań.
_MARM_ROLE = {
    code: role
    for role, codes in (("piece", ("SZT", "JU", "ST", "PC", "EA")),
                        ("op", ("OP", "OPA", "OPK")),
                        ("opz", ("OPZ",)),
                        ("carton", ("KAR", "KRT", "CTN", "CS", "BOX")),
                        ("pallet", ("PAZ", "PAL", "PLT", "PL")))
    for code in codes
}


# ── prymitywy komórek ─────────────────────────────────────────────────────
def _parse_dim(s):
    """'L X W X H cm' (przecinki dziesiętne) → (l, w, h) albo None (brak/niedodatnie)."""
    m = _DIM_RE.search(s or "")
    if not m:
        return None
    try:
        l, w, h = (float(g.replace(",", ".")) for g in m.groups())
    except ValueError:
        return None
    return (l, w, h) if (l > 0 and w > 0 and h > 0) else None


def _to_int(s, default=1):
    try:
        return int(float(str(s).replace(",", ".")))
    except (ValueError, TypeError):
        return default


def _col(header, *names, default=None):
    """Indeks pierwszej pasującej nazwy kolumny (nagłówek już lower/strip)."""
    for n in names:
        if n in header:
            return header.index(n)
    return default


def _cell(r, i):
    # r may be None (a material missing a given unit-of-measure row).
    return str(r[i]).strip() if (r is not None and i is not None and i < len(r) and r[i] is not None) else ""


def _num(r, i):
    s = _cell(r, i)
    if s in ("", "-"):
        return 0.0
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return 0.0


# ── Format A: SAP MARM ────────────────────────────────────────────────────
def _marm_columns(header):
    return {
        "mat": _col(header, "materiał", "material", default=0),
        "ajm": _col(header, "alternatywna jednostka miary", "ajm"),
        "mian": _col(header, "mianownik", "umren"), "licz": _col(header, "licznik", "umrez"),
        "szer": _col(header, "szerokość", "width"), "wys": _col(header, "wysokość", "height"),
        "dl": _col(header, "długość", "length"),
        "brutto": _col(header, "waga brutto", "brutto", "weight"),
        "ean": _col(header, "kod ean/upc", "ean"),
    }


def _group_marm_rows(body, c):
    """Wiersze MARM → ({materiał: {rola: wiersz}}, kolejność materiałów). Pierwszy wiersz
    danej roli wygrywa; JU trafia dodatkowo pod klucz 'ju' (przelicznik szt./bazę)."""
    groups, order = defaultdict(dict), []
    for r in body:
        mat = _cell(r, c["mat"])
        if not mat:
            continue
        if mat not in groups:
            order.append(mat)
        ajm = _cell(r, c["ajm"]).upper()
        role = _MARM_ROLE.get(ajm)
        if role and role not in groups[mat]:
            groups[mat][role] = r
        # JU (jednostka użytkowa) carries the szt-per-base conversion separately,
        # even though it also matches the "piece" role.
        if ajm == "JU" and "ju" not in groups[mat]:
            groups[mat]["ju"] = r
    return groups, order


def _marm_dim(r, c):
    if not r:
        return None
    l, w, h = _num(r, c["dl"]), _num(r, c["szer"]), _num(r, c["wys"])
    return (l, w, h) if (l > 0 and w > 0 and h > 0) else None


def _marm_pcs(r, c):
    if not r:
        return 0
    mi = _num(r, c["mian"]) or 1
    li = _num(r, c["licz"])
    return max(0, round(li / mi)) if li else 0


def _marm_ju_factor(r, c):
    # JU row: Mianownik JU = Licznik base (np. 100 JU = 1 jedn. bazowa).
    if not r:
        return 1
    mi, li = _num(r, c["mian"]), _num(r, c["licz"])
    return max(1, round(mi / li)) if (li and mi > li) else 1


def _marm_weight(piece, op, kar, kpcs, c):
    """Waga na sztukę: brutto SZT, potem OP / sztuk w OP, potem KAR / sztuk w kartonie.

    OP i KAR są liczone tak samo (waga opakowania podzielona przez liczbę sztuk),
    więc ``unit_weight`` zawsze oznacza wagę JEDNEJ sztuki."""
    weight = _num(piece, c["brutto"])
    if not weight and op:
        op_pcs = _marm_pcs(op, c)
        weight = _num(op, c["brutto"]) / op_pcs if op_pcs else 0.0
    if not weight and kar:
        weight = _num(kar, c["brutto"]) / kpcs if kpcs else 0.0
    return weight


def _marm_record(mat, g, c):
    piece, op, kar, paz = g.get("piece"), g.get("op"), g.get("carton"), g.get("pallet")
    kpcs = _marm_pcs(kar, c) or 1
    weight = _marm_weight(piece, op, kar, kpcs, c)
    return {
        "code": mat[:50], "name": mat[:200],
        "ean": (_cell(piece, c["ean"]) or _cell(kar, c["ean"]))[:30] if (piece or kar) else "",
        "supplier": "",
        "unit": _marm_dim(piece, c) or _marm_dim(op, c),
        "carton": _marm_dim(kar, c), "pcs": kpcs,
        "opz": _marm_dim(g.get("opz"), c), "opz_pcs": _marm_pcs(g.get("opz"), c),
        "op": _marm_dim(op, c), "op_pcs": _marm_pcs(op, c),
        "weight": round(weight, 4),
        "pallet_h": int(_num(paz, c["wys"])) if paz else 0,
        "pallet_w": int(_num(paz, c["brutto"])) if paz else 0,
        "demand": _marm_pcs(paz, c),
        "units_per_piece": _marm_ju_factor(g.get("ju") or piece, c),
    }


def _parse_marm(header, body):
    c = _marm_columns(header)
    groups, order = _group_marm_rows(body, c)
    return [_marm_record(mat, groups[mat], c) for mat in order]


# ── Format B: kolumny łączone ─────────────────────────────────────────────
def _combined_columns(header):
    def plus2(i):
        return (i + 2) if i is not None else None

    i_kar, i_op, i_opz = (_col(header, "karton_wymiar"), _col(header, "op_wymiar"),
                          _col(header, "opz_wymiar"))
    return {
        "code": _col(header, "ref_code", default=0),
        "name": _col(header, "opis_pl"), "short": _col(header, "txt_short_pl"),
        "ean": _col(header, "sztuka_ean"), "sup": _col(header, "producent"),
        "szt": _col(header, "sztuka_wymiar"), "kar": i_kar, "op": i_op, "opz": i_opz,
        "kar_qty": plus2(i_kar), "op_qty": plus2(i_op), "opz_qty": plus2(i_opz),
    }


def _combined_record(r, code, c):
    return {
        "code": code[:50],
        "name": (_cell(r, c["name"]) or _cell(r, c["short"]) or code)[:200],
        "ean": _cell(r, c["ean"])[:30], "supplier": _cell(r, c["sup"])[:50],
        "unit": _parse_dim(_cell(r, c["szt"])),
        "carton": _parse_dim(_cell(r, c["kar"])),
        "pcs": _to_int(_cell(r, c["kar_qty"]), 1),
        "opz": _parse_dim(_cell(r, c["opz"])), "opz_pcs": _to_int(_cell(r, c["opz_qty"]), 0),
        "op": _parse_dim(_cell(r, c["op"])), "op_pcs": _to_int(_cell(r, c["op_qty"]), 0),
        "weight": 0.0, "pallet_h": 0, "pallet_w": 0, "demand": 0,
        "units_per_piece": 1,
    }


def _parse_combined(header, body):
    c = _combined_columns(header)
    parsed = []
    for r in body:
        code = _cell(r, c["code"])
        if code:
            parsed.append(_combined_record(r, code, c))
    return parsed


def _dedupe_first_wins(parsed):
    """Jeden rekord na kod — PIERWSZY wiersz wygrywa → (rekordy, liczba duplikatów).

    Dotyczy zarówno powtórzonego kodu, jak i kolizji po przycięciu do 50 znaków
    (różne surowe wartości → ten sam ``code``), więc bulk_create nie trafi na UNIQUE."""
    unique = {}
    for p in parsed:
        unique.setdefault(p["code"], p)
    return list(unique.values()), len(parsed) - len(unique)


# MARM ma kilka wierszy na materiał (SZT/OP/OPZ/KAR/PAZ/JU…) — twardy limit wierszy to
# limit materiałów × tyle (ochrona pamięci workera przy absurdalnie „szerokim” pliku).
MARM_MAX_ROWS_PER_MATERIAL = 10


def _is_marm(header):
    return _col(header, "alternatywna jednostka miary", "ajm") is not None


def import_size_error(header, body, limit):
    """→ komunikat „Plik zbyt duży …” albo None. Format łączony: limit wierszy. MARM: limit
    liczony po MATERIAŁACH (60 000 wierszy MARM to tylko ok. 12–15 tys. materiałów) plus
    twardy limit wierszy ``limit × MARM_MAX_ROWS_PER_MATERIAL``."""
    if not _is_marm(header):
        return None if len(body) <= limit else f"Plik zbyt duży (max {limit:,} wierszy).".replace(",", " ")
    max_rows = limit * MARM_MAX_ROWS_PER_MATERIAL
    if len(body) > max_rows:
        return f"Plik zbyt duży (max {max_rows:,} wierszy MARM).".replace(",", " ")
    c = _marm_columns(header)
    if len({_cell(r, c["mat"]) for r in body} - {""}) > limit:
        return f"Plik zbyt duży (max {limit:,} materiałów MARM).".replace(",", " ")
    return None


def fractional_counters(header, body, limit=5):
    """MARM: niecałkowite Licznik/Mianownik (np. „1.991”) — import zaokrągla je do sztuk
    (kropka = separator dziesiętny, jak w eksporcie). → (lista „MAT AJM: licznik X → N”,
    liczba wszystkich), żeby zaokrąglenie nie działo się po cichu."""
    if not _is_marm(header):
        return [], 0
    c = _marm_columns(header)
    out = []
    for r in body:
        for key, label in (("licz", "licznik"), ("mian", "mianownik")):
            v = _num(r, c[key])
            if v and v != int(v):
                out.append(f"{_cell(r, c['mat'])} {_cell(r, c['ajm']).upper()}: "
                           f"{label} {_cell(r, c[key])} → {round(v)}")
    return out[:limit], len(out)


def parse_master_data(header, body):
    """Nagłówek + wiersze → (rekordy produktów, liczba pominiętych duplikatów kodu).

    Format wykrywany po kolumnie AJM."""
    if _is_marm(header):
        parsed = _parse_marm(header, body)
    else:
        parsed = _parse_combined(header, body)
    return _dedupe_first_wins(parsed)
