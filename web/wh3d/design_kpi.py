"""Wskaźniki wariantu projektu magazynu (czysty Python — testowalny bez bazy i Blendera).

Poza pojemnością i alejkami z `design_catalog` liczymy:
  • drogę do miejsc paletowych — odległość prostokątna (jak jazda alejkami) od najbliższego
    punktu obsługi (dok, brama, stanowisko, stanowisko AMR) do frontu każdego boku regału,
    ważona liczbą miejsc; osobno dla strefy A = 20 % miejsc najbliżej punktów obsługi
    (tam trafiają szybko rotujące SKU przy slottingu ABC),
  • nominalną wydajność sprzętu z katalogu (zadania AMR/h, linie/h stanowisk, kartony/h).
Droga to miara porównawcza wariantów (ten sam wzór dla każdego), nie symulacja trasy.
"""
from .blender_route import rack_axes
from .design_catalog import ELEMENTS, RACK_KINDS, footprint, params_for, variant_summary

ANCHOR_FEATURES = ("dock", "gate", "station", "leader")
A_SHARE = 0.20


def _center(x, y, angle, w, d):
    u_w, u_d = rack_axes(angle)
    return (x + u_w[0] * w / 2 + u_d[0] * d / 2, y + u_w[1] * w / 2 + u_d[1] * d / 2)


def anchors(elements, features, floor_w, floor_d):
    """Punkty obsługi: doki/bramy/stanowiska z hali + stanowiska kompletacji z wariantu.
    Brak → środek przedniej krawędzi hali (y = głębokość)."""
    pts = [_center(f["x"], f["y"], f.get("angle") or 0, f.get("width") or 0, f.get("depth") or 0)
           for f in features if f.get("kind") in ANCHOR_FEATURES]
    for e in elements:
        if e["kind"] == "amr_station":
            w, d = footprint(e["kind"], e["params"])
            pts.append(_center(e["x"], e["y"], e["angle"], w, d))
    return pts or [(floor_w / 2, floor_d)]


def anchor_count(elements, features):
    """Liczba rzeczywistych punktów obsługi (0 → droga liczona od przodu hali)."""
    return (sum(1 for f in features if f.get("kind") in ANCHOR_FEATURES)
            + sum(1 for e in elements if e["kind"] == "amr_station"))


def storage_faces(elements):
    """(punkt przed frontem boku/kanału, liczba miejsc paletowych) dla elementów składowania."""
    out = []
    for e in elements:
        if e["kind"] not in RACK_KINDS:
            continue
        p, (x, y, a) = e["params"], (e["x"], e["y"], e["angle"])
        u_w, u_d = rack_axes(a)
        if e["kind"] == "shuttle":
            step, n, per = p["channel_width"], p["channels"], p["depth_pallets"] * p["levels"]
        else:
            step, n, per = p["bay_width"], p["bays"], p["pallets_per_bay"] * p["levels"]
        for i in range(n):
            along = (i + 0.5) * step
            out.append(((x + u_w[0] * along - u_d[0] * 0.9, y + u_w[1] * along - u_d[1] * 0.9), per))
    return out


def travel_stats(elements, features, floor_w, floor_d):
    pts = anchors(elements, features, floor_w, floor_d)
    rows = sorted((min(abs(fx - ax) + abs(fy - ay) for ax, ay in pts), n)
                  for (fx, fy), n in storage_faces(elements))
    total = sum(n for _, n in rows)
    if not total:
        return {"avg_m": None, "a_zone_avg_m": None, "max_m": None,
                "anchors": anchor_count(elements, features)}
    avg = sum(d * n for d, n in rows) / total
    a_left, a_sum = total * A_SHARE, 0.0
    a_cnt = 0.0
    for d, n in rows:
        take = min(n, a_left - a_cnt)
        if take <= 0:
            break
        a_sum += d * take
        a_cnt += take
    return {"avg_m": round(avg, 1), "a_zone_avg_m": round(a_sum / a_cnt, 1) if a_cnt else None,
            "max_m": round(rows[-1][0], 1), "anchors": anchor_count(elements, features)}


def equipment_capacity(elements):
    """Nominalna wydajność sprzętu wg katalogu, zsumowana per rodzaj elementu."""
    out = {}
    for e in elements:
        tph = ELEMENTS[e["kind"]].get("throughput_h")
        if tph:
            k = out.setdefault(e["kind"], {"label": ELEMENTS[e["kind"]]["label"], "count": 0,
                                           "throughput_h": 0})
            k["count"] += 1
            k["throughput_h"] += tph
    return out


def compute_kpi(elements, features, floor_w, floor_d):
    s = variant_summary(elements, floor_w, floor_d)
    s["travel"] = travel_stats(elements, features, floor_w, floor_d)
    s["equipment"] = equipment_capacity(elements)
    s["aisle_issue_count"] = len(s["aisle_issues"])
    return s


def rack_to_element(r):
    """Regał modelu magazynu (dict jak w scenie: width/depth/level_h/n_bays/n_levels) →
    element `rack_std` katalogu (miejsca w boku ≈ szerokość boku / 0,9 m)."""
    n_bays = max(1, r["n_bays"])
    bay_w = r["width"] / n_bays
    return {"kind": "rack_std", "label": f"{r['zone']}-{r['rack_id']}", "x": r["x"], "y": r["y"],
            "angle": r.get("angle") or 0.0,
            "params": params_for("rack_std", bays=n_bays, levels=max(1, r["n_levels"]),
                                 bay_width=round(bay_w, 3), depth=r["depth"], level_h=r["level_h"],
                                 pallets_per_bay=max(1, round(bay_w / 0.9)))}


def clean_elements(raw):
    """Walidacja elementów z pliku: znany rodzaj, liczby, parametry przez params_for.
    Zwraca (elements, errors) — błędne elementy są pomijane z opisem."""
    ok, errors = [], []
    for i, e in enumerate(raw if isinstance(raw, list) else []):
        try:
            kind = e["kind"]
            params = params_for(kind, **{k: v for k, v in (e.get("params") or {}).items()
                                         if k in ELEMENTS.get(kind, {}).get("params", {})})
            for k, v in params.items():
                if not isinstance(v, (int, float)) or v < 0:
                    raise ValueError(f"parametr {k}={v!r}")
            ok.append({"kind": kind, "label": str(e.get("label") or f"{kind}-{i + 1}")[:60],
                       "x": float(e["x"]), "y": float(e["y"]), "angle": float(e.get("angle") or 0),
                       "params": params})
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"element {i + 1}: {exc}")
    return ok, errors
