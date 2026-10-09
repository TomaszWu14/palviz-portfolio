"""Rzeczywiste palety w lokalizacjach → scena Blendera („cyfrowe zdjęcie" magazynu).

Źródła (każde opcjonalne, łączone po kodzie lokalizacji):
  • WarehouseSnapshot (eksport SAP) — zajętość, blokady pobrania/odłożenia, max wysokość,
  • palety HU na stanie (Shipment.is_stock) — SKU, nazwa, LOT, termin ważności, ilość,
  • PickerActivityBatch — liczba pobrań z lokalizacji i klasa ABC SKU (wg liczby pobrań).

Kod lokalizacji jest parsowany tą samą konwencją co mapa 3D (`_parse_loc_code`):
litera EWM = poziom w stosie (ui.views.core.ewm_levels), legacy J/K/… = kolumna w boku.
Lokalizacje spoza regałów modelu są liczone w `stats.unmapped`, nie znikają po cichu.
"""
import re
from collections import Counter, defaultdict
from datetime import date

from ui.views.core.ewm_levels import code_slot, shelves_in_opening

from .blender_route import rack_axes, rack_point
from .locations import parse_code

PALLET = (0.8, 1.2)          # EU: 0,8 m wzdłuż trawersu × 1,2 m w głąb regału
DEFAULT_LOAD_H = 1.2         # m — wysokość ładunku, gdy SAP nie podaje max wysokości
MAX_PALLETS = 20_000


class SlotLocator:
    """Kod lokalizacji → gniazdo w regale modelu (środek palety, wysokość, obrót).

    Model magazynu zna tylko liczbę boków regału, a nie ich kody — indeks boku to
    ranga kodu boku wśród kodów widzianych dla regału, przeskalowana do n_bays."""

    def __init__(self, racks, codes, levels=None):
        self.by_key = {(r["zone"], r["rack_id"]): r for r in racks}
        # Poziom z mastera/mapy wygrywa z dekodowaniem litery (w B0 litera = poziom:
        # A=1, B=2, C=3…; w konwencji mapy B = kolumna 1 na poziomie 1).
        self.levels = {k.strip().upper(): v for k, v in (levels or {}).items() if v}
        # Jeśli master choć raz przeczy dekodowaniu litery, cała hala ma konwencję
        # „litera = poziom" (B0) → jedna pozycja w boku (D/-1/-2 to głębokość/podział).
        self.letter_is_level = any(
            (p := parse_code(c)) and self.levels.get(c.strip().upper(), p[4]) != p[4]
            for c in codes)
        bays, lanes = defaultdict(set), defaultdict(int)
        for code in codes:
            p = self._parse(code)
            if p and (p[0], p[1]) in self.by_key:
                bays[(p[0], p[1])].add(p[2])
                lanes[(p[0], p[1])] = max(lanes[(p[0], p[1])], p[3] + 1)
        self.bay_rank = {k: {b: i for i, b in enumerate(sorted(v, key=int))} for k, v in bays.items()}
        self.lanes = lanes

    def _parse(self, code):
        p = parse_code(code)
        if not p:
            return None
        level = self.levels.get((code or "").strip().upper())
        if self.letter_is_level:
            return p[0], p[1], p[2], 0, level or p[4]
        return p

    def rack_and_bay(self, code):
        p = self._parse(code)
        if not p or (p[0], p[1]) not in self.by_key:
            return None
        rack = self.by_key[(p[0], p[1])]
        rank = self.bay_rank.get((p[0], p[1]), {})
        n = max(1, len(rank))
        idx = rank.get(p[2], 0) * max(1, rack["n_bays"]) // n
        return rack, idx, p[3], p[4]

    def slot(self, code):
        """dict gniazda: x, y, z (dół palety), heading, rozmiar [w, d] (+ half 1/2) albo None."""
        found = self.rack_and_bay(code)
        if not found:
            return None
        rack, bay_idx, col_idx, level = found
        key = (rack["zone"], rack["rack_id"])
        bays = max(1, rack["n_bays"])
        lanes = max(1, self.lanes.get(key, 1))
        rank = self.bay_rank.get(key, {})
        # Model z rysunku ma FIZYCZNE gniazda (np. 39 × 2,7 m), a kody niosą pozycje paletowe
        # (…300/301/302 = 3 palety w gnieździe 30). Gdy kodów boków jest więcej niż gniazd,
        # każdy kod dostaje równy odcinek regału — inaczej palety gniazda leżały jedna na drugiej.
        if len(rank) > bays:
            pos, pos_w = rank.get(self._parse(code)[2], 0), rack["width"] / len(rank)
        else:
            pos, pos_w = bay_idx, rack["width"] / bays
        lane_w = pos_w / lanes
        half = _half(code)
        cell_w = lane_w / 2 if half else lane_w          # połówka = miejsce podzielone wszerz na dwa
        pallet_w = PALLET[0] / 2 if half else PALLET[0]  # połówka ~40 cm
        along = pos * pos_w + min(col_idx, lanes - 1) * lane_w + (max(half, 1) - 0.5) * cell_w
        x, y = rack_point(rack, along, rack["depth"] / 2)
        lvl = min(max(1, level), max(1, rack["n_levels"]))
        u_d = rack_axes(rack["angle"])[1]
        # Półki B/C/D (poz. 1) i części G/H (poz. 2) leżą JEDNA NAD DRUGĄ w otworze poziomu.
        slot_ewm = None if self.letter_is_level else code_slot(code)
        n_sh = shelves_in_opening(slot_ewm)
        shelf_h = rack["level_h"] / n_sh
        z = (lvl - 1) * rack["level_h"] + ((slot_ewm.shelf - 1) * shelf_h if n_sh > 1 else 0)
        out = {"x": round(x, 3), "y": round(y, 3), "z": round(z + 0.05, 3),
               "level": lvl, "rack": f"{rack['zone']}-{rack['rack_id']}",
               # heading = kierunek głębokości regału (paleta wchodzi „nosem" w regał)
               "heading": round(_deg(u_d), 2),
               "w": round(min(pallet_w, cell_w - 0.06), 3),
               "d": round(min(PALLET[1], rack["depth"] - 0.04), 3),
               "max_h": round(max(0.3, shelf_h - 0.2), 3)}
        if half:
            out["half"] = half
        return out


