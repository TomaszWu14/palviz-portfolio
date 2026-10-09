from dataclasses import dataclass
import math
from palletizer.domain.types import Dimensions


@dataclass(frozen=True)
class CartonVariant:
    """
    Model danych pod Twoje wymagania:
    - podajesz wymiary KARTONU (L/W/H)
    - wagę 1 SZTUKI (unit_weight_kg)
    - ile sztuk w kartonie (pieces_per_carton)
    - zapotrzebowanie w sztukach (demand_pieces)
    """
    sku: str
    variant: str
    dims: Dimensions

    unit_weight_kg: float          # waga 1 sztuki
    pieces_per_carton: int         # ile sztuk w kartonie
    demand_pieces: int             # ile sztuk trzeba przygotować

    carton_tare_kg: float = 0.0    # opcjonalnie: masa kartonu/opakowania

    allow_rotation: bool = True

    @property
    def id(self) -> str:
        return f"{self.sku}:{self.variant}"

    def validate(self) -> "CartonVariant":
        if not self.sku:
            raise ValueError("SKU nie może być puste.")
        if not self.variant:
            raise ValueError("WARIANT nie może być pusty.")
        self.dims.normalized()

        if math.isnan(self.unit_weight_kg) or math.isinf(self.unit_weight_kg) or self.unit_weight_kg <= 0:
            raise ValueError(f"WAGA_SZT musi być > 0 dla {self.id}")
        if self.pieces_per_carton <= 0:
            raise ValueError(f"SZT_W_KARTONIE musi być > 0 dla {self.id}")
        if self.demand_pieces < 0:
            raise ValueError(f"ILOSC_SZT nie może być ujemna dla {self.id}")
        if math.isnan(self.carton_tare_kg) or math.isinf(self.carton_tare_kg) or self.carton_tare_kg < 0:
            raise ValueError(f"KARTON_TARE nie może być ujemne dla {self.id}")
        return self

    @property
    def carton_weight_kg(self) -> float:
        return (self.unit_weight_kg * self.pieces_per_carton) + self.carton_tare_kg

    @property
    def cartons_needed(self) -> int:
        if self.demand_pieces == 0:
            return 0
        return int(math.ceil(self.demand_pieces / self.pieces_per_carton))
