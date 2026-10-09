# core/ — shared kernel split into layers (base ◅ figures/xlsx/helpers ◅ packing).
# Re-exported wholesale by core/__init__.py so `from .core import *` is unchanged.
# No intra-core import needed — this module is self-contained (no `.base` symbols used).

def _stack_base_from_rows(rows, aisle_widths=None):
    """Derive (zone, aisle, stack) -> (base_col, base_row) grid positions straight from
    snapshot codes when no physical layout covers the snapshot.

    aisle_widths (opcjonalne): {aisle: krok_pz} — rozstaw głębokości per aleja (rząd regału
    + korytarz) z konfiguracji szerokości alej. Brak/None → stały AISLE_STEP dla każdej alei,
    czyli DOKŁADNIE dotychczasowe pozycje (zero regresji dla snapshotów bez konfiguracji).

    Dostrojone pod duże, wielostrefowe magazyny (np. B0 = 82 aleje × 237 stosów obok
    małych A0–A3): KLUCZ zawiera strefę — inaczej strefy o tej samej numeracji alei/stosów
    ('01') nakładałyby się. Każda strefa dostaje własne pasmo głębokości (przerwa między
    strefami); aleje = kolejne rzędy z korytarzem; stosy = GLOBALNE kolumny w strefie
    (ta sama numeracja stosu → ta sama kolumna we wszystkich alejach, więc regały się
    wyrównują w czytelną siatkę), z miejscem na kolumny A/B/C obok siebie."""
    by_zone = {}
    for r in rows:
        zone = r.get("zone") or (str(r.get("location_code") or "").split("-")[0])
        by_zone.setdefault(zone, {}).setdefault(r["aisle"], set()).add(r["stack"])

    def _skey(s):
        digits = "".join(c for c in str(s) if c.isdigit())
        return (0, int(digits), str(s)) if digits else (1, 0, str(s))

    COLS_PER_STACK = 3     # miejsce na col_idx 0/1/2 (A/B/C)
    AISLE_STEP = 2         # rząd alei + korytarz
    ZONE_GAP = 4           # wyraźna przerwa między strefami
    aw = aisle_widths or {}
    base = {}
    pz = 0
    for zone in sorted(by_zone):
        aisles = sorted(by_zone[zone])
        # globalne kolumny stosów w obrębie strefy → aleje wyrównane
        all_stacks = sorted({s for a in by_zone[zone].values() for s in a}, key=_skey)
        col_of = {s: i for i, s in enumerate(all_stacks)}
        # Kursor kumulatywny: krok per aleja z konfiguracji, domyślnie AISLE_STEP.
        # Bez aisle_widths każda aleja dokłada AISLE_STEP → tożsame z ai*AISLE_STEP.
        for aisle in aisles:
            row = pz
            for stack in by_zone[zone][aisle]:
                base[(zone, aisle, stack)] = (col_of[stack] * COLS_PER_STACK, row)
            pz += aw.get(aisle, AISLE_STEP)
        pz += ZONE_GAP
    return base


def _apply_aisle_widths(stack_base, aisle_widths, default=2):
    """Rozsuń rzędy (pz) gotowego stack_base wg szerokości alej — dla ścieżki LAYOUT,
    gdzie pozycje pochodzą z arkusza (nie z _stack_base_from_rows, który liczy szerokości
    sam). Dodaje EKSTRA odstęp PO każdej skonfigurowanej alei (proporcjonalnie do width
    ponad default), zachowując bazowe odstępy arkusza. Brak konfiguracji → bez zmian.

    aisle_widths: {aisle: krok_pz}. default = bazowy krok (AISLE_STEP)."""
    if not aisle_widths or not stack_base:
        return stack_base

    def _aisle(k):
        return k[1] if len(k) == 3 else k[0]

    # Pierwsza aleja napotkana w każdym rzędzie (rząd = jedna aleja w tych układach).
    row_aisle = {}
    for k, (_c, r) in stack_base.items():
        row_aisle.setdefault(r, _aisle(k))
    shift, extra = {}, 0
    for r in sorted(row_aisle):
        shift[r] = extra                       # offset skumulowany z alej WYŻEJ
        step = aisle_widths.get(row_aisle[r])
        if step:
            extra += max(0, step - default)    # ekstra przestrzeń po tej alei
    return {k: (c, r + shift[r]) for k, (c, r) in stack_base.items()}


