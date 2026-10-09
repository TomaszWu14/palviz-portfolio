"""Budowa pojedynczych poziomów hierarchii opakowań (karty + `three_data` dla
renderera three.js). Wydzielone z `hierarchy.build_hierarchy` (CODE-001) — jedna
funkcja na poziom; orkiestracja, KPI palety i alerty zostają w `ui/hierarchy.py`.

Kolejność kluczy w słownikach i w JSON-ie `three_data` jest częścią kontraktu
(przypięta testem `test_hierarchy_characterization`) — nie przestawiać."""
import json


def _dims(l, w, h):
    """Numeryczne wymiary + objętość (m³) poziomu — do karty materiału (obok stringa dims)."""
    return {"l_cm": l, "w_cm": w, "h_cm": h,
            "vol_m3": round(l * w * h / 1_000_000, 5) if (l and w and h) else None}


def _art_list(obj, with_artwork=True):
    """Grafiki opakowania (as_dict) albo [] — gdy brak obiektu lub pominięte."""
    return [a.as_dict() for a in obj.artworks.all()] if (obj and with_artwork) else []


def _glb_url(obj, field="glb_model"):
    """URL modelu 3D (.glb) albo "" — gdy brak obiektu lub pliku."""
    f = getattr(obj, field) if obj else None
    return f.url if f else ""


def packaging_refs(instr):
    """→ (carton_obj, ip, ppc, pcs_ip): karton z bazy, OPZ, OPZ/karton, szt/OPZ.
    Fallbacki: instrukcja → karton z bazy → InnerPack.units_per_pack."""
    carton_obj = instr.carton if instr else None
    if not instr:
        return carton_obj, None, None, None
    ip = instr.inner_pack or (carton_obj.inner_pack if carton_obj else None)
    ppc = instr.packs_per_carton or (carton_obj.packs_per_carton if carton_obj else None)
    pcs_ip = instr.pcs_per_inner_pack or (ip.units_per_pack if ip else None)
    return carton_obj, ip, ppc, pcs_ip


def pallet_level(product, instr, layout, with_artwork=True):
    """Poziom „Paleta" (wymaga zapisanego layoutu). Grafiki i glb kartonu idą też na
    paletę — inaczej stos to puste bryły bez ścian. `with_artwork=False` pomija grafiki
    kartonu jak na pozostałych poziomach (wcześniej paleta dociągała je zawsze)."""
    meta = instr.get_meta()
    carton = instr.carton
    carton_label = (carton.name if carton else None) or product.code
    # Realna grafika kartonu też na palecie — inaczej stos to puste bryły bez ścian.
    pallet_carton_art = _art_list(carton, with_artwork)
    # Realny model 3D kartonu (glTF) też na palecie — renderer wczyta go RAZ i sklonuje
    # na każdą pozycję (współdzielona geometria), inaczej karton z glb daje szary stos.
    pallet_carton_glb = _glb_url(carton)
    n_layers = layout.get("layers_used", 1) or 1
    base_h = instr.pallet_base_height_cm or meta.get("base_height_cm", 15)
    pallet_total_h = int(base_h + n_layers * instr.carton_h)
    return {
        "key": "pallet",
        "title": "Paleta",
        "subtitle": "Jednostka ładunkowa",
        "color": "#8B6914",
        "dims": f"{meta['length_cm']}×{meta['width_cm']}×{pallet_total_h} cm",
        **_dims(meta["length_cm"], meta["width_cm"], pallet_total_h),
        "qty": f"{layout['cartons_per_pallet']} KAR, {layout['layers_used']} warstw",
        "three_data": json.dumps({
            "type": "pallet",
            "pallet": {"l": meta["length_cm"], "w": meta["width_cm"],
                       "base_h": meta.get("base_height_cm", 15)},
            "carton": {"l": instr.carton_l, "w": instr.carton_w, "h": instr.carton_h},
            "placements": layout.get("placements", []),
            "layers": n_layers,
            "label": carton_label,
            "color": "#DCC4A0",
            **({"carton_artwork": pallet_carton_art} if pallet_carton_art else {}),
            **({"carton_glb_url": pallet_carton_glb} if pallet_carton_glb else {}),
        }),
    }