_HALF = re.compile(r"-([12])$")


def _half(code):
    """1/2 dla połówki miejsca (kod z końcówką -1/-2, np. B0-07-300C-1), inaczej 0.
    Parser mapy (parse_code) tę końcówkę pomija — obie połówki dają ten sam bok i kolumnę."""
    m = _HALF.search((code or "").strip())
    return int(m.group(1)) if m else 0


def _deg(v):
    import math
    return math.degrees(math.atan2(v[1], v[0]))


def abc_by_hits(hits_per_sku, a_cut=0.80, b_cut=0.95):
    """Klasa ABC wg udziału w pobraniach (ta sama reguła progów co ui.slotting.abc_classify)."""
    total = sum(hits_per_sku.values())
    out, cum = {}, 0.0
    for sku, n in sorted(hits_per_sku.items(), key=lambda kv: kv[1], reverse=True):
        prev = cum / total if total else 0.0
        cum += n
        out[sku] = "A" if prev < a_cut else ("B" if prev < b_cut else "C")
    return out


def build_pallets(racks, snapshot_rows=(), stock_items=(), activity=(), today=None, levels=None,
                  extra_codes=()):
    """Czysta funkcja: dane wejściowe jako proste krotki/dicty → (pallets, stats, codes).

    snapshot_rows: dicty {location_code, is_empty, blocked_pick, blocked_put, capacity_mm}
    stock_items:   dicty {location, sku, name, lot, expiry(date|None), qty, unit, hu}
    activity:      krotki (location_code, material_code)
    levels:        {kod: poziom} z mastera lokalizacji (pierwszeństwo przed literą kodu)
    extra_codes:   inne kody widziane w danych (np. zadania EWM) — tylko do numeracji boków
    """
    today = today or date.today()
    snap = {r["location_code"].strip().upper(): r for r in snapshot_rows if r.get("location_code")}
    stock = defaultdict(list)
    for it in stock_items:
        if it.get("location"):
            stock[it["location"].strip().upper()].append(it)
    loc_hits, sku_hits = Counter(), Counter()
    for code, sku in activity:
        loc_hits[(code or "").strip().upper()] += 1
        if sku:
            sku_hits[sku] += 1
    abc = abc_by_hits(sku_hits)

    codes = set(snap) | set(stock) | set(loc_hits) | {c.strip().upper() for c in extra_codes}
    loc = SlotLocator(racks, codes, levels)
    pallets, unmapped = [], 0
    occupied_codes = {c for c, r in snap.items() if not r.get("is_empty", True)} | set(stock)
    blocked_codes = {c for c, r in snap.items() if r.get("blocked_pick") or r.get("blocked_put")}
    for code in sorted(occupied_codes | blocked_codes):
        slot = loc.slot(code)
        if slot is None:
            unmapped += 1
            continue
        if len(pallets) >= MAX_PALLETS:
            break
        row, items = snap.get(code, {}), stock.get(code, [])
        cap = (row.get("capacity_mm") or 0) / 1000
        height = min(slot.pop("max_h"), cap * 0.9 if cap else DEFAULT_LOAD_H)
        main = max(items, key=lambda i: i.get("qty") or 0) if items else {}
        expiries = [i["expiry"] for i in items if i.get("expiry")]
        soonest = min(expiries) if expiries else None
        occupied = code in occupied_codes
        pallets.append({
            "code": code, **slot, "h": round(height if occupied else 0.15, 3),
            "state": ("blocked" if code in blocked_codes else "occupied") if occupied else "blocked_empty",
            "sku": main.get("sku", ""), "name": main.get("name", ""), "lot": main.get("lot", ""),
            "qty": round(sum(i.get("qty") or 0 for i in items), 3), "unit": main.get("unit", ""),
            "hu": sorted({i["hu"] for i in items if i.get("hu")}), "skus": len({i.get("sku") for i in items}),
            "expiry": soonest.isoformat() if soonest else None,
            "days_to_expiry": (soonest - today).days if soonest else None,
            "abc": abc.get(main.get("sku", ""), ""), "picks": loc_hits.get(code, 0),
        })
    stats = {"pallets": len(pallets), "occupied": sum(p["state"] != "blocked_empty" for p in pallets),
             "blocked": sum(p["state"] != "occupied" for p in pallets), "unmapped": unmapped,
             "snapshot_rows": len(snap), "stock_locations": len(stock),
             "truncated": len(occupied_codes | blocked_codes) - unmapped > MAX_PALLETS}
    return pallets, stats, loc


