"""„Wykryj z EWM”: kody lokalizacji z mastera → propozycja szablonów gniazd, reguł rzędów
i wyjątków, która po rozwinięciu (addressing.expand_row) odtwarza DOKŁADNIE te same kody.

Czysty Python (bez ORM) — zapis propozycji robi wh3d.ewm_service.apply_proposal.
Wzór gniazda = zbiór (pozycja, litera, połówka) + typ EWM litery. Wzór „regularny”
(pełna siatka pozycji 0..k-1 × litery, połówki per litera) staje się szablonem;
nieregularne gniazdo dostaje najbliższy szablon + wyjątki skip/add.
"""
from collections import Counter, defaultdict
from statistics import median

from .addressing import format_bay_numbers, letter_rank, parse_code

BEAM_MM = {2: 1825, 3: 2700, 4: 3600}   # typowe belki; inne k → k × 900 mm (do poprawy w formularzu)
DEFAULT_HEIGHT_MM = 1000                  # eksport EWM nie niesie wysokości → wartość do poprawy


def _grid(k, letters):
    """k pozycji × [(litera, split)] → zbiór komórek (pozycja, litera, połówka)."""
    return {(p, letter, h) for letter, split in letters for p in range(k)
            for h in ((1, 2) if split else (0,))}


def _shape(cells, types):
    """Komórki gniazda → (sygnatura obrysu, czy siatka regularna).
    Sygnatura = (k, ((litera, split, typ), …)) — ten sam kształt co _template_sig."""
    k = max(p for p, _, _ in cells) + 1
    split = defaultdict(bool)
    for _, letter, h in cells:
        split[letter] |= bool(h)
    letters = sorted(split, key=letter_rank)
    sig = (k, tuple((L, split[L], types[L]) for L in letters))
    return sig, set(cells) == _grid(k, [(L, split[L]) for L in letters])


def _template_sig(k, levels):
    return k, tuple(sorted(((lv["letter"], bool(lv.get("split")), lv.get("ewm_type", "")) for lv in levels),
                           key=lambda x: letter_rank(x[0])))


def _new_template(tpls, sig):
    tpls.setdefault(sig, {"key": "", "pk": None, "pallets_per_beam": sig[0], "bays": 0, "_new": True,
                          "_heights": defaultdict(list), "_kg": defaultdict(list)})


def _distance(cells, sig):
    """Liczba różnic gniazda od szablonu: brakujące + nadmiarowe komórki + inne typy EWM."""
    k, levels = sig
    grid = _grid(k, [(L, s) for L, s, _ in levels])
    ltype = {L: t for L, _, t in levels}
    return len(grid ^ set(cells)) + sum(1 for c in grid & set(cells) if cells[c] != ltype[c[1]])


