"""Jedno źródło prawdy hierarchii opakowań produktu (desktop + skaner/PHV).

`build_hierarchy(product)` zwraca poziomy (z gotowym `three_data` dla wspólnego
renderera three.js `renderPalVizLevel`), KPI palety i alerty spójności. Logika
przeniesiona 1:1 z dawnego widoku `planner_product_hierarchy` — oba widoki mają
IDENTYCZNE liczby, bo liczą tu, nie u siebie.

Zasady (wymagania skanera 2026-08-06):
  • poziom „Warstwa" NIE jest kartą hierarchii — warstwy zostają metadaną palety
    („…, N warstw" w opisie),
  • zestaw poziomów wynika z KATEGORII produktu (ProductCategory.hierarchy_levels,
    CSV kluczy; puste = wszystkie dostępne),
  • poziom wymagany przez kategorię, ale niepoliczalny → alert „Brak przelicznika: X → Y",
  • niespójności przeliczników (OPZ×szt/OPZ ≠ szt/karton itd.) → alert + log.
"""
import json
import logging
import math

from .hierarchy_levels import build_levels, packaging_refs

log = logging.getLogger(__name__)

# Klucze poziomów (kolejność = kolejność kart). „layer" celowo nieobecny.
# „sales_unit" (opakowanie handlowe) usunięty z kanonicznych poziomów — MATinfo pokazuje max 4.
LEVEL_KEYS = ["pallet", "carton", "inner_pack", "unit", "ju"]
LEVEL_NAMES = {
    "pallet": "paleta", "carton": "karton", "inner_pack": "opakowanie zbiorcze (OPZ)",
    "sales_unit": "opakowanie handlowe", "unit": "sztuka / OP", "ju": "sztuka (JU)",
}
# Rodzaj jednostki miary poziomu (dla karty materiału): podstawowa / handlowa / zbiorcza.
UOM_KIND = {
    "pallet": "collective", "carton": "collective", "inner_pack": "collective",
    "sales_unit": "sales", "unit": "base", "ju": "base",
}
UOM_KIND_LABEL = {
    "base": "Podstawowa JM", "sales": "Jednostka handlowa", "collective": "Jednostka zbiorcza",
}
# Który przelicznik „produkuje" dany poziom — do komunikatu o braku.
MISSING_MSG = {
    "pallet": "karton → paleta (układ palety)",
    "carton": "sztuka → karton",
    "inner_pack": "sztuka → opakowanie zbiorcze (OPZ)",
    "sales_unit": "OPZ → opakowanie handlowe",
    "unit": "wymiary sztuki (master data)",
    "ju": "OP → sztuka użytkowa (JU)",
}


