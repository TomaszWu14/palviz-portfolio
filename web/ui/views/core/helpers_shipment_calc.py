# core/ — shared kernel split into layers (base ◅ figures/xlsx/helpers ◅ packing).
# Re-exported wholesale by core/__init__.py so `from .core import *` is unchanged.
from .base import _PRODUCT_COLORS, math

_PALLET_HEIGHTS = list(range(150, 261, 10))
_DEFAULT_PALLET_HEIGHT = 180
# Per-pallet weight cap and default stowage efficiency are configurable (env/settings) so
# they can be calibrated from real shipment outcomes instead of staying hardcoded guesses.
from django.conf import settings as _dj_settings
_MAX_PALLET_WEIGHT_KG = int(getattr(_dj_settings, "PALLET_MAX_WEIGHT_KG", 1000) or 1000)
_DEFAULT_STOW_EFF_PCT = int(getattr(_dj_settings, "DEFAULT_STOWAGE_EFFICIENCY_PCT", 80) or 80)


def _shipment_pallet_height(shipment):
    """Wysokość ładunku palety [cm] do liczenia palet wysyłki (BIZ-007): wybrana na wysyłce,
    inaczej domyślna. Jedno źródło dla transportu (wycena, KPI, CMR…) i generowania HU."""
    h = getattr(shipment, "selected_pallet_height_cm", None)
    return h if h and h > 0 else _DEFAULT_PALLET_HEIGHT

_DEFAULT_HEIGHTS = [180, 225]   # two scenarios: 1.8 m (low truck) & 2.25 m (as built); ±10 cm
_PAZ_BUILD_HEIGHT_CM = 225      # how tall PAZ pallets are built; at ≥ this a PAZ line stays
                                # a ready full pallet, below it it's broken into cartons


def _latest_instructions(product_ids):
    """{product_id: najnowsza aktywna PalletizationInstruction} — jedno zapytanie."""
    from ...models import PalletizationInstruction as _PI
    out: dict = {}
    for _i in (_PI.objects.filter(product_id__in=product_ids, is_active=True)
               .order_by("product_id", "-version").select_related("product", "carton")):
        out.setdefault(_i.product_id, _i)  # first = highest version
    return out


_PALLET_L, _PALLET_W = 120, 80  # cm — EU pallet footprint used by the shipment estimate
_REF_DIVISOR = 5000             # courier reference divisor (per-carrier quotes use their own)


def _resolve_stow_eff(shipment, stow_eff):
    """Efektywność układania [%]: jawna albo zapisana na przesyłce, przycięta do 30–100."""
    if stow_eff is None:
        stow_eff = getattr(shipment, "stowage_efficiency_pct", _DEFAULT_STOW_EFF_PCT) or _DEFAULT_STOW_EFF_PCT
    return max(30, min(100, int(stow_eff)))


def _shipment_lines(shipment):
    """Linie przesyłki z produktem — z prefetchu listy (PERF-002) albo jednym zapytaniem."""
    # One query for the lines (product joined) instead of two — a values_list for the
    # ids AND a second select_related pass. product_ids are derived in Python below.
    # Lista przesyłek prefetchuje linie (Prefetch z tym samym select_related/order_by)
    # i podaje instr_map dla całej strony — wtedy zero zapytań per wiersz (PERF-002).
    _pf = getattr(shipment, "_prefetched_objects_cache", {})
    if "lines" in _pf:
        return list(_pf["lines"])
    return list(shipment.lines.select_related("product").order_by("order", "id"))


def _layout_cpp(instr):
    """Kartony na paletę z wybranego układu instrukcji (0 = nieznane: brak układu / 0)."""
    layout = instr.get_selected_layout()
    return (layout.get("cartons_per_pallet") or 0) if layout else 0


def _instr_skip_reason(instr, line=None):
    """Powód pominięcia linii (None = instrukcja nadaje się do liczenia)."""
    if not instr:
        return "brak instrukcji paletyzacji — pominięto"
    # Zerowy wymiar kartonu (import SAP/MARM bez wymiarów, ręczny wpis) trafiłby do
    # dzielenia w packerze 3D (_per_layer: PL//cl) → ZeroDivisionError 500. Traktuj jak
    # brak instrukcji: twarde ostrzeżenie + pominięcie, zamiast wywalać wycenę.
    if not (instr.carton_l > 0 and instr.carton_w > 0 and instr.carton_h > 0):
        return "zerowe wymiary kartonu w instrukcji — pominięto"
    # Linia w paletach bez układu (layouts=[] albo cartons_per_pallet=0): nie wiadomo, ile
    # kartonów jest na palecie. Dawniej `or 1` liczyło 1 karton/paletę → objętość, waga
    # i liczba palet mocno zaniżone. Jak brak instrukcji: twarde ostrzeżenie + pominięcie.
    if line is not None and line.unit == "pal" and not _layout_cpp(instr):
        return "brak układu palety (kartonów na paletę) w instrukcji — pominięto"
    return None