def _apply_perpendicular_wings(stack_base, aisle_angles):
    """Przenieś aleje oznaczone kątem ~90° do OSOBNEGO regionu podłogi (na prawo od
    głównego bloku), zachowując ich wewnętrzny kształt. Renderer obróci ten blok o 90°
    wokół jego środka → skrzydło prostopadłe OBOK bloku 0° (układ L, jak w hali).

    Jedno spójne przekształcenie: tu tylko RELOKACJA (bez transpozycji), obrót geometrii
    robi render — inaczej podwójna rotacja rozjeżdża regały.

    Zwraca (nowy_stack_base, zbiór_alej_obróconych). Brak alej 90° → wejście bez zmian
    (regresja-safe: identyczny słownik gdy nic nie skonfigurowano)."""
    rotated = {a for a, ang in (aisle_angles or {}).items()
               if ang and round(abs(ang)) % 180 == 90}
    if not rotated or not stack_base:
        return stack_base, set()
    # Klucz bywa 3-elementowy (zone, aisle, stack) [code-derived] albo 2-elementowy
    # (aisle, stack) [layout]. Aleja to element [1] lub [0].
    def _aisle(k):
        return k[1] if len(k) == 3 else k[0]
    main = {k: v for k, v in stack_base.items() if _aisle(k) not in rotated}
    wing = {k: v for k, v in stack_base.items() if _aisle(k) in rotated}
    if not wing:
        return stack_base, set()
    COLS_PER_STACK = 3      # jak w _stack_base_from_rows (miejsce na A/B/C)
    ZONE_GAP = 4            # przerwa między blokiem a skrzydłem
    max_col = max((c for c, _r in main.values()), default=-COLS_PER_STACK)
    col0 = max_col + COLS_PER_STACK + ZONE_GAP
    wing_min_col = min(c for c, _r in wing.values())
    wing_min_row = min(r for _c, r in wing.values())
    dc = col0 - wing_min_col
    new = dict(main)
    for k, (c, r) in wing.items():
        new[k] = (c + dc, r - wing_min_row)        # przesuń w prawo, rzędy od 0
    return new, rotated


# ── Elementy hali (WarehouseHallFeature) — wspólne dla modułu B (model) i A (layout) ──
# Jedno źródło zamiast kopii w warehouse_model.py i warehouse_map.py (review: Duplicated Code).
HALL_FEATURE_COLORS = {
    "dock": "#64748b", "gate": "#0ea5e9", "corridor": "#94a3b8",
    "block_zone": "#a855f7", "returns": "#f43f5e", "leader": "#22c55e",
    "station": "#eab308", "other": "#6b7280",
}


def hall_feature_kinds():
    from ...models import WarehouseHallFeature
    return dict(WarehouseHallFeature.KIND_CHOICES)


def hall_feature_dict(f):
    """Element hali → dict do renderu (kolor rozwiązany, etykieta z kind)."""
    kinds = hall_feature_kinds()
    return {
        "id": f.pk, "kind": f.kind,
        "kind_label": kinds.get(f.kind, f.kind),
        "label": f.label or kinds.get(f.kind, ""),
        "x": f.x_m, "y": f.y_m, "width": f.width_m, "depth": f.depth_m,
        "angle": f.angle_deg,
        "color": (f.color_hex or HALL_FEATURE_COLORS.get(f.kind, "#6b7280")),
        "zone_code": f.zone_code,
    }


def save_hall_features(request, owner_field, owner_obj):
    """Zapis edytowalnej tabeli elementów hali z równoległych list POST + deleted_ids.
    owner_field: 'model' albo 'layout' (który FK ustawić dla nowych wierszy)."""
    from django.db import transaction
    from ...models import WarehouseHallFeature
    valid = dict(WarehouseHallFeature.KIND_CHOICES)
    P = request.POST
    row_ids = P.getlist("row_id")
    kinds = P.getlist("kind")
    labels, zone_codes = P.getlist("label"), P.getlist("zone_code")
    xs, ys = P.getlist("x_m"), P.getlist("y_m")
    ws, ds = P.getlist("width_m"), P.getlist("depth_m")
    angles, colors, notes = P.getlist("angle_deg"), P.getlist("color_hex"), P.getlist("notes")
    deleted = [d for d in (P.get("deleted_ids") or "").split(",") if d.strip().isdigit()]

    def _f(lst, i, default=0.0):
        try:
            return float(lst[i]) if i < len(lst) and lst[i] != "" else default
        except (ValueError, TypeError):
            return default

    features_qs = owner_obj.features
    with transaction.atomic():
        if deleted:
            features_qs.filter(pk__in=deleted).delete()
        existing = {f.pk: f for f in features_qs.all()}
        for i in range(len(kinds)):
            rid = row_ids[i] if i < len(row_ids) else ""
            kind = kinds[i] if kinds[i] in valid else "other"
            feat = existing.get(int(rid)) if rid.isdigit() else None
            if feat is None:
                feat = WarehouseHallFeature(**{owner_field: owner_obj})
            feat.kind = kind
            feat.label = (labels[i] if i < len(labels) else "")[:100]
            feat.zone_code = (zone_codes[i] if i < len(zone_codes) else "").strip().upper()[:20]
            feat.x_m, feat.y_m = _f(xs, i), _f(ys, i)
            feat.width_m, feat.depth_m = _f(ws, i, 2), _f(ds, i, 2)
            feat.angle_deg = _f(angles, i)
            feat.color_hex = (colors[i] if i < len(colors) else "").strip()[:7]
            feat.notes = (notes[i] if i < len(notes) else "")[:200]
            feat.save()


