"""Generator hali od parametrów — nowy magazyn „od zera” (plan 2026-10-02, etap 1).

Czysta funkcja (bez Django): parametry → regały (`WarehouseModelRack`), elementy hali
(`WarehouseHallFeature`) i podsumowanie. Wynik zapisujemy jako zwykły model magazynu,
więc od razu działa widok 3D, odtwarzacz przepływów i „wariant z modelu”.

Przepływ I wzdłuż osi X:
  ściana przyjęć (x = 0): doki kontenerowe + paletyzacja, dok paletowy → bufor przyjęć →
  przejazd AGV → blok VNA (korytarze wzdłuż X) → przejazd → strefa K1 (półki) + pakowanie
  paczek i owijarki → bufor wydań → ściana wydań (x = W): doki FTL, brama busów, dok paczek.
Wymiary hali wynikają z pojemności; liczba par rzędów VNA dobrana do proporcji `aspect`.
"""
import math

from .design_catalog import ELEMENTS

VNA = ELEMENTS["rack_vna"]["params"]
VNA_AISLE = ELEMENTS["rack_vna"]["aisle_m"]

PRESET = {                      # wzorzec EDCO Deurne przeskalowany do danych ACME (2026-10-02)
    "pallet_positions": 100_000, "carton_locations": 20_000,
    "clear_height_m": 16.0, "pallet_height_m": 2.35,
    "container_docks": 6, "pallet_in_docks": 1,
    "out_docks": 2, "van_gates": 1, "parcel_docks": 1,
    "packing_stations": 4, "wrappers": 2, "aspect": 1.7,
}

SPRINKLER_M = 1.0               # wolne pod tryskaczami
BEAM_GAP_M = 0.25               # belka + luz nad paletą
BACK_GAP_M = 0.1                # rzędy plecami do siebie
WALL_M = 1.0
INBOUND_BAND_M = 25.0           # rozładunek kontenerów, paletyzacja, bufor
OUTBOUND_BAND_M = 20.0          # bufor wydań przy dokach
TRANSFER_M = 5.0                # przejazd poprzeczny (AGV / wózki VNA zmieniają korytarz)
DOCK_PITCH_M, DOCK_W_M, DOCK_D_M = 5.0, 3.5, 4.0
# K1 — regał półkowy: gniazdo 1,0 × 0,6 m, 5 półek × 3 lokalizacje, korytarz 2 m (EPT)
K1 = {"bay_w": 1.0, "depth": 0.6, "levels": 5, "level_h": 0.45, "per_level": 3, "aisle": 2.0}