def _line_cartons(line, ppc, cpp):
    """Exact carton-equivalent (may be fractional) of a line in its ordered unit."""
    if line.unit == "szt":
        return line.quantity / ppc
    if line.unit == "kar":
        return float(line.quantity)
    return line.quantity * cpp  # pal


def _line_volume(instr, cartons, ppc, carton_vol_m3):
    # Volume: prefer the SAP MARM per-unit (per-OP) volume when stored — the carton
    # L×W×H overstates it when the box carries void (e.g. 75.76 m³ read as 80.28). Fall
    # back to carton dims. Either way use the EXACT (fractional) carton-equivalent, not
    # the rounded-up whole-box count.
    if instr.unit_volume_m3:
        return round(cartons * ppc * instr.unit_volume_m3, 4)   # pieces × per-OP volume
    return round(cartons * carton_vol_m3, 4)


def _max_layers(product):
    # Stackability cap: non-stackable → 1 layer; else the product's limit (0 = none).
    if not getattr(product, "stackable", True):
        return 1
    return getattr(product, "max_stack_layers", 0) or 0


def _calc_line(line, instr, color):
    """Pozycja wyniku dla jednej linii z poprawną instrukcją."""
    product = line.product
    # Linie „pal” bez układu są pomijane wcześniej (_instr_skip_reason); dla „kar”/„szt”
    # cpp służy tylko informacyjnie, więc nieznane (0) raportujemy jak dotąd jako 1.
    cpp = _layout_cpp(instr) or 1
    ppc = instr.pcs_per_carton or 1          # guard a stored 0 once, use everywhere
    cartons = _line_cartons(line, ppc, cpp)
    n_cartons = math.ceil(cartons)           # whole boxes: count, weight, 3D packing

    carton_vol_m3 = (instr.carton_l * instr.carton_w * instr.carton_h) / 1_000_000
    carton_weight = ppc * instr.unit_weight + instr.carton_tare
    return {
        "line": line,
        "product": product,
        "n_cartons": n_cartons,
        "carton_dims": (instr.carton_l, instr.carton_w, instr.carton_h),
        "carton_vol_m3": round(carton_vol_m3, 5),
        "carton_weight_kg": round(carton_weight, 3),
        "volume_m3": _line_volume(instr, cartons, ppc, carton_vol_m3),
        "weight_kg": round(n_cartons * carton_weight, 2),
        "color": color,
        # Unit hierarchy: a line ordered in PAZ (whole pallets) is a ready full pallet
        # at the warehouse build height; below it the packer must break it into cartons.
        "unit": line.unit,
        "paz_pallets": (math.ceil(line.quantity) if line.unit == "pal" else 0),
        "cartons_per_pallet": cpp,
        "max_layers": _max_layers(product),
    }


def _collect_lines(lines, instr_map):
    """Przelicz linie → (lines_calc, vol, weight, cartons, errors, missing_products)."""
    lines_calc, errors = [], []
    missing_products = []   # lines dropped for lack of a palletization instruction
    color_map = {}
    total_vol_m3 = total_weight_kg = 0.0
    total_cartons = 0
    for line in lines:
        product = line.product
        instr = instr_map.get(product.pk)
        reason = _instr_skip_reason(instr, line)
        if reason:
            errors.append(f"{product.code}: {reason}")
            # Track separately: a dropped line means the quote UNDER-counts goods (volume,
            # weight, pallets) — this must be surfaced as a hard warning, not a soft note.
            missing_products.append({"code": product.code, "name": product.name,
                                     "quantity": line.quantity, "unit": line.unit})
            continue
        if product.pk not in color_map:
            color_map[product.pk] = _PRODUCT_COLORS[len(color_map) % len(_PRODUCT_COLORS)]
        lc = _calc_line(line, instr, color_map[product.pk])
        total_vol_m3 += lc["volume_m3"]
        total_weight_kg += lc["weight_kg"]
        total_cartons += lc["n_cartons"]
        lines_calc.append(lc)
    return lines_calc, total_vol_m3, total_weight_kg, total_cartons, errors, missing_products