def carton_level(product, instr, carton_obj, ip, ppc, upp, with_artwork):
    """Poziom „Karton"; z OPZ i OPZ/karton renderuje karton z jednostkami w środku."""
    carton_label = (carton_obj.name if carton_obj else None) or product.code
    # Realna grafika opakowania: rozmieszczone na ściankach grafiki kartonu
    # (CartonArtwork) — renderer 3D nakłada je zamiast płaskiego koloru. Pusto = kolor.
    carton_art = _art_list(carton_obj, with_artwork)
    glb = _glb_url(carton_obj)
    with_units = bool(ip and ppc)
    return {
        "key": "carton",
        "title": "Karton",
        "subtitle": "Jednostka kompletacyjna",
        "color": "#A07840",
        "dims": f"{instr.carton_l}×{instr.carton_w}×{instr.carton_h} cm",
        **_dims(instr.carton_l, instr.carton_w, instr.carton_h),
        "ean": carton_obj.ean if carton_obj else "",
        "qty": (f"{instr.pcs_per_carton} OP / KAR"
                if upp > 1 else f"{instr.pcs_per_carton} szt / KAR"),
        "three_data": json.dumps({
            "type": "box_with_units" if with_units else "box",
            "l": instr.carton_l, "w": instr.carton_w, "h": instr.carton_h,
            **({"unit_l": ip.length_cm, "unit_w": ip.width_cm,
                "unit_h": ip.height_cm, "units": ppc} if with_units else {}),
            "label": carton_label,
            "color": "#DCC4A0",
            **({"artwork": carton_art} if carton_art else {}),
            # Realny model 3D (glTF) zastępuje generowaną bryłę, gdy wgrany na kartonie.
            **({"glb_url": glb} if glb else {}),
        }),
    }


def _has_unit_dims(product):
    return bool(product.unit_length_cm and product.unit_width_cm and product.unit_height_cm)


def inner_pack_level(product, ip, pcs_ip, with_artwork):
    """Poziom „Opakowanie zbiorcze" (OPZ); ze sztukami w środku, gdy znane wymiary sztuki
    i szt/OPZ. „Opakowanie handlowe" (sales_unit) celowo pominięte: MATinfo pokazuje
    kanoniczne 4 poziomy — dane sales_unit_* zostają w modelu, bez osobnego kafla."""
    unit_data = {}
    if _has_unit_dims(product) and pcs_ip:
        unit_data = {"unit_l": product.unit_length_cm,
                     "unit_w": product.unit_width_cm,
                     "unit_h": product.unit_height_cm,
                     "units": pcs_ip}
    qty_str = (f"{pcs_ip} szt / opakowanie" if pcs_ip
               else f"{ip.length_cm}×{ip.width_cm}×{ip.height_cm} cm")
    ip_art = _art_list(ip, with_artwork)
    return {
        "key": "inner_pack",
        "title": "Opakowanie zbiorcze",
        "subtitle": "Jednostka dostawy",
        "color": "#B79B6B",
        "dims": f"{ip.length_cm}×{ip.width_cm}×{ip.height_cm} cm",
        **_dims(ip.length_cm, ip.width_cm, ip.height_cm),
        "ean": ip.ean,
        "qty": qty_str,
        "three_data": json.dumps({
            "type": "box_with_units" if unit_data else "box",
            "l": ip.length_cm, "w": ip.width_cm, "h": ip.height_cm,
            **unit_data,
            "label": product.code,
            "color": "#D8C7A0",
            **({"artwork": ip_art} if ip_art else {}),
        }),
    }


def _unit_volume(product):
    return round(product.unit_length_cm * product.unit_width_cm
                 * product.unit_height_cm / 1_000_000, 5)


