"""Katalog elementów do projektowania wariantów magazynu — JEDNO źródło prawdy.

Czysty Python (bez Django, bez bpy): importuje go zarówno GROOVE (wskaźniki wariantów),
jak i zestaw projektowy w Blenderze (`tools/blender/palviz_design_kit.py`), więc
pojemność, obrys i wymagania alejek liczą się w obu miejscach identycznie.

Wspólna konwencja geometrii (jak regały modelu magazynu): element ma narożnik (x, y)
na planie hali, oś szerokości wzdłuż kąta `angle`, głębokość „w bok" od frontu.
Jednostki: metry, sekundy, palety (EU 1,2 × 0,8 m).

Wartości domyślne to typowe dane katalogowe (rząd wielkości do porównań wariantów),
nie oferta dostawcy — przed decyzją inwestycyjną zweryfikuj je z dostawcą sprzętu.
"""

PALLET_L, PALLET_W = 1.2, 0.8

ELEMENTS = {
    "rack_std": {
        "label": "Regał paletowy (wózek wysokiego składowania)",
        "group": "składowanie",
        "params": {"bays": 10, "levels": 5, "bay_width": 2.7, "depth": 1.1, "level_h": 1.8,
                   "pallets_per_bay": 3},
        "aisle_m": 3.0,          # reach truck: korytarz roboczy ~2,9–3,2 m
        "equipment": "Wózek wysokiego składowania (reach truck)",
    },
    "rack_vna": {
        "label": "Regał wąskokorytarzowy VNA (wózek systemowy)",
        "group": "składowanie",
        "params": {"bays": 20, "levels": 8, "bay_width": 2.7, "depth": 1.1, "level_h": 1.6,
                   "pallets_per_bay": 3},
        "aisle_m": 1.8,          # wózek systemowy prowadzony szynowo/indukcyjnie
        "equipment": "Wózek systemowy VNA (prowadzenie szynowe / indukcyjne)",
    },
    "shuttle": {
        "label": "Regał kanałowy z shuttlem (składowanie blokowe)",
        "group": "składowanie",
        "params": {"channels": 6, "depth_pallets": 12, "levels": 5, "channel_width": 1.4,
                   "level_h": 1.6},
        "aisle_m": 3.0,          # obsługa czoła kanałów wózkiem
        "equipment": "Wózek satelitarny (shuttle) + wózek wysokiego składowania na czole",
        "throughput_h": 25,      # cykle/h na shuttle (rząd wielkości)
    },
    "amr": {
        "label": "Robot AMR / AGV (transport palet)",
        "group": "transport",
        "params": {"length": 1.3, "width": 0.9, "speed": 1.5},
        "aisle_m": 2.0,          # mijanie się dwóch robotów / robot + pieszy
        "throughput_h": 20,      # zadań/h na robota przy ~60 m trasy
    },
    "amr_station": {
        "label": "Stanowisko kompletacji (towar do człowieka)",
        "group": "kompletacja",
        "params": {"width": 2.5, "depth": 2.0, "ports": 2},
        "throughput_h": 150,     # linii/h na stanowisko
    },
    "conveyor": {
        "label": "Przenośnik rolkowy",
        "group": "transport",
        "params": {"length": 10.0, "width": 0.8, "height": 0.8, "speed": 0.5},
        "throughput_h": 1200,    # kartonów/h
    },
    "sorter": {
        "label": "Sorter z zsypami",
        "group": "transport",
        "params": {"length": 12.0, "width": 1.2, "chutes": 10},
        "throughput_h": 3000,    # kartonów/h
    },
}

RACK_KINDS = ("rack_std", "rack_vna", "shuttle")


def params_for(kind, **overrides):
    """Parametry elementu: domyślne z katalogu + nadpisania (tylko znane klucze)."""
    if kind not in ELEMENTS:
        raise ValueError(f"Nieznany element: {kind!r}. Dostępne: {', '.join(ELEMENTS)}")
    params = dict(ELEMENTS[kind]["params"])
    unknown = set(overrides) - set(params)
    if unknown:
        raise ValueError(f"{kind}: nieznane parametry {sorted(unknown)}")
    params.update(overrides)
    return params


def footprint(kind, p):
    """(szerokość wzdłuż osi elementu, głębokość) [m]."""
    if kind in ("rack_std", "rack_vna"):
        return p["bays"] * p["bay_width"], p["depth"]
    if kind == "shuttle":
        return p["channels"] * p["channel_width"], p["depth_pallets"] * (PALLET_L + 0.05)
    if kind in ("amr", ):
        return p["length"], p["width"]
    if kind == "amr_station":
        return p["width"], p["depth"]
    if kind in ("conveyor", "sorter"):
        extra = 1.2 if kind == "sorter" else 0.0     # zsypy po obu stronach
        return p["length"], p["width"] + extra
    raise ValueError(kind)


