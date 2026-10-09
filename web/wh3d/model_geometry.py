"""Geometria regałów z pliku CSV (np. odczytana z rysunku hali) → regały modelu magazynu.

Plik: nagłówek + jeden regał na wiersz, separator `;` albo `,`, przecinek dziesiętny
dozwolony. Kolumny wymagane: zone, rack_id, x_m, y_m, n_bays, bay_width_cm.
Opcjonalne: angle_deg (0), depth_cm (110), n_levels (4), level_height_cm (200).
Konwencja jak w modelu: narożnik regału w (x_m, y_m), oś szerokości wzdłuż kąta.

Czysty Python (bez Django) — testowalny w izolacji; zapis robi widok uploadu.
"""
import csv
import io

REQUIRED = ("zone", "rack_id", "x_m", "y_m", "n_bays", "bay_width_cm")
DEFAULTS = {"angle_deg": 0.0, "depth_cm": 110, "n_levels": 4, "level_height_cm": 200}
INTS = {"n_bays", "bay_width_cm", "depth_cm", "n_levels", "level_height_cm"}
MARGIN_M = 2.0


def is_geometry_csv(text):
    """Plik geometrii poznajemy po nagłówku (plik kodów lokalizacji go nie ma)."""
    head = next((ln for ln in (text or "").splitlines() if ln.strip()), "").lower()
    return "x_m" in head and "rack_id" in head


def _num(raw, as_int):
    v = float(str(raw).strip().replace(",", "."))
    return int(round(v)) if as_int else v


def parse_geometry_csv(text):
    """Tekst CSV → (racks, errors). Wiersz z błędem trafia do `errors` (nr linii + powód),
    nie przerywa reszty — widok decyduje, czy zapisywać."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return [], ["Pusty plik."]
    delim = ";" if lines[0].count(";") >= lines[0].count(",") else ","
    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delim)
    reader.fieldnames = [(f or "").strip().lower() for f in reader.fieldnames or []]
    missing = [c for c in REQUIRED if c not in reader.fieldnames]
    if missing:
        return [], [f"Brak kolumn: {', '.join(missing)}."]
    racks, errors, seen = [], [], set()
    for no, row in enumerate(reader, start=2):
        try:
            rack = {"zone": row["zone"].strip().upper(), "rack_id": row["rack_id"].strip()}
            if not rack["zone"] or not rack["rack_id"]:
                raise ValueError("pusta strefa albo nr regału")
            for key in ("x_m", "y_m", "n_bays", "bay_width_cm", *DEFAULTS):
                raw = (row.get(key) or "").strip()
                rack[key] = _num(raw, key in INTS) if raw else DEFAULTS[key]
            if min(rack[k] for k in INTS) < 1:
                raise ValueError("liczby gniazd/poziomów i wymiary muszą być ≥ 1")
        except (ValueError, TypeError, AttributeError) as exc:
            errors.append(f"Linia {no}: {exc}")
            continue
        key = (rack["zone"], rack["rack_id"])
        if key in seen:
            errors.append(f"Linia {no}: regał {key[0]}-{key[1]} powtórzony")
            continue
        seen.add(key)
        racks.append(rack)
    return racks, errors


def floor_size(racks, rack_corners):
    """Najmniejsza hala (szer., głęb.) mieszcząca wszystkie regały + margines."""
    xs, ys = [], []
    for r in racks:
        probe = {"x": r["x_m"], "y": r["y_m"], "angle": r["angle_deg"],
                 "width": r["n_bays"] * r["bay_width_cm"] / 100, "depth": r["depth_cm"] / 100}
        for x, y in rack_corners(probe):
            xs.append(x)
            ys.append(y)
    if not xs:
        return 0.0, 0.0
    return round(max(xs) + MARGIN_M, 1), round(max(ys) + MARGIN_M, 1)