def detect(rows, master, templates=()):
    """rows: [{"zone", "rack_id", "n_bays"}]; master: [(kod, typ_ewm, wysokość_mm, udźwig_kg)];
    templates: istniejące szablony (atrybuty pk, name, pallets_per_beam, levels).

    → {"templates": [{key, pk, name, pallets_per_beam, beam_mm, levels, bays}],
       "rows": [{zone, rack_id, template, bay_numbers, overrides, codes}],
       "missing_rows": ["B0-99", …]}   (rzędy bez żadnego kodu w masterze)"""
    wanted = {(r["zone"], r["rack_id"]): r for r in rows}
    loc = defaultdict(lambda: defaultdict(dict))            # (zone, rack) → bay → komórka → typ
    stats = defaultdict(lambda: ([], []))                    # (zone, rack, bay, litera) → (wys., kg)
    for code, ewm_type, height, max_kg in master:
        p = parse_code(code)
        if not p or (p[0], p[1]) not in wanted:
            continue
        zone, aisle, bay, pos, letter, half = p
        loc[(zone, aisle)][bay][(pos, letter, half)] = ewm_type or ""
        hs, ws = stats[(zone, aisle, bay, letter)]
        if height:
            hs.append(height)
        if max_kg:
            ws.append(max_kg)

    # 1) szablony: istniejące + wzory regularnych gniazd
    tpls = {}                                                # sygnatura → propozycja szablonu
    for t in templates:
        tpls.setdefault(_template_sig(t.pallets_per_beam, t.levels),
                        {"key": f"pk:{t.pk}", "pk": t.pk, "name": t.name, "pallets_per_beam": t.pallets_per_beam,
                         "levels": t.levels, "bays": 0, "_new": False})
    bay_info = {}                                            # (zone, rack, bay) → (komórki, sygn.|None, obrys)
    for (zone, aisle), bays in loc.items():
        for bay, cells in bays.items():
            types = {L: Counter(t for c, t in cells.items() if c[1] == L).most_common(1)[0][0]
                     for L in {c[1] for c in cells}}
            sig, regular = _shape(cells, types)
            if regular:
                _new_template(tpls, sig)
            bay_info[(zone, aisle, bay)] = (cells, sig if regular else None, sig)

    # 2) gniazdo → szablon (regularne po sygnaturze, nieregularne: najbliższy)
    freq = Counter(sig for _, sig, _ in bay_info.values() if sig)
    choice = {}
    for key, (cells, sig, hull) in sorted(bay_info.items()):
        if sig is None:
            if not tpls:                      # brak jakiegokolwiek wzoru regularnego → obrys gniazda
                _new_template(tpls, hull)
            sig = min(tpls, key=lambda s: (_distance(cells, s), -freq[s], repr(s)))
        choice[key] = sig
        t = tpls[sig]
        t["bays"] += 1
        if t["_new"]:
            for L, _, _ in sig[1]:
                hs, ws = stats[(key[0], key[1], key[2], L)]
                t["_heights"][L] += hs
                t["_kg"][L] += ws

    new = sorted(((s, t) for s, t in tpls.items() if t["_new"]), key=lambda st: -st[1]["bays"])
    for i, ((k, levels), t) in enumerate(new):
        t["key"] = f"new:{i}"
        t["beam_mm"] = BEAM_MM.get(k, k * 900)
        t["levels"] = [{"letter": L, "split": s, "ewm_type": typ,
                        "height_mm": int(median(t["_heights"][L])) if t["_heights"][L] else DEFAULT_HEIGHT_MM,
                        "max_kg": int(median(t["_kg"][L])) if t["_kg"][L] else 0}
                       for L, s, typ in levels]
        types = "/".join(dict.fromkeys(typ for _, _, typ in levels if typ))
        t["name"] = (f"{k} pal. · " + " ".join(L + ("½" if s else "") for L, s, _ in levels)
                     + (f" · {types}" if types else ""))

    # 3) rzędy: szablon domyślny, numeracja, wyjątki
    out_rows, missing = [], []
    for (zone, aisle), row in sorted(wanted.items()):
        bays = loc.get((zone, aisle))
        if not bays:
            missing.append(f"{zone}-{aisle}")
            continue
        order = sorted(bays)
        default = Counter(choice[(zone, aisle, b)] for b in order).most_common(1)[0][0]
        overrides = []
        n, span = row["n_bays"], order[-1] - order[0] + 1
        if n >= span:                         # fizyczne gniazda bez adresów → skip całego gniazda
            numbers = list(range(order[0], order[0] + n))
            overrides += [{"bay": b, "position": 0, "letter": "", "half": 0, "action": "skip", "value": ""}
                          for b in numbers if b not in bays]
        else:                                 # przerwy w numeracji EWM → numeracja z przerwami
            numbers = order
        for b in order:
            sig = choice[(zone, aisle, b)]
            if sig != default:
                overrides.append({"bay": b, "position": 0, "letter": "", "half": 0,
                                  "action": "template", "value": "", "template": tpls[sig]["key"]})
            k, levels = sig
            grid = _grid(k, [(L, s) for L, s, _ in levels])
            ltype = {L: typ for L, _, typ in levels}
            cells = bays[b]
            for pos, L, h in sorted(grid - set(cells)):
                overrides.append({"bay": b, "position": pos, "letter": L, "half": h, "action": "skip", "value": ""})
            for (pos, L, h), typ in sorted(cells.items()):
                if (pos, L, h) not in grid:
                    overrides.append({"bay": b, "position": pos, "letter": L, "half": h,
                                      "action": "add", "value": typ})
                elif typ != ltype[L]:
                    overrides.append({"bay": b, "position": pos, "letter": L, "half": h,
                                      "action": "ewm_type", "value": typ})
        out_rows.append({"zone": zone, "rack_id": aisle, "template": tpls[default]["key"],
                         "bay_numbers": format_bay_numbers(numbers), "overrides": overrides,
                         "codes": sum(len(c) for c in bays.values())})

    used = {tpls[s]["key"] for s in choice.values()}
    templates_out = [{k: v for k, v in t.items() if not k.startswith("_")}
                     for t in tpls.values() if t["key"] in used]
    templates_out.sort(key=lambda t: (t["pk"] is None, -t["bays"]))
    return {"templates": templates_out, "rows": out_rows, "missing_rows": missing}
