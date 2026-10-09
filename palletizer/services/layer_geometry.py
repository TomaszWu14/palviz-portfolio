# Struktury wyniku paletyzacji (wyniesione z pallet_calculator).
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Placement2D:
    x: int
    y: int
    dx: int
    dy: int
    rotated: bool


@dataclass(frozen=True)
class LayoutOption:
    name: str
    cartons_per_layer: int
    utilization_percent: float
    placements: Tuple[Placement2D, ...]


@dataclass(frozen=True)
class PalletizationResult:
    carton_id: str
    pallet_code: str
    pallet_dims_cm: str
    carton_dims_cm: str

    pieces_per_carton: int
    unit_weight_kg: float
    carton_tare_kg: float
    carton_weight_kg: float

    demand_pieces: int
    cartons_needed: int

    max_layers_by_height: int
    max_layers_by_weight: int
    layers_used: int

    best_layout: LayoutOption
    all_layouts: Tuple[LayoutOption, ...]

    cartons_per_pallet: int
    pallets_full: int
    remainder_cartons: int
    remainder_pieces_est: int

    height_used_cm: int
    weight_per_pallet_kg: float

