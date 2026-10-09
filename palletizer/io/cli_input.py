from typing import List
from palletizer.domain import CartonVariant, Dimensions


def _read_int(prompt: str) -> int:
    while True:
        try:
            return int(input(prompt).strip())
        except ValueError:
            print("  Błąd: wpisz liczbę całkowitą.")


def _read_float(prompt: str, default: float | None = None) -> float:
    while True:
        raw = input(prompt).strip().replace(",", ".")
        if not raw and default is not None:
            return default
        try:
            return float(raw)
        except ValueError:
            print("  Błąd: wpisz liczbę (np. 1.5 lub 1,5).")


def read_cartons_from_cli() -> List[CartonVariant]:
    cartons: List[CartonVariant] = []

    print("Tryb ręczny: wpisuj kolejne pozycje. Pusta linia SKU kończy.")
    while True:
        sku = input("SKU (enter kończy): ").strip()
        if not sku:
            break

        variant = input("WARIANT (np. A/STD): ").strip() or "STD"

        l = _read_int("L kartonu [cm]: ")
        w = _read_int("W kartonu [cm]: ")
        h = _read_int("H kartonu [cm]: ")

        unit_w = _read_float("WAGA 1 sztuki [kg]: ")
        pcs_in_carton = _read_int("Ile sztuk w kartonie?: ")
        demand_pcs = _read_int("Zapotrzebowanie [szt]: ")
        tare = _read_float("Masa kartonu/tare [kg] (opcjonalnie, enter=0): ", default=0.0)

        cartons.append(
            CartonVariant(
                sku=sku,
                variant=variant,
                dims=Dimensions(l_cm=l, w_cm=w, h_cm=h),
                unit_weight_kg=unit_w,
                pieces_per_carton=pcs_in_carton,
                demand_pieces=demand_pcs,
                carton_tare_kg=tare,
                allow_rotation=True,
            ).validate()
        )

    return cartons