def cartons_per_pallet_estimate(instr):
    """Geometryczny fallback kartonów/paletę dla instrukcji BEZ zapisanego layoutu
    (np. import master daty): prosta siatka na warstwę (z obrotem 90°) × liczba warstw
    pod limitem wysokości. 0 przy brakach wymiarów. Jedno źródło — używane przez
    unit_factors (skaner Kontroli HU) i summary hierarchii (MATINFO), żeby ten sam
    indeks dawał identyczne wartości we wszystkich widokach."""
    try:
        pl, pw = int(instr.pallet_length_cm or 0), int(instr.pallet_width_cm or 0)
        cl, cw, ch = int(instr.carton_l or 0), int(instr.carton_w or 0), int(instr.carton_h or 0)
        cargo_h = int(instr.max_height_total_cm or 0) - int(instr.pallet_base_height_cm or 0)
    except (TypeError, ValueError):
        return 0
    if min(pl, pw, cl, cw, ch) <= 0 or cargo_h <= 0:
        return 0
    per_layer = max((pl // cl) * (pw // cw), (pl // cw) * (pw // cl))
    return per_layer * (cargo_h // ch)


def unit_factors(instr):
    """WSPÓLNE przeliczniki AJM (szt bazowych na jednostkę): OPZ / KAR / PAL — jedno
    źródło prawdy dla MATINFO, skanera Kontroli HU i karty lokalizacji. None = jednostka
    niezdefiniowana dla indeksu. `estimated`=True gdy PAL policzony geometrycznym
    fallbackiem (brak zapisanego layoutu) — widoki oznaczają wartość jako szacunek.

    Fallbacki identyczne z build_hierarchy (wcześniej skaner liczył OPZ bez fallbacku
    na inner_pack.units_per_pack i miał WŁASNY estymator PAL → rozjazd między ekranami)."""
    out = {"opz": None, "kar": None, "pal": None, "cpp": None, "estimated": False}
    if not instr:
        return out
    carton_obj = getattr(instr, "carton", None)
    ip = instr.inner_pack or (carton_obj.inner_pack if carton_obj else None)
    pcs_ip = instr.pcs_per_inner_pack or (ip.units_per_pack if ip else None)
    if pcs_ip and pcs_ip > 1:
        out["opz"] = float(pcs_ip)
    if instr.pcs_per_carton and instr.pcs_per_carton > 1:
        out["kar"] = float(instr.pcs_per_carton)
    cpp = (instr.get_selected_layout() or {}).get("cartons_per_pallet")
    if not (cpp and cpp > 0):
        cpp = cartons_per_pallet_estimate(instr)
        out["estimated"] = bool(cpp)
    if cpp and cpp > 0:
        out["cpp"] = int(cpp)
        out["pal"] = float(cpp) * float(instr.pcs_per_carton or 1)
    return out


def _dims_product(instr, *attrs):
    """Iloczyn atrybutów instrukcji (None → 0)."""
    out = 1
    for attr in attrs:
        out *= getattr(instr, attr) or 0
    return out


def _round_half_up(x):
    """Zaokrąglenie „half up" (2,5 → 3), nie bankierskie jak wbudowane round()."""
    return int(math.floor(x + 0.5))


def _volume_fill_pct(summary, instr):
    """Wypełnienie objętości palety (%) z istniejących wymiarów; None przy brakach.
    Ujemne/zerowe kartony na paletę = brak danych; wynik w przedziale 0–100."""
    cpp = (summary or {}).get("cartons_per_pallet")
    if not (instr and cpp and cpp > 0):
        return None
    usable_h = (instr.max_height_total_cm or 0) - (instr.pallet_base_height_cm or 0)
    pallet_vol = _dims_product(instr, "pallet_length_cm", "pallet_width_cm") * usable_h
    carton_vol = _dims_product(instr, "carton_l", "carton_w", "carton_h")
    if pallet_vol > 0 and carton_vol > 0:
        return max(0, min(100, _round_half_up(100 * cpp * carton_vol / pallet_vol)))
    return None


def _per_pallet_counts(summary, instr):
    """Ile jednostek każdego poziomu mieści paleta (None = nieznane).
    JU (sztuka użytkowa) na paletę = OP/paletę × units_per_piece: 1 OP mieści `upp` JU,
    więc bez tego mnożnik OP→JU wychodził zawsze 1 zamiast upp na produktach split (OP/JU)."""
    summary = summary or {}
    upp = (instr.units_per_piece if instr else 1) or 1
    pcs_pp = summary.get("pcs_per_pallet")
    return {
        "pallet": 1,
        "carton": summary.get("cartons_per_pallet"),
        "inner_pack": summary.get("packs_per_pallet"),
        "sales_unit": summary.get("sales_units_per_pallet"),
        "unit": pcs_pp,
        "ju": (pcs_pp * upp) if pcs_pp else None,
    }


def _clear_level_mult(lvl):
    """Brak mnożnika: `mult=None` i BEZ `mult_exact` (bez starej flagi z poprzedniego
    wywołania na tych samych `levels`)."""
    lvl["mult"] = None
    lvl.pop("mult_exact", None)


def _set_level_mult(lvl, a, b):
    """`mult` = ile jednostek następnego poziomu (b/paletę) mieści bieżąca (a/paletę).
    Niecałkowity mnożnik = niespójna master data → `mult_exact=False` (widok może
    oznaczyć „≈"). Zaokrąglenie „half up"; dodatni stosunek daje min. ×1 (nigdy ×0);
    liczniki ≤ 0 = brak danych."""
    _clear_level_mult(lvl)
    if a and b and a > 0 and b > 0:
        ratio = b / a
        lvl["mult"] = max(1, _round_half_up(ratio))
        lvl["mult_exact"] = abs(ratio - lvl["mult"]) < 1e-9


def enrich_pallet_metrics(levels, summary, instr):
    """Metryki prezentacji WSPÓLNE dla plannera i PHV (jedno źródło = brak rozjazdu):
      • vol_pct — wypełnienie objętości palety (%) z istniejących wymiarów,
      • per-poziom `mult` — ile jednostek następnego poziomu mieści jednostka bieżącego.
    Mutuje `levels` w miejscu (dopisuje `mult`/`mult_exact`), zwraca `vol_pct` (int|None).
    Idempotentna: ponowne wywołanie na tych samych `levels` nie zostawia starych flag.
    Mnożnik liczony z „na paletę" (wewnętrznie spójne dzięki asercjom), więc odporny
    na brak opcjonalnych poziomów."""
    vol_pct = _volume_fill_pct(summary, instr)
    per_pallet = _per_pallet_counts(summary, instr)
    for i, lvl in enumerate(levels):
        if summary and i < len(levels) - 1:
            _set_level_mult(lvl, per_pallet.get(lvl["key"]), per_pallet.get(levels[i + 1]["key"]))
        else:
            _clear_level_mult(lvl)
    return vol_pct


def levels_for_category(category):
    """Zestaw kluczy poziomów wymaganych przez kategorię, albo None = bez ograniczeń.
    ProductCategory.hierarchy_levels: CSV kluczy z LEVEL_KEYS (puste pole = None)."""
    raw = (getattr(category, "hierarchy_levels", "") or "").strip()
    if not raw:
        return None
    keys = {k.strip() for k in raw.split(",") if k.strip() in LEVEL_KEYS}
    return keys or None


def _pallet_summary(instr, layout, carton_obj, upp):
    """KPI palety. Brak layoutu → ten sam geometryczny fallback co kafle skanera
    (unit_factors) — bez niego indeks bez layoutu pokazywał PAL na skanerze, a pusto
    w MATINFO (rozjazd)."""
    cpp = layout.get("cartons_per_pallet", 0) if layout else 0
    cpp_estimated = False
    if not cpp:
        cpp = cartons_per_pallet_estimate(instr)
        cpp_estimated = bool(cpp)
    pcs_per_pallet = cpp * instr.pcs_per_carton if cpp else None
    carton_weight = round(instr.unit_weight * instr.pcs_per_carton + instr.carton_tare, 2)
    pallet_weight = round(cpp * carton_weight, 1) if cpp else None
    ip2 = carton_obj.inner_pack if carton_obj else None
    packs_per_pal = (cpp * (carton_obj.packs_per_carton or 0)
                     if (cpp and carton_obj and carton_obj.packs_per_carton) else None)
    sales_per_pal = (packs_per_pal * ip2.sales_units_per_pack
                     if (packs_per_pal and ip2 and ip2.sales_unit_l_cm) else None)
    split = upp > 1
    return {
        "cpp_estimated": cpp_estimated,   # PAL z geometrii, nie z layoutu → „szacunek"
        "pcs_per_pallet": pcs_per_pallet,
        "pallet_unit": "OP" if split else "szt",
        "pallet_unit_desc": "opakowań (OP) na palecie" if split else "sztuk konsumpcyjnych",
        "cartons_per_pallet": cpp or None,
        "pallet_weight_kg": pallet_weight,
        "packs_per_pallet": packs_per_pal,
        "sales_units_per_pallet": sales_per_pal,
        "layers": layout.get("layers_used") if layout else None,
        "carton_weight_kg": carton_weight,
        "unit_weight_kg": instr.unit_weight,
    }


def _warn(alerts, product, msg):
    alerts.append(msg)
    log.warning("hierarchy inconsistency %s: %s", product.code, msg)


def _consistency_alerts(product, instr, ppc, pcs_ip, summary):
    """Spójność przeliczników (asercja wymagana przez spec skanera) → alerty + log.

    Drugi alert porównuje KPI palety widoczne na karcie: OPZ/paleta (z kartonu w bazie)
    × szt/OPZ vs szt/paleta (z instrukcji). Dawny warunek „szt/karton × kartony/paleta
    ≠ szt/paleta" był tautologią (szt/paleta liczone z tego samego iloczynu) — nigdy
    się nie pokazywał. Gdy niespójność wyłapał już alert kartonu — bez duplikatu."""
    alerts = []
    if ppc and pcs_ip and instr.pcs_per_carton and ppc * pcs_ip != instr.pcs_per_carton:
        _warn(alerts, product,
              f"Niespójność master daty: {ppc} OPZ/karton × {pcs_ip} szt/OPZ = "
              f"{ppc * pcs_ip} ≠ {instr.pcs_per_carton} szt/karton")
    packs_pp = summary.get("packs_per_pallet") if summary else None
    pcs_pp = summary.get("pcs_per_pallet") if summary else None
    if not alerts and packs_pp and pcs_ip and pcs_pp and packs_pp * pcs_ip != pcs_pp:
        _warn(alerts, product,
              f"Niespójność: {packs_pp} OPZ/paleta × {pcs_ip} szt/OPZ = "
              f"{packs_pp * pcs_ip} ≠ {pcs_pp} szt/paleta")
    return alerts


def _filter_by_category(product, instr, levels):
    """Filtr wg kategorii + alerty o brakach wymaganych poziomów → (levels, alerts)."""
    required = levels_for_category(product.category)
    built = {l["key"] for l in levels}
    if required is not None:
        alerts = [f"Brak przelicznika: {MISSING_MSG[k]}"
                  for k in LEVEL_KEYS if k in required and k not in built]
        return [l for l in levels if l["key"] in required], alerts
    # Bez konfiguracji kategorii: minimalny komplet = karton + paleta.
    alerts = []
    if instr:
        alerts += [f"Brak przelicznika: {MISSING_MSG[k]}"
                   for k in ("pallet", "carton") if k not in built]
    return levels, alerts


# Klucze three_data niosące realne media poziomu (paleta niesie media KARTONU).
_MEDIA_KEYS = ("artwork", "glb_url", "carton_artwork", "carton_glb_url")


def _decorate_levels(levels):
    """Flagi prezentacji dopisywane na końcu (w miejscu): has_media, rodzaj JM, noRuler."""
    for l in levels:
        # Rodzaj JM na kartę materiału (podstawowa / handlowa / zbiorcza).
        l["uom_kind"] = UOM_KIND.get(l["key"])
        l["uom_label"] = UOM_KIND_LABEL.get(l["uom_kind"], "")
        try:
            td = json.loads(l["three_data"])
        except (KeyError, ValueError, TypeError):
            l["has_media"] = False
            continue
        # Flaga „poziom ma realne media" (grafika-nadruk albo model .glb) — z KLUCZY
        # JSON-a, nie podciągu: paleta niesie carton_artwork/carton_glb_url, a etykieta
        # „artwork" nie może udawać grafiki.
        l["has_media"] = any(td.get(k) for k in _MEDIA_KEYS)
        # MatInfo: bez linijki wymiarowej („N cm”) na renderze 3D — wymiary są w tekście
        # pod kartą. Flaga w danych, więc oba ekrany MatInfo (desktop + skaner) ją dostają;
        # calc/paleta/shipment nie idą przez build_hierarchy, więc mają linijkę dalej.
        td["noRuler"] = True
        l["three_data"] = json.dumps(td)


def build_hierarchy(product, instr=None, with_artwork=True):
    """→ {"levels": [...], "summary": {...}|None, "alerts": [str], "instr": instr}.

    `levels[*]` = {key, title, subtitle, color, dims, qty, three_data(JSON str)}.
    Bez poziomu „layer" — liczba warstw siedzi w qty palety i summary["layers"].

    `with_artwork=False` pomija dociąganie i serializację grafik opakowania (trzy
    zapytania + `as_dict()` na produkt). Dla wołających, którym wystarczy ZESTAW
    poziomów — jak macierz grafik w Data Center, gdzie `build_hierarchy` leci na każdy
    wiersz strony — to różnica między kilkuset a kilkoma zapytaniami. Ekrany renderujące
    bryły (planner, PHV) zostają na domyślnym `True`.

    Orkiestrator (CODE-001): poziomy → `hierarchy_levels.build_levels`, KPI →
    `_pallet_summary`, alerty → `_consistency_alerts` + `_filter_by_category`."""
    instr = instr or product.latest_instruction()
    upp = (instr.units_per_piece if instr else 1) or 1
    layout = instr.get_selected_layout() if instr else None
    levels = build_levels(product, instr, layout, upp, with_artwork)
    carton_obj, _ip, ppc, pcs_ip = packaging_refs(instr)

    summary = _pallet_summary(instr, layout, carton_obj, upp) if (instr and levels) else None
    alerts = _consistency_alerts(product, instr, ppc, pcs_ip, summary) if instr else []
    levels, missing = _filter_by_category(product, instr, levels)
    alerts += missing
    if not instr:
        alerts.append("Brak instrukcji paletyzacji dla tego indeksu — hierarchia niekompletna")

    _decorate_levels(levels)
    return {"levels": levels, "summary": summary, "alerts": alerts, "instr": instr}