def _pallet_weight_cap(shipment):
    # A linked customer's max pallet weight is a hard limit — cap the per-pallet weight at
    # the stricter of the global default and the customer's, so it feeds by_weight and the
    # 3D packer (a low cap → more, lighter pallets).
    _cust = getattr(shipment, "customer", None)
    cap = _MAX_PALLET_WEIGHT_KG
    if _cust and getattr(_cust, "max_pallet_weight_kg", None):
        cap = min(cap, _cust.max_pallet_weight_kg)
    return cap


def _pallet_anchors(shipment):
    # Authoritative pallet anchors, strongest first: the real SAP HU count (what physically
    # shipped) overrides the count the warehouse committed to, which overrides the volumetric
    # guess. Either, when set, pins the headline to an agreed reality instead of the estimate.
    actual_hu = getattr(shipment, "actual_hu_count", None) or 0
    wh_pallets = getattr(shipment, "warehouse_pallets", None) or 0
    return actual_hu, wh_pallets, actual_hu or wh_pallets


def _ceil_div(total, per):
    return math.ceil(total / per) if total else 0


def _drawn_filled(three):
    """Ile palet scena 3D faktycznie zapełniła (0 = brak sceny)."""
    if not (three and three.get("pallets")):
        return 0
    return sum(1 for p in three["pallets"] if not p.get("empty") and p.get("boxes"))


def _scenario_three(lines_calc, sc_h, weight_cap, est, with_packing):
    # Build the 3D layout AIMED at the headline count (the picture is an illustration).
    # The shipment list passes with_packing=False (no 3D) and just shows the estimate.
    if not with_packing:
        return None
    from .helpers_shipment_three import _build_shipment_three_data  # cykl: leniwie
    return _build_shipment_three_data({"lines": lines_calc}, max_h=sc_h, max_w=weight_cap,
                                      target_bins=est)


def _build_scenario(sc_h, *, eff, total_vol_m3, total_weight_kg, weight_cap,
                    actual_hu, wh_pallets, pallet_anchor, lines_calc, with_packing):
    """Jeden scenariusz paletowy dla wysokości ładunku `sc_h` [cm]."""
    pallet_vol_m3 = round(_PALLET_L * _PALLET_W * sc_h / 1_000_000, 4)
    usable_vol_m3 = pallet_vol_m3 * eff      # realistic cube after stowage losses
    by_vol = _ceil_div(total_vol_m3, usable_vol_m3)
    by_weight = _ceil_div(total_weight_kg, weight_cap)
    # Efficiency-calibrated volumetric/weight estimate. This is the SLIDER's output:
    # raising the efficiency raises usable cube → fewer pallets. It is the headline
    # UNLESS SAP told us the real HU count, which then anchors the quote to reality.
    est_theoretical = max(by_vol, by_weight)
    est = pallet_anchor or est_theoretical
    # Optimistic count at +5pp efficiency. If packing a touch tighter saves EXACTLY one
    # pallet, the load is borderline → expose a range (e.g. "6–7") so the quote covers
    # both; otherwise a single figure.
    by_vol_opt = _ceil_div(total_vol_m3, pallet_vol_m3 * min(1.0, eff + 0.05))
    n_low = pallet_anchor or max(by_vol_opt, by_weight)
    three = _scenario_three(lines_calc, sc_h, weight_cap, est, with_packing)
    # Headline / billed count = the calibrated estimate, NOT the loose 3D draw. Without
    # a true interlocking 3D packer the FFD layout leaves horizontal gaps; letting it
    # drive the count (a) inflated the quote above the volumetric reality (3 vs the real
    # 2) and (b) pinned the count to a value the efficiency slider couldn't move (the
    # draw is efficiency-independent). The headline is therefore the estimate — anchored
    # to the SAP HU count when known, else efficiency-driven — and the 3D scene is a
    # non-binding illustration whose own drawn count is surfaced separately.
    drawn_filled = _drawn_filled(three)
    n_pallets = est
    # Precise modelled breakdown: how many pallets actually carry goods vs are reserved
    # slack (the hand-stacking buffer). Lets the UI show an exact count, not a range.
    n_filled = drawn_filled or n_pallets
    # fill_pct = real goods volume vs the nominal pallet cube (shows the empty space)
    fill_pct = round(100 * total_vol_m3 / (n_pallets * pallet_vol_m3), 1) if n_pallets else 0
    return {
        "label": f"{sc_h / 100:.2f} m".replace(".", ","),
        "max_h_cm": sc_h,
        "max_w_kg": weight_cap,
        "n_filled": n_filled,
        "n_slack": max(0, n_pallets - n_filled),
        "pallet_vol_m3": pallet_vol_m3,
        "usable_vol_m3": round(usable_vol_m3, 4),
        "n_pallets": n_pallets,
        "n_pallets_low": n_low,
        "n_range": (not pallet_anchor) and (n_pallets - n_low == 1),
        "est_theoretical": est_theoretical,   # slider-driven volumetric estimate
        "actual_hu": actual_hu,               # SAP physical HU count (0 = unknown)
        "wh_pallets": wh_pallets,             # warehouse-committed count (0 = none)
        "pallet_anchor": pallet_anchor,       # whichever authoritative count pinned it
        "model_pallets": drawn_filled or n_pallets,   # pallets the 3D illustration drew
        "by_vol": by_vol,
        "by_weight": by_weight,
        "ldm": round(n_pallets * _PALLET_L / 100 / 2.4, 2),  # loading meters (2.4m truck width)
        "fill_pct": fill_pct,
        "three": three,          # reused by the detail view → no second packing pass
    }


