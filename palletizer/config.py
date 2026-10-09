from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class PalletPreset:
    code: str
    length_cm: int
    width_cm: int
    default_max_height_cm: int
    max_weight_kg: int


PALLET_PRESETS: Dict[str, PalletPreset] = {
    "EU":   PalletPreset(code="EU", length_cm=120, width_cm=80, default_max_height_cm=180, max_weight_kg=1000),
    # Legacy alias — kept so old DB rows with Z129 still resolve
    "Z129": PalletPreset(code="EU", length_cm=120, width_cm=80, default_max_height_cm=180, max_weight_kg=1000),
}


def get_pallet_preset(code: str) -> PalletPreset:
    key = (code or "").strip().upper()
    if key not in PALLET_PRESETS:
        available = ", ".join(sorted(PALLET_PRESETS.keys()))
        raise ValueError(f"Nieznany typ palety '{code}'. Dostępne: {available}")
    return PALLET_PRESETS[key]
