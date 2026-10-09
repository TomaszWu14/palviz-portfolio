from dataclasses import dataclass


@dataclass(frozen=True)
class Dimensions:
    l_cm: int
    w_cm: int
    h_cm: int

    def normalized(self) -> "Dimensions":
        if self.l_cm <= 0 or self.w_cm <= 0 or self.h_cm <= 0:
            raise ValueError(f"Niepoprawne wymiary: {self}")
        return self