# ─── ORM ─────────────────────────────────────────────────────────────────────

def load_stock_inputs(snapshot=None, batch=None):
    """Pobiera dane z bazy do `build_pallets` (snapshot/batch opcjonalne; stan HU zawsze)."""
    from ui.views.core import HandlingUnitItem

    snap_rows = []
    if snapshot is not None:
        snap_rows = list(snapshot.rows.values(
            "location_code", "is_empty", "blocked_pick", "blocked_put", "capacity_mm"))
    items = (HandlingUnitItem.objects
             .filter(hu__shipment__is_stock=True).exclude(hu__location="")
             .select_related("hu", "product")
             .only("ref_code", "description", "lot", "expiry", "alt_qty", "expected_qty",
                   "alt_unit", "unit", "base_qty", "base_unit", "hu__location", "hu__code",
                   "hu__seq", "product__code", "product__name"))

    def _qty(it):
        # Pierwsza niezerowa: AJM (np. KAR) → oczekiwana → JP (import stanu bywa tylko w JP).
        for qty, unit in ((it.alt_qty, it.alt_unit), (it.expected_qty, it.unit),
                          (it.base_qty, it.base_unit)):
            if qty:
                return qty, unit
        return 0, it.base_unit or it.unit

    stock = [{
        "location": it.hu.location, "hu": it.hu.code or f"HU {it.hu.seq}",
        "sku": it.product.code if it.product else it.ref_code,
        "name": ((it.product.name if it.product else it.description) or "")[:80],
        "lot": it.lot, "expiry": it.expiry,
        "qty": _qty(it)[0], "unit": _qty(it)[1],
    } for it in items.iterator()]
    activity = []
    if batch is not None:
        activity = list(batch.activities.values_list("location_code", "material_code").iterator())
    return snap_rows, stock, activity


def load_master_levels():
    """{kod: poziom} z aktywnego mastera lokalizacji (pusty dict, gdy brak)."""
    from ui.views.core import WarehouseLocationMasterBatch

    batch = WarehouseLocationMasterBatch.objects.filter(is_active=True).order_by("-uploaded_at").first()
    if batch is None:
        return {}
    return dict(batch.locations.values_list("location_code", "level"))