def _scenario_heights(heights):
    # Pallet scenarios — one per selectable load HEIGHT. Two editable heights by
    # default (1.8 and 2.2 m). Weight cap is height-independent.
    # Wartości zaokrąglamy (199.7 → 200, nie obcinamy do 199); nieliczbowe/niedodatnie
    # pomijamy zamiast rzucać ValueError (→ 500). Nic poprawnego → domyślne wysokości.
    return sorted({h for h in map(_parse_height, heights or _DEFAULT_HEIGHTS) if h}) or _DEFAULT_HEIGHTS


def _parse_height(value):
    """Wysokość scenariusza [cm] jako int (zaokrąglona) albo None, gdy niepoprawna."""
    try:
        h = round(float(str(value).replace(",", ".")))
    except (TypeError, ValueError, OverflowError):
        return None
    return h if h > 0 else None


def _calc_shipment_data(shipment, stow_eff=None, heights=None, with_packing=True,
                        instr_map=None):
    """Return full calculation dict for a shipment.

    `stow_eff` is the stowage-efficiency percentage (30–100): the realistic share of
    pallet cube usable when a human hand-stacks mixed SKUs. It shrinks the usable
    pallet volume so the pallet count (and therefore transport quotes) isn't an
    over-optimistic "perfect-cube" estimate. Defaults to the shipment's saved value."""
    stow_eff = _resolve_stow_eff(shipment, stow_eff)
    lines = _shipment_lines(shipment)
    # Pre-fetch latest active instruction per product to avoid N+1 queries
    _instr_map = (instr_map if instr_map is not None
                  else _latest_instructions(list({l.product_id for l in lines})))
    (lines_calc, total_vol_m3, total_weight_kg, total_cartons,
     errors, missing_products) = _collect_lines(lines, _instr_map)
    total_vol_m3 = round(total_vol_m3, 4)
    total_weight_kg = round(total_weight_kg, 2)

    pallet_weight_cap = _pallet_weight_cap(shipment)
    actual_hu, wh_pallets, pallet_anchor = _pallet_anchors(shipment)
    scenarios = [
        _build_scenario(sc_h, eff=stow_eff / 100.0, total_vol_m3=total_vol_m3,
                        total_weight_kg=total_weight_kg, weight_cap=pallet_weight_cap,
                        actual_hu=actual_hu, wh_pallets=wh_pallets,
                        pallet_anchor=pallet_anchor, lines_calc=lines_calc,
                        with_packing=with_packing)
        for sc_h in _scenario_heights(heights)
    ]

    # Courier scenario (reference divisor 5000; per-carrier quotes use their own divisor)
    dim_weight_kg = round(total_vol_m3 * 1_000_000 / _REF_DIVISOR, 2)
    return {
        "lines": lines_calc,
        "total_vol_m3": total_vol_m3,
        "total_weight_kg": total_weight_kg,
        "total_cartons": total_cartons,
        "scenarios": scenarios,
        "stow_eff_pct": stow_eff,
        "pallet_weight_cap_kg": pallet_weight_cap,
        # customer's max pallet weight is stricter than default
        "weight_capped": pallet_weight_cap < _MAX_PALLET_WEIGHT_KG,
        "is_small": total_weight_kg <= 70 and total_vol_m3 <= 0.30,
        "dim_weight_kg": dim_weight_kg,
        "billed_kg": round(max(total_weight_kg, dim_weight_kg), 2),
        "errors": errors,
        "missing_products": missing_products,
        "has_missing": bool(missing_products),
    }


