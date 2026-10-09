from dataclasses import dataclass
from palletizer.domain.types import Dimensions


@dataclass(frozen=True)
class PalletType:
    code: str
    dims: Dimensions  # l,w,h gdzie h = max wysokość (limit)
    max_weight_kg: int

    @property
    def length_cm(self) -> int:
        return self.dims.l_cm

    @property
    def width_cm(self) -> int:
        return self.dims.w_cm

    @property
    def max_height_cm(self) -> int:
        return self.dims.h_cm

    def validate(self) -> "PalletType":
        if not self.code:
            raise ValueError("Kod palety nie może być pusty.")
        self.dims.normalized()
        if self.max_weight_kg <= 0:
            raise ValueError("Max waga palety musi być > 0.")
        return self