def height(kind, p):
    if kind in ("rack_std", "rack_vna", "shuttle"):
        return p["levels"] * p["level_h"]
    return {"amr": 0.4, "amr_station": 1.1, "conveyor": p.get("height", 0.8),
            "sorter": 1.0}.get(kind, 1.0)


def pallet_positions(kind, p):
    """Liczba miejsc paletowych elementu (0 dla transportu/kompletacji)."""
    if kind in ("rack_std", "rack_vna"):
        return p["bays"] * p["pallets_per_bay"] * p["levels"]
    if kind == "shuttle":
        return p["channels"] * p["depth_pallets"] * p["levels"]
    return 0


def element_summary(kind, p):
    w, d = footprint(kind, p)
    return {"kind": kind, "label": ELEMENTS[kind]["label"], "width": round(w, 3),
            "depth": round(d, 3), "height": round(height(kind, p), 3),
            "area_m2": round(w * d, 2), "pallet_positions": pallet_positions(kind, p),
            "aisle_m": ELEMENTS[kind].get("aisle_m"),
            "throughput_h": ELEMENTS[kind].get("throughput_h")}


def block_rows(kind, rows, *, back_to_back=True, aisle=None, **overrides):
    """Rzędy bloku regałów: lista przesunięć „w głąb" [m] kolejnych rzędów.

    back_to_back=True: pary plecami do siebie (szczelina 0,1 m) i korytarz `aisle`
    (domyślnie wymagany przez sprzęt z katalogu) między parami."""
    p = params_for(kind, **overrides)
    _, d = footprint(kind, p)
    aisle = ELEMENTS[kind].get("aisle_m", 3.0) if aisle is None else aisle
    offsets, pos = [], 0.0
    for i in range(rows):
        offsets.append(round(pos, 3))
        gap = 0.1 if (back_to_back and i % 2 == 0) else aisle
        pos += d + gap
    return offsets


def _span(e, axis, along):
    """Rzut obrysu elementu na oś: (początek, koniec) [m]; along=True → szerokość."""
    w, d = footprint(e["kind"], e["params"])
    o = e["x"] * axis[0] + e["y"] * axis[1]
    return o, o + (w if along else d)


def check_aisles(elements):
    """Kontrola szerokości alejek między równoległymi elementami składowania.

    elements: dicty {kind, x, y, angle, params, label}. Dla każdej pary regałów o tym
    samym kącie, nakładających się wzdłuż osi, liczy prześwit między frontami; prześwit
    0,05–aisle_m (węższy niż wymaga sprzęt, a nie „plecami do siebie") = naruszenie."""
    import math

    racks = [e for e in elements if e["kind"] in RACK_KINDS]
    issues = []
    for i, a in enumerate(racks):
        for b in racks[i + 1:]:
            if abs(((a["angle"] - b["angle"]) + 180) % 360 - 180) > 1:
                continue
            t = math.radians(a["angle"] or 0)
            u_w, u_d = (math.cos(t), -math.sin(t)), (math.sin(t), math.cos(t))

            aw, bw = _span(a, u_w, along=True), _span(b, u_w, along=True)
            if min(aw[1], bw[1]) - max(aw[0], bw[0]) <= 0.2:     # nie leżą naprzeciw siebie
                continue
            ad, bd = _span(a, u_d, along=False), _span(b, u_d, along=False)
            gap = max(bd[0] - ad[1], ad[0] - bd[1])
            need = max(ELEMENTS[a["kind"]].get("aisle_m", 0), ELEMENTS[b["kind"]].get("aisle_m", 0))
            if gap < -0.01:
                issues.append({"type": "kolizja", "a": a.get("label"), "b": b.get("label"),
                               "gap_m": round(gap, 2), "need_m": need})
            elif 0.3 < gap < need - 0.01:
                issues.append({"type": "za wąska alejka", "a": a.get("label"), "b": b.get("label"),
                               "gap_m": round(gap, 2), "need_m": need})
    return issues


def variant_summary(elements, floor_w, floor_d):
    """Wskaźniki wariantu: miejsca paletowe, powierzchnia zabudowy, sprzęt, naruszenia."""
    by_kind, positions, area = {}, 0, 0.0
    for e in elements:
        s = element_summary(e["kind"], e["params"])
        k = by_kind.setdefault(e["kind"], {"label": s["label"], "count": 0, "pallet_positions": 0})
        k["count"] += 1
        k["pallet_positions"] += s["pallet_positions"]
        positions += s["pallet_positions"]
        area += s["area_m2"]
    floor = floor_w * floor_d
    return {"pallet_positions": positions, "built_area_m2": round(area, 1),
            "floor_area_m2": round(floor, 1),
            "positions_per_m2": round(positions / floor, 3) if floor else 0,
            "by_kind": by_kind, "aisle_issues": check_aisles(elements)}