def _build_vehicle_pallet_load(calc, vehicle, max_h):
    """Arrange the BUILT pallets onto the floor of a vehicle/container and return 3D data
    — "palety załadowane w naczepie". Reuses the dense pallet packer, then places each
    120×80 pallet on the vehicle floor grid (120 cm along the length, 80 cm across)."""
    L, W, H = int(vehicle["len"]), int(vehicle["wid"]), int(vehicle["height"])
    PL, PW, BASE = 120, 80, 14
    from .helpers_shipment_three import _build_shipment_three_data  # cykl: leniwie
    three = _build_shipment_three_data(calc, max_h=max_h, max_w=_MAX_PALLET_WEIGHT_KG)
    if not three or not three.get("pallets"):
        return None
    pallets = [p for p in three["pallets"] if p.get("boxes")]
    if not pallets:
        return None

    across_n = max(1, W // PW)      # pallets across the width (80 cm side)
    along_n = max(1, L // PL)       # pallets along the length (120 cm side)
    slots = across_n * along_n

    vehicles = {}
    total_vol = 0.0
    for i, p in enumerate(pallets):
        vi, s = divmod(i, slots)
        row, col = divmod(s, across_n)          # row = along length, col = across width
        sx = -L / 2 + (row + 0.5) * PL
        sz = -W / 2 + (col + 0.5) * PW
        v = vehicles.setdefault(vi, {"boxes": [], "decks": [], "h": 0.0})
        for b in p["boxes"]:
            v["boxes"].append({"dims": b["dims"], "color": b["color"], "label": b.get("label", ""),
                               "pos": [round(b["pos"][0] + sx, 1), b["pos"][1], round(b["pos"][2] + sz, 1)]})
            d = b["dims"]
            total_vol += d[0] * d[1] * d[2]
        v["decks"].append([round(sx, 1), round(sz, 1)])
        v["h"] = max(v["h"], p["height_cm"])

    containers = [{"boxes": v["boxes"], "decks": v["decks"], "height_cm": round(v["h"], 1),
                   "empty": False, "mixed": True}
                  for _, v in sorted(vehicles.items())]
    cube = len(containers) * L * W * H
    return {
        "type": "container", "mode": "pallets", "vehicle": vehicle["name"], "vehicle_key": vehicle["key"],
        "L": L, "W": W, "H": H, "pallet_l": PL, "pallet_w": PW, "pallet_base": BASE,
        "n_containers": len(containers), "rendered": len(containers),
        "n_pallets": len(pallets), "slots_per_vehicle": slots,
        "fill_pct": round(100 * total_vol / cube, 1) if cube else 0.0,
        "total_cartons": sum(len(c["boxes"]) for c in containers),
        "containers": containers,
    }


def _build_container_load(calc, vehicle, max_w=None, render_cap=2500):
    """Pack the goods LOOSE (no pallets) straight into a vehicle/container of the given
    internal dimensions — "ile wejdzie luzem do TIR-a/kontenera". Greedy fullest-fit
    layer flow + gravity settle, capped by the payload weight. Returns 3D data for the
    container viewer plus how many vehicles are needed and the cube fill."""
    L, W, H = int(vehicle["len"]), int(vehicle["wid"]), int(vehicle["height"])
    mw = vehicle["payload"] if max_w is None else max_w
    if not calc.get("lines"):
        return None

    cartons = []
    for lc in calc["lines"]:
        l, w, h = lc["carton_dims"]
        wkg = lc.get("carton_weight_kg") or 1.0
        color, label = lc["color"], lc["product"].code
        for _ in range(int(lc["n_cartons"])):
            cartons.append((int(l), int(w), int(h), color, label, wkg))
    if not cartons:
        return None
    cartons.sort(key=lambda c: (-c[2], -c[5], c[4]))   # tall, then heavy, then SKU

    def _new():
        return {"boxes": [], "base": 0.0, "x": 0.0, "z": 0.0,
                "row_depth": 0.0, "layer_h": 0.0, "wt": 0.0, "vol": 0.0}

    def _coords(b, l, w):
        base, x, z, rd, lh = b["base"], b["x"], b["z"], b["row_depth"], b["layer_h"]
        if x + l > L:                       # current row full
            if z + rd + w > W:              # floor full → next layer up
                base += lh; x = z = rd = lh = 0.0
            else:                           # next row in the same layer
                z += rd; x = 0.0; rd = 0.0
        return base, x, z, rd, lh

    def _apply(b, l, w, h, color, label, wkg, base, x, z, rd, lh):
        b["base"], b["x"], b["z"] = base, x + l, z
        b["row_depth"], b["layer_h"] = max(rd, w), max(lh, h)
        b["wt"] += wkg; b["vol"] += l * w * h
        b["boxes"].append({"dims": [l, w, h], "color": color, "label": label,
                           "pos": [round(x + l / 2 - L / 2, 1), round(base + h / 2, 1),
                                   round(z + w / 2 - W / 2, 1)]})

    bins = []
    for (l, w, h, color, label, wkg) in cartons:
        # The packer lays l along the length and w along the width and does NOT rotate, so
        # orient the carton to fit the width, then reject only what truly can't fit (the old
        # `min(l,w) > max(L,W)` let cartons wider than W through, overflowing the wall).
        if w > W and l <= W:
            l, w = w, l
        if l > L or w > W or h > H:
            continue
        best = None
        for b in bins:
            base, x, z, rd, lh = _coords(b, l, w)
            if base + h <= H + 0.1 and (not mw or b["wt"] + wkg <= mw + 1e-6):
                if best is None or b["vol"] > best[0]["vol"]:    # fullest-fit → dense
                    best = (b, base, x, z, rd, lh)
        if best is None:
            b = _new(); bins.append(b)
            best = (b,) + _coords(b, l, w)
        _apply(best[0], l, w, h, color, label, wkg, *best[1:])

    if not bins:
        return None
    total_vol = sum(b["vol"] for b in bins)
    total_wt = sum(b["wt"] for b in bins)
    out = []
    for b in bins[:render_cap]:
        boxes = b["boxes"]
        _settle_boxes(boxes, 0.0)
        top = max((bb["pos"][1] + bb["dims"][2] / 2) for bb in boxes)
        for bb in boxes:
            bb["mix"] = True
        out.append({"boxes": boxes, "height_cm": round(top, 1), "mixed": True, "empty": False})
    cube = len(bins) * L * W * H
    cap_kg = len(bins) * mw if mw else 0
    return {
        "type": "container", "vehicle": vehicle["name"], "vehicle_key": vehicle["key"],
        "L": L, "W": W, "H": H, "n_containers": len(bins), "rendered": len(out),
        "fill_pct": round(100 * total_vol / cube, 1) if cube else 0.0,
        # Weight utilisation: how much of the payload limit is used (the other half of
        # "how full" — a light, bulky load fills cube but not weight, and vice-versa).
        "weight_pct": round(100 * total_wt / cap_kg, 1) if cap_kg else None,
        "total_weight_kg": round(total_wt, 1),
        "total_cartons": len(cartons), "max_w_kg": mw,
        "containers": out,
    }


def _settle_boxes(boxes, base):
    """Drop every box straight down until it rests on the pallet base or on top of a
    lower box whose footprint it overlaps — removes mid-air gaps so no carton floats.
    dims = [x-extent, z-extent, height]; pos = box centre. Mutates+returns `boxes`."""
    boxes.sort(key=lambda b: b["pos"][1] - b["dims"][2] / 2)   # lowest first
    settled = []
    for bx in boxes:
        hw, hd, h = bx["dims"][0] / 2, bx["dims"][1] / 2, bx["dims"][2]
        cx, cz = bx["pos"][0], bx["pos"][2]
        rest = base
        for s in settled:
            # footprints overlap on both floor axes (epsilon: mere edge contact doesn't
            # count as support, so a box only rests on what's genuinely beneath it)
            if (abs(cx - s["pos"][0]) < hw + s["dims"][0] / 2 - 0.1 and
                    abs(cz - s["pos"][2]) < hd + s["dims"][1] / 2 - 0.1):
                rest = max(rest, s["pos"][1] + s["dims"][2] / 2)
        bx["pos"][1] = round(rest + h / 2, 1)
        settled.append(bx)
    return boxes



__all__ = [
    '_DEFAULT_PALLET_HEIGHT', '_MAX_PALLET_WEIGHT_KG', '_PALLET_HEIGHTS',
    '_build_container_load', '_build_vehicle_pallet_load', '_calc_shipment_data',
    '_shipment_pallet_height',
]
