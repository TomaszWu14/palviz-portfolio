"""Porównanie wariantów hali na tym samym dniu projektowym (plan 2026-10-02, etap 7) — czysty Python.

Dla każdego wariantu: pojemność z regałów modelu + flota dobrana symulacją (start od floty
z formularza, potem sugerowana, aż przestanie się zmieniać) → wskaźniki obok siebie.
"""
from .blender_scene import _is_shelf
from .design_generator import K1
from .design_sim import FLEET_KINDS, simulate

PALLET_W_M = 0.9               # miejsce paletowe w boku regału ≈ 0,9 m (jak design_kpi.rack_to_element)
MAX_ROUNDS = 4


def capacity(racks, features, floor):
    """Miejsca paletowe (regały nie-półkowe), lokalizacje kartonowe (półki K1), bramy, powierzchnia."""
    pallets = sum(r["n_bays"] * max(1, round(r["width"] / r["n_bays"] / PALLET_W_M)) * r["n_levels"]
                  for r in racks if not _is_shelf(r))
    cartons = sum(r["n_bays"] * r["n_levels"] * K1["per_level"] for r in racks if _is_shelf(r))
    gates = sum(1 for f in features if f["kind"] in ("dock", "gate"))
    return {"area_m2": round(floor["width"] * floor["depth"]), "pallets": pallets, "cartons": cartons,
            "gates": gates}


def required_fleet(tasks, racks, features, fleet, **kw):
    """Symulacja z flotą sugerowaną przez poprzedni przebieg, aż flota się ustali (≤ MAX_ROUNDS).
    Zwraca (wynik ostatniego przebiegu, flota) albo (None, flota) dla hali bez VNA/półek/doków."""
    result = None
    for _ in range(MAX_ROUNDS):
        result = simulate(tasks, racks, features, fleet, **kw)
        if result is None:
            return None, fleet
        suggested = {f["kind"]: f["suggested"] for f in result["fleet"]}
        if suggested == fleet:
            break
        fleet = suggested
    return result, fleet


# (klucz, etykieta, jednostka, lepiej: "min" | "max" | None)
ROWS = [
    ("area_m2", "Powierzchnia hali", "m²", "min"),
    ("pallets", "Miejsca paletowe", "", "max"),
    ("cartons", "Lokalizacje kartonowe K1", "", "max"),
    ("gates", "Bramy (doki + bramy)", "", None),
    *[(f"fleet_{k}", f"Wymagane: {label}", "szt.", "min") for k, label in FLEET_KINDS],
    ("km", "Droga sprzętu i pickerów w ciągu dnia", "km", "min"),
    ("wait_putaway", "Czekanie przyjęć P95", "min", "min"),
    ("wait_picking", "Czekanie kompletacji P95", "min", "min"),
    ("late", "Zadania kończone po 21:00", "", "min"),
]


def variant_row(cap, result, fleet):
    row = dict(cap)
    if result is None:
        return row
    row.update({f"fleet_{k}": fleet[k] for k, _ in FLEET_KINDS})
    row["km"] = round(sum(f["km"] for f in result["fleet"]), 1)
    row["wait_putaway"] = result["waits"].get("putaway", {}).get("p95_min")
    row["wait_picking"] = result["waits"].get("picking", {}).get("p95_min")
    row["late"] = result["late"]
    return row


def comparison(rows):
    """[{wiersz KPI z wartościami per wariant + najlepszy}] dla tabeli."""
    out = []
    for key, label, unit, better in ROWS:
        vals = [r.get(key) for r in rows]
        nums = [v for v in vals if isinstance(v, (int, float))]
        best = (min(nums) if better == "min" else max(nums)) if better and len(set(nums)) > 1 else None
        out.append({"label": label, "unit": unit,
                    "cells": [{"value": v, "best": best is not None and v == best} for v in vals]})
    return out