def vna_levels(clear_h, pallet_h):
    """Poziomy składowania z podłogą: góra najwyższej palety ≤ wysokość − tryskacze."""
    usable = clear_h - SPRINKLER_M - pallet_h
    return int(usable // (pallet_h + BEAM_GAP_M)) + 1 if usable >= 0 else 0


def _pair_pitch(depth, aisle):
    return 2 * depth + BACK_GAP_M + aisle


def _row_pairs(n_pairs, depth, aisle, y0):
    """[korytarz][A|B][korytarz]… — y każdego rzędu; A patrzy na korytarz przed, B za."""
    ys = []
    for k in range(n_pairs):
        y = y0 + aisle + k * _pair_pitch(depth, aisle)
        ys += [y, y + depth + BACK_GAP_M]
    return ys


def _rack(zone, i, x, y, bays, bay_w, depth, levels, level_h):
    return {"zone": zone, "rack_id": f"{i:03d}", "n_bays": bays, "n_levels": levels,
            "bay_width_cm": round(bay_w * 100), "depth_cm": round(depth * 100),
            "level_height_cm": round(level_h * 100), "x_m": round(x, 2), "y_m": round(y, 2),
            "angle_deg": 0.0}


def _feature(kind, label, x, y, w, d):
    return {"kind": kind, "label": label, "x_m": round(x, 2), "y_m": round(y, 2),
            "width_m": round(w, 2), "depth_m": round(d, 2), "angle_deg": 0.0}


def _docks(kind, label, n, x, y_start, start_no=1):
    return [_feature(kind, f"{label} {start_no + i}", x, y_start + i * DOCK_PITCH_M, DOCK_D_M, DOCK_W_M)
            for i in range(n)]


def generate(**overrides):
    p = {**PRESET, **overrides}
    levels = vna_levels(p["clear_height_m"], p["pallet_height_m"])
    if levels < 1:
        raise ValueError("Paleta nie mieści się pod tryskaczami — zwiększ wysokość hali.")
    level_h = p["pallet_height_m"] + BEAM_GAP_M
    per_bay = VNA["pallets_per_bay"] * levels
    bays_total = math.ceil(p["pallet_positions"] / per_bay)

    # K1: blok w przybliżeniu kwadratowy
    k1_per_bay = K1["levels"] * K1["per_level"]
    k1_bays = math.ceil(p["carton_locations"] / k1_per_bay) if p["carton_locations"] else 0
    k1_len = math.ceil(math.sqrt(k1_bays * K1["bay_w"] * _pair_pitch(K1["depth"], K1["aisle"]) / 2)) if k1_bays else 0
    k1_pairs = math.ceil(k1_bays / (2 * k1_len)) if k1_bays else 0
    k1_depth = k1_pairs * _pair_pitch(K1["depth"], K1["aisle"]) + K1["aisle"] if k1_bays else 0
    k1_band = max(k1_len, 30.0)

    docks_in = p["container_docks"] + p["pallet_in_docks"]
    docks_out = p["out_docks"] + p["van_gates"] + p["parcel_docks"]
    min_depth = max(docks_in, docks_out) * DOCK_PITCH_M + 2 * WALL_M

    best = None                 # liczba par rzędów VNA najbliższa proporcji hali
    for n_pairs in range(1, 600):
        bays_row = math.ceil(bays_total / (2 * n_pairs))
        length = bays_row * VNA["bay_width"]
        depth = max(2 * WALL_M + VNA_AISLE + n_pairs * _pair_pitch(VNA["depth"], VNA_AISLE),
                    min_depth, k1_depth + 30.0)
        width = INBOUND_BAND_M + 2 * TRANSFER_M + length + k1_band + OUTBOUND_BAND_M
        score = abs(width / depth - p["aspect"])
        if best is None or score < best[0]:
            best = (score, n_pairs, bays_row, length, width, depth)
    _, n_pairs, bays_row, length, W, D = best

    x_vna = INBOUND_BAND_M + TRANSFER_M
    racks = [_rack("V", i + 1, x_vna, y, bays_row, VNA["bay_width"], VNA["depth"], levels, level_h)
             for i, y in enumerate(_row_pairs(n_pairs, VNA["depth"], VNA_AISLE, WALL_M))]
    x_k1 = x_vna + length + TRANSFER_M
    if k1_bays:
        racks += [_rack("K1", i + 1, x_k1, y, k1_len, K1["bay_w"], K1["depth"], K1["levels"], K1["level_h"])
                  for i, y in enumerate(_row_pairs(k1_pairs, K1["depth"], K1["aisle"], WALL_M))]

    # Ściana przyjęć (x = 0)
    y_in = (D - docks_in * DOCK_PITCH_M) / 2
    f = _docks("dock", "Dok kontenerowy (przenośnik teleskopowy)", p["container_docks"], 0, y_in)
    f += [_feature("station", f"Paletyzacja {i + 1}", DOCK_D_M + 2, y_in + i * DOCK_PITCH_M, 4, DOCK_W_M)
          for i in range(p["container_docks"])]
    f += _docks("dock", "Dok paletowy", p["pallet_in_docks"], 0, y_in + p["container_docks"] * DOCK_PITCH_M)
    f.append(_feature("block_zone", "Bufor przyjęć", 13, WALL_M, INBOUND_BAND_M - 14, D - 2 * WALL_M))
    f.append(_feature("corridor", "Przejazd AGV — przyjęcia", INBOUND_BAND_M, 0, TRANSFER_M, D))
    f.append(_feature("corridor", "Przejazd — wydania", x_vna + length, 0, TRANSFER_M, D))
    # Strefa K1: pakowanie paczek i owijarki pod blokiem półek
    stations = ([("Pakowanie paczek", i) for i in range(p["packing_stations"])]
                + [("Owijarka", i) for i in range(p["wrappers"])])
    cols = max(1, int(k1_band // 6))
    f += [_feature("station", f"{name} {i + 1}", x_k1 + (n % cols) * 6, WALL_M + k1_depth + 4 + (n // cols) * 6, 4, 4)
          for n, (name, i) in enumerate(stations)]
    # Ściana wydań (x = W)
    x_out = W - DOCK_D_M
    y_out = (D - docks_out * DOCK_PITCH_M) / 2
    f += _docks("dock", "Dok FTL", p["out_docks"], x_out, y_out)
    f += _docks("gate", "Brama busów (najazd, poziom 0)", p["van_gates"], x_out, y_out + p["out_docks"] * DOCK_PITCH_M)
    f += _docks("dock", "Dok paczek → kontener (przenośnik teleskopowy)", p["parcel_docks"], x_out,
                y_out + (p["out_docks"] + p["van_gates"]) * DOCK_PITCH_M)
    f.append(_feature("block_zone", "Bufor wydań", W - OUTBOUND_BAND_M, WALL_M, OUTBOUND_BAND_M - DOCK_D_M - 1,
                      D - 2 * WALL_M))

    summary = {
        "floor_w": round(W, 1), "floor_d": round(D, 1), "area_m2": round(W * D),
        "pallet_positions": 2 * n_pairs * bays_row * per_bay, "vna_levels": levels,
        "vna_rows": 2 * n_pairs, "vna_aisles": n_pairs + 1, "vna_row_len_m": round(length, 1),
        "carton_locations": 2 * k1_pairs * k1_len * k1_per_bay, "k1_rows": 2 * k1_pairs,
        "docks_in": docks_in, "docks_out": docks_out,
    }
    return {"floor": {"width": round(W, 2), "depth": round(D, 2)}, "racks": racks,
            "features": f, "summary": summary, "params": p}