def unit_level(product, upp, with_artwork):
    """Poziom „Sztuka" albo — przy podziale (upp > 1) — „OP (opakowanie)"."""
    vol = _unit_volume(product)
    split = upp > 1
    prod_art = _art_list(product, with_artwork)
    glb = _glb_url(product)
    return {
        "key": "unit",
        "title": "OP (opakowanie)" if split else "Sztuka",
        "subtitle": "Opakowanie" if split else "Jednostka konsumpcyjna",
        "color": "#B79B6B",
        "dims": f"{product.unit_length_cm}×{product.unit_width_cm}×{product.unit_height_cm} cm",
        **_dims(product.unit_length_cm, product.unit_width_cm, product.unit_height_cm),
        "ean": product.ean,
        "qty": (f"{upp} szt JU w opakowaniu — {vol} m³" if split else f"1 szt — {vol} m³"),
        "three_data": json.dumps({
            "type": "box",
            "l": product.unit_length_cm, "w": product.unit_width_cm,
            "h": product.unit_height_cm,
            "label": product.code,
            "color": "#CDBE9A",
            **({"artwork": prod_art} if prod_art else {}),
            # Realny model 3D (.glb) poziomu OP/sztuka — jak glb_url kartonu.
            **({"glb_url": glb} if glb else {}),
        }),
    }


def _ju_image_artwork(url):
    """Zdjęcie JU jako pseudo-artwork frontu (kontrakt _artwork_as_dict, bez nowego modelu)."""
    return [{"id": 0, "face": "front", "kind": "print",
             "url": url, "thumb": url, "full": url, "name": "",
             "x": 0, "y": 0, "w": 100, "h": 100,
             "rot": 0, "z": 0, "w_px": None, "h_px": None}]


def ju_level(product, upp, with_artwork):
    """Poziom „Sztuka (JU)" — tylko przy podziale OP/JU; bok JU ≈ sześcian z objętości."""
    side = round((_unit_volume(product) / upp * 1_000_000) ** (1 / 3), 1) or 1.0
    ju_glb = _glb_url(product, "ju_glb_model")
    ju_img = product.ju_image.url if (with_artwork and product.ju_image) else ""
    return {
        "key": "ju",
        "title": "Sztuka (JU)",
        "subtitle": "Jednostka użytkowa",
        "color": "#C2A878",
        "dims": f"≈ {side}×{side}×{side} cm",
        **_dims(side, side, side),
        "ean": product.ean,
        "qty": f"{upp} szt JU w opakowaniu",
        "three_data": json.dumps({
            "type": "box_with_units",
            "l": product.unit_length_cm, "w": product.unit_width_cm,
            "h": product.unit_height_cm,
            "unit_l": side, "unit_w": side, "unit_h": side, "units": upp,
            "label": product.code,
            "color": "#CDBE9A",
            # Media JU: model .glb zastępuje bryłę; zdjęcie = nadruk frontu.
            **({"glb_url": ju_glb} if ju_glb else {}),
            **({"artwork": _ju_image_artwork(ju_img)} if ju_img else {}),
        }),
    }


def build_levels(product, instr, layout, upp, with_artwork):
    """Wszystkie policzalne poziomy w kolejności kart: paleta → karton → OPZ → sztuka → JU."""
    levels = []
    if instr and layout:
        levels.append(pallet_level(product, instr, layout, with_artwork))
    if instr:
        carton_obj, ip, ppc, pcs_ip = packaging_refs(instr)
        levels.append(carton_level(product, instr, carton_obj, ip, ppc, upp, with_artwork))
        if ip:
            levels.append(inner_pack_level(product, ip, pcs_ip, with_artwork))
    if _has_unit_dims(product):
        levels.append(unit_level(product, upp, with_artwork))
        if upp > 1:
            levels.append(ju_level(product, upp, with_artwork))
    return levels
