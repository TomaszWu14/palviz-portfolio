"""Whole-vehicle load planning — how many pallets fit a truck/container, how many
vehicles a shipment needs, loading metres (LDM), payload & space utilisation, and a
simple weight-per-LDM balance check. Framework-free and unit-testable.

Dimensions in cm, weights in kg. EUR pallet footprint 120×80 by default.
"""
import math
from typing import List

# Inner usable dimensions (cm) + payload (kg). Road units are EU norms; sea containers use
# the precise internal dimensions supplied by the user (mm in the source table → cm here).
VEHICLES = [
    # Road (trailers — internal dims)
    {"key": "solo72",   "name": "Solo 7,2 m",                 "len": 720,  "wid": 245, "height": 270, "payload": 7000},
    {"key": "naczepa",  "name": "Naczepa Standard (firanka) 13,6 m", "len": 1360, "wid": 245, "height": 270, "payload": 24000},
    {"key": "mega",     "name": "Naczepa Mega 13,6 m",        "len": 1360, "wid": 245, "height": 300, "payload": 24000},
    {"key": "chlodnia", "name": "Naczepa chłodnia 13,4 m",    "len": 1340, "wid": 245, "height": 260, "payload": 22000},
    {"key": "jumbo",    "name": "Zestaw przestrzenny (Jumbo) 7,7+7,7 m", "len": 1540, "wid": 248, "height": 300, "payload": 24000},
    # Sea containers (internal dims)
    {"key": "cont20",     "name": "Kontener 20'",            "len": 592,  "wid": 234, "height": 238, "payload": 28000},
    {"key": "cont40",     "name": "Kontener 40'",            "len": 1205, "wid": 231, "height": 238, "payload": 26500},
    {"key": "cont40hc",   "name": "Kontener 40' High Cube",  "len": 1206, "wid": 235, "height": 268, "payload": 26500},
    {"key": "cont20ot",   "name": "Kontener 20' Open Top",   "len": 592,  "wid": 234, "height": 229, "payload": 28000},
    {"key": "cont40ot",   "name": "Kontener 40' Open Top",   "len": 1204, "wid": 234, "height": 227, "payload": 26500},
    {"key": "cont20rf",   "name": "Kontener 20' Reefer",     "len": 543,  "wid": 227, "height": 224, "payload": 27000},
    {"key": "cont40rf",   "name": "Kontener 40' Reefer",     "len": 1163, "wid": 229, "height": 251, "payload": 27000},
    {"key": "cont40hcpw", "name": "Kontener 40' HC Pallet Wide", "len": 1210, "wid": 243, "height": 269, "payload": 26500},
    {"key": "cont45hcpw", "name": "Kontener 45' HC Pallet Wide", "len": 1356, "wid": 244, "height": 270, "payload": 26000},
    {"key": "cont45hc",   "name": "Kontener 45' High Cube",  "len": 1356, "wid": 235, "height": 270, "payload": 26000},
]

# A safe upper limit on how high pallets may be double-stacked (realistic for road freight).
_MAX_STACK_LAYERS = 2
# Typical max load intensity for road transport (kg per loading metre) — balance heuristic.
_MAX_KG_PER_LDM = 1800.0


def floor_positions(v_len: int, v_wid: int, pl: int = 120, pw: int = 80) -> int:
    """Max pallet footprints on the floor, taking the better of two pure orientations."""
    if min(pl, pw) <= 0:
        return 0
    a = (v_len // pl) * (v_wid // pw)
    b = (v_len // pw) * (v_wid // pl)
    return max(a, b)


def plan_vehicle(vehicle: dict, n_pallets: int, pallet_height_cm: float,
                 avg_pallet_weight_kg: float, total_weight_kg: float,
                 double_stack: bool = False, pl: int = 120, pw: int = 80) -> dict:
    """Plan one vehicle type for the load. Returns capacity, vehicles needed, LDM and
    utilisation. Single-stack by default (never under-counts vehicles)."""
    floor = floor_positions(vehicle["len"], vehicle["wid"], pl, pw)
    # Twarde niedopasowania → 0 pojazdów (plan NIEwykonalny), zamiast max(1,...) udającego,
    # że za wysoka/za ciężka paleta „jakoś wejdzie": paleta wyższa niż przestrzeń ładunkowa
    # albo cięższa niż ładowność nie jedzie tym typem wcale.
    if pallet_height_cm and pallet_height_cm > vehicle["height"]:
        floor = 0
    if avg_pallet_weight_kg and avg_pallet_weight_kg > vehicle["payload"]:
        floor = 0
    layers = 1
    if double_stack and pallet_height_cm and pallet_height_cm > 0:
        layers = max(1, min(_MAX_STACK_LAYERS, int(vehicle["height"] // pallet_height_cm)))
    cap_space = floor * layers
    cap_weight = int(vehicle["payload"] // avg_pallet_weight_kg) if avg_pallet_weight_kg > 0 else cap_space
    per_vehicle = max(1, min(cap_space, cap_weight)) if cap_space else 0
    if per_vehicle <= 0 or n_pallets <= 0:
        return {**_base(vehicle, floor, layers), "per_vehicle": per_vehicle, "vehicles": 0,
                "limited_by": "—", "ldm": 0.0, "payload_util": 0.0, "space_util": 0.0,
                "kg_per_ldm": 0.0, "balance_ok": True}
    vehicles = math.ceil(n_pallets / per_vehicle)
    # LDM consumed: pallet length along the trailer over a 2.4 m wide deck.
    pallets_across = max(1, vehicle["wid"] // pw) if (vehicle["wid"] // pw) else 1
    ldm_per_pallet = (pl / 100.0) / max(1, pallets_across) / max(1, layers)
    ldm = round(n_pallets * ldm_per_pallet, 1)
    kg_per_ldm = round(total_weight_kg / ldm, 0) if ldm else 0.0
    return {
        **_base(vehicle, floor, layers),
        "per_vehicle": per_vehicle,
        "vehicles": vehicles,
        "limited_by": "waga" if cap_weight < cap_space else "przestrzeń",
        "ldm": ldm,
        "payload_util": round(100 * total_weight_kg / (vehicles * vehicle["payload"]), 1) if vehicle["payload"] else 0.0,
        "space_util": round(100 * n_pallets / (vehicles * cap_space), 1) if cap_space else 0.0,
        "kg_per_ldm": kg_per_ldm,
        "balance_ok": kg_per_ldm <= _MAX_KG_PER_LDM,
    }


def plan_all(n_pallets: int, pallet_height_cm: float, total_weight_kg: float,
             double_stack: bool = False, pl: int = 120, pw: int = 80) -> List[dict]:
    """Plan every vehicle preset and rank by fewest vehicles, then best space use."""
    avg_w = (total_weight_kg / n_pallets) if n_pallets > 0 else 0.0
    plans = [plan_vehicle(v, n_pallets, pallet_height_cm, avg_w, total_weight_kg,
                          double_stack, pl, pw) for v in VEHICLES]
    plans = [p for p in plans if p["vehicles"] > 0]
    plans.sort(key=lambda p: (p["vehicles"], -p["space_util"]))
    if plans:
        plans[0]["recommended"] = True
    return plans


def _base(vehicle: dict, floor: int, layers: int) -> dict:
    return {"key": vehicle["key"], "name": vehicle["name"], "floor": floor,
            "layers": layers, "recommended": False}