def _location_fit(loc, carton_l, carton_w, carton_h,
                  unit_weight=0, pcs_per_carton=0, carton_tare=0, with_pallet=None):
    """Lightweight carton/pallet fit into a warehouse location — no figures.

    Mirrors the geometry of `_fig_location_3d` so inline checks (e.g. the calculator's
    location checkboxes) agree with the full 3D simulator. Returns a verdict dict."""
    PALLET_L, PALLET_W = 120, 80
    if with_pallet is None:
        with_pallet = bool(loc.is_pallet_location)
    LW = loc.depth_cm        # x axis (depth)
    WW = loc.width_cm        # y axis (clearance width)
    usable_h = loc.usable_height_cm
    avail_h = usable_h + (loc.pallet_height_cm if not with_pallet else 0)

    if with_pallet:
        foot_l, foot_w = PALLET_L, PALLET_W
        fits_w = PALLET_W <= WW
        fits_d = PALLET_L <= LW
    else:
        foot_l, foot_w = LW, WW
        fits_w = carton_w <= WW
        fits_d = carton_l <= LW
    fits_h = carton_h <= avail_h

    n_x = max(0, int(foot_l // carton_l)) if carton_l > 0 else 0
    n_y = max(0, int(foot_w // carton_w)) if carton_w > 0 else 0
    n_layers = max(0, int(avail_h // carton_h)) if carton_h > 0 else 0
    per_layer = n_x * n_y
    total_cartons = per_layer * n_layers
    pieces = total_cartons * (pcs_per_carton or 0)
    weight = total_cartons * ((pcs_per_carton or 0) * (unit_weight or 0) + (carton_tare or 0))

    usable_vol = WW * LW * (usable_h if with_pallet else avail_h)
    used_vol = total_cartons * carton_l * carton_w * carton_h
    fill_pct = round(100 * used_vol / usable_vol, 1) if usable_vol else 0.0
    max_w = loc.max_load_kg or 0
    over_weight = max_w > 0 and weight > max_w
    over_w = max(0, (PALLET_W if with_pallet else carton_w) - WW)
    over_d = max(0, (PALLET_L if with_pallet else carton_l) - LW)
    fits = fits_w and fits_d and fits_h and not over_weight

    # Single source for the verdict label/colour so templates render one span.
    if fits:
        verdict, verdict_color = "✓ Mieści się", "#15803d"
    elif not fits_w or not fits_d:
        verdict, verdict_color = "✗ Obrys za duży", "#b91c1c"
    elif not fits_h:
        verdict, verdict_color = "✗ Za wysokie", "#b91c1c"
    elif over_weight:
        verdict, verdict_color = "✗ Przekr. wagi", "#b91c1c"
    else:
        verdict, verdict_color = "✗ Nie mieści", "#b91c1c"

    return {
        "loc": loc, "with_pallet": with_pallet,
        "fits": fits, "verdict": verdict, "verdict_color": verdict_color,
        "fits_w": fits_w, "fits_d": fits_d, "fits_h": fits_h,
        "per_layer": per_layer, "n_layers": n_layers, "total_cartons": total_cartons,
        "n_x": n_x, "n_y": n_y,
        "pieces": pieces, "weight": round(weight, 1), "fill_pct": fill_pct,
        "over_w": over_w, "over_d": over_d, "max_w": max_w, "over_weight": over_weight,
        "avail_h": avail_h, "usable_h": usable_h,
    }


__all__ = [
    'HALL_FEATURE_COLORS', '_apply_aisle_widths', '_apply_perpendicular_wings',
    '_location_fit', '_stack_base_from_rows', 'hall_feature_dict', 'hall_feature_kinds',
    'save_hall_features',
]
