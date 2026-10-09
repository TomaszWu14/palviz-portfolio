import pandas as pd
from typing import List

from palletizer.domain import CartonVariant, Dimensions
from palletizer.io.parsing import parse_float, parse_int


REQUIRED_COLUMNS = ["SKU", "WARIANT", "L", "W", "H", "WAGA_SZT", "SZT_W_KARTONIE", "ILOSC_SZT"]
OPTIONAL_COLUMNS = ["KARTON_TARE"]


def load_cartons_from_csv(path: str) -> List[CartonVariant]:
    df = pd.read_csv(path, encoding="utf-8-sig")

    # normalizacja nazw
    df.columns = [c.strip().upper() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Brak kolumn w CSV: {missing}. Wymagane: {REQUIRED_COLUMNS}")

    cartons: List[CartonVariant] = []
    for _, row in df.iterrows():
        tare = 0.0
        if "KARTON_TARE" in df.columns and not pd.isna(row["KARTON_TARE"]):
            tare = parse_float(row["KARTON_TARE"])

        carton = CartonVariant(
            sku=str(row["SKU"]).strip(),
            variant=str(row["WARIANT"]).strip(),
            dims=Dimensions(
                l_cm=parse_int(row["L"]),
                w_cm=parse_int(row["W"]),
                h_cm=parse_int(row["H"]),
            ),
            unit_weight_kg=parse_float(row["WAGA_SZT"]),
            pieces_per_carton=parse_int(row["SZT_W_KARTONIE"]),
            demand_pieces=parse_int(row["ILOSC_SZT"]),
            carton_tare_kg=tare,
            allow_rotation=True,
        ).validate()

        cartons.append(carton)

    return cartons
