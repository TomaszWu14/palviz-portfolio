"""Metryki HU liczone z danych feedu z fallbackiem na master datę (Fala 1 roadmapy HU).

Jedno źródło prawdy dla: objętości/wagi palety (listy, mail gotowości, etykieta)
i sumowania jednostek pobrania (10 SZT + 3 OPZ = 1 KAR). Wszystko liczone BATCHOWO
per strona/lista — nigdy per wiersz (listy HU mają setki tysięcy pozycji).

ponytail: waga/objętość z master daty to szacunek (unit_weight×ilość); denormalizuj
na HU przy imporcie, jeśli zbiorcze listy staną się wolne.
"""


def _instr_by_product(product_ids):
    """{product_id: najnowsza aktywna instrukcja} jednym zapytaniem."""
    from .models import PalletizationInstruction
    out = {}
    if product_ids:
        for instr in (PalletizationInstruction.objects
                      .filter(product_id__in=product_ids, is_active=True)
                      .order_by("product_id", "-version")):
            out.setdefault(instr.product_id, instr)
    return out


def hu_metrics(hus):
    """{hu_id: {"weight_kg": float|None, "volume_m3": float|None, "estimated": bool}}.

    Priorytety: waga = suma wag pozycji z feedu, fallback unit_weight×ilość z instrukcji;
    objętość = wymiary palety z feedu (L×W×H), fallback unit_volume_m3×ilość, fallback
    objętość kartonu×liczba kartonów. None gdy braki nie pozwalają policzyć niczego."""
    from .models import HandlingUnitItem
    hus = list(hus)
    items = list(HandlingUnitItem.objects.filter(hu_id__in=[h.pk for h in hus])
                 .values("hu_id", "product_id", "base_qty", "weight_kg"))
    instrs = _instr_by_product({i["product_id"] for i in items if i["product_id"]})
    agg = {h.pk: {"weight": 0.0, "w_any": False, "vol": 0.0, "v_any": False,
                  "estimated": False} for h in hus}
    for i in items:
        a = agg[i["hu_id"]]
        instr = instrs.get(i["product_id"])
        qty = i["base_qty"] or 0
        if i["weight_kg"] is not None:
            a["weight"] += i["weight_kg"]
            a["w_any"] = True
        elif instr and instr.unit_weight and qty:
            a["weight"] += instr.unit_weight * qty
            a["w_any"] = True
            a["estimated"] = True
        if instr and qty:
            if instr.unit_volume_m3:
                a["vol"] += instr.unit_volume_m3 * qty
                a["v_any"] = True
            elif instr.carton_l and instr.carton_w and instr.carton_h \
                    and instr.pcs_per_carton:
                cartons = qty / instr.pcs_per_carton
                a["vol"] += (instr.carton_l * instr.carton_w * instr.carton_h / 1e6) * cartons
                a["v_any"] = True
                a["estimated"] = True
    out = {}
    for h in hus:
        a = agg[h.pk]
        # Wymiary palety z feedu wygrywają z sumą pozycji (fizyczna prawda o palecie).
        if h.length_cm and h.width_cm and h.height_cm:
            vol = round(h.length_cm * h.width_cm * h.height_cm / 1e6, 3)
            v_est = False
        else:
            vol = round(a["vol"], 3) if a["v_any"] else None
            v_est = a["estimated"]
        weight = h.weight_kg if h.weight_kg else (round(a["weight"], 1) if a["w_any"] else None)
        out[h.pk] = {"weight_kg": weight, "volume_m3": vol,
                     "estimated": a["estimated"] or v_est}
    return out


def summarize_units(items):
    """Suma pobrania HU w największych jednostkach: „1 KAR · 3 OPZ · 10 SZT".

    Per pozycja: ilość bazowa dzielona przez przeliczniki z master daty
    (pcs_per_carton → KAR, pcs_per_inner_pack → OPZ); reszta w jednostce bazowej.
    Pozycje bez przeliczników wykazywane w swojej jednostce z dopiskiem
    „(bez przelicznika)" — do uzupełnienia w Data Center."""
    items = list(items)
    instrs = _instr_by_product({it.product_id for it in items if it.product_id})
    buckets, no_conv = {}, {}

    def _add(d, unit, qty):
        if qty:
            d[unit] = d.get(unit, 0) + qty

    for it in items:
        qty = it.base_qty or 0
        unit = (it.base_unit or it.unit or "SZT").upper()
        instr = instrs.get(it.product_id)
        kar = float(instr.pcs_per_carton) if instr and (instr.pcs_per_carton or 0) > 1 else None
        opz = (float(instr.pcs_per_inner_pack)
               if instr and (getattr(instr, "pcs_per_inner_pack", 0) or 0) > 1 else None)
        if not qty:
            continue
        if kar is None and opz is None:
            _add(no_conv, unit, qty)
            continue
        rest = qty
        if kar:
            _add(buckets, "KAR", int(rest // kar))
            rest = rest % kar
        if opz:
            _add(buckets, "OPZ", int(rest // opz))
            rest = rest % opz
        _add(buckets, unit, round(rest, 2))

    order = {"KAR": 0, "OPZ": 1}
    parts = [f"{qty:g} {u}" for u, qty in
             sorted(buckets.items(), key=lambda kv: order.get(kv[0], 2))]
    text = " · ".join(parts)
    if no_conv:
        extra = " · ".join(f"{q:g} {u}" for u, q in sorted(no_conv.items()))
        text = (text + " · " if text else "") + f"{extra} (bez przelicznika)"
    return text or "—"
