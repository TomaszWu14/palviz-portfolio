# Geometria dopasowania kartonu do palety + limity dla packera FFD sceny 3D przesyłki.
# Liść bez zależności (tylko stałe i czyste funkcje) — importowany przez
# helpers_shipment_three.py; bez star-eksportu (nazwy prywatne, importowane jawnie).
_FFD_PL, _FFD_PW, _FFD_BASE = 120, 80, 14
_FFD_OVER = 2                # cartons may overhang the 120×80 footprint by ~2 cm/side


def _ffd_fits(l, w):
    """Karton (l wzdłuż 120, w wzdłuż 80) mieści się w obrysie palety + tolerancji zwisu."""
    return l <= _FFD_PL + 2 * _FFD_OVER and w <= _FFD_PW + 2 * _FFD_OVER


def _ffd_oversize(l, w):
    """Nie mieści się w ŻADNEJ orientacji — ten sam predykat co _ffd_fits, więc pula
    nigdy nie gubi kartonu, który podział na mono-palety przepuścił."""
    return not (_ffd_fits(l, w) or _ffd_fits(w, l))


def _ffd_per_layer(cl, cw):
    return max(1, (_FFD_PL + 2 * _FFD_OVER) // cl) * max(1, (_FFD_PW + 2 * _FFD_OVER) // cw)


def _ffd_layer_orientation(l, w):
    """Orientacja warstwy (dopuszczalny obrót o 90°) z większą liczbą kartonów/warstwę —
    wybierana TYLKO spośród orientacji mieszczących się w obrysie + tolerancji (max(1,…)
    w _ffd_per_layer liczyłby wystającą jako „1 w rzędzie”). Remis → wejściowa, o ile się
    mieści. Wymaga, by karton nie był _ffd_oversize."""
    fits = [o for o in ((l, w), (w, l)) if _ffd_fits(*o)]
    return max(fits, key=lambda o: _ffd_per_layer(*o))    # max → pierwsza przy remisie


def _ffd_pool_footprint(l, w):
    """(l, w) oriented for the pool, or None when the carton can't be placed.

    Jak w _build_container_load: karton szerszy niż paleta → obróć; gdy wejściowa
    orientacja wystaje ponad tolerancję, a obrócona się mieści (np. 83×85 → 85×83) —
    obróć; niepasujący w żadnej orientacji → pomiń (stary kod kładł go z 8+ cm zwisu)."""
    if w > _FFD_PW + _FFD_OVER and l <= _FFD_PW + _FFD_OVER:
        l, w = w, l
    if _ffd_fits(l, w):
        return l, w
    return (w, l) if _ffd_fits(w, l) else None


def _ffd_num(v):
    """Liczba do komunikatu: maks. 1 miejsce po przecinku, polski przecinek dziesiętny."""
    return f"{float(v):.1f}".rstrip("0").rstrip(".").replace(".", ",")


def _ffd_limit_warnings(lines, max_h, mw):
    """Ostrzeżenia (PL) o kartonach rysowanych w 3D mimo przekroczenia limitu palety.

    Rozmieszczenie się nie zmienia (towar nie znika z widoku) — scena tylko jawnie mówi,
    że paleta z takim kartonem przekracza limit wagi (karton cięższy niż `mw`) albo
    wysokości (karton wyższy niż ładunek: max_h − podstawa; lmax_h=max(1,…) i tak kładzie
    1 warstwę). Karton zbyt duży na paletę w obu orientacjach jest pomijany w 3D — też
    z ostrzeżeniem (dawniej znikał z modelu po cichu)."""
    out = []
    cargo_h = max(1, max_h - _FFD_BASE)
    for lc in lines:
        l, w, h = lc["carton_dims"]
        if lc["n_cartons"] <= 0:
            continue
        if _ffd_oversize(l, w):
            out.append(f"Karton {lc['product'].code} ({_ffd_num(l)}×{_ffd_num(w)} cm) większy niż "
                       f"paleta {_FFD_PL}×{_FFD_PW} cm (+{_FFD_OVER} cm zwisu) — "
                       f"{lc['n_cartons']} szt. pominięto w modelu 3D")
            continue
        code, wkg = lc["product"].code, lc.get("carton_weight_kg") or 1.0
        if mw and wkg > mw:
            out.append(f"Karton {code} ({_ffd_num(wkg)} kg) cięższy niż limit palety "
                       f"({_ffd_num(mw)} kg) — paleta z nim przekracza limit wagi")
        if h > cargo_h:
            out.append(f"Karton {code} (wys. {_ffd_num(h)} cm) wyższy niż limit ładunku "
                       f"({_ffd_num(cargo_h)} cm przy palecie {_ffd_num(max_h)} cm)"
                       " — paleta z nim przekracza limit wysokości")
    return out
