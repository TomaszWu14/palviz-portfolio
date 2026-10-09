"""Liczba palet wysyłki na JEDNEJ wysokości (audyt BIZ-007, decyzja właściciela
„Wysokość wybrana na wysyłce”).

Wycena spedycji (ekran, e-mail, strona odpowiedzi), zapytanie do magazynu, KPI
transportu, lista wysyłek, pakiet kierowcy i CMR liczą palety dla wysokości wybranej
na wysyłce (`selected_pallet_height_cm`); bez wyboru — domyślnej (_DEFAULT_PALLET_HEIGHT).
Tę samą wysokość bierze generowanie HU (huctl) — liczba HU = liczba palet z wyceny.
Serwis transportu (jak transport.kpi) — jedno miejsce zamiast kopii w widokach.
"""
from ui.views.core import _calc_shipment_data, _shipment_pallet_height


# Wysokość ładunku palety [cm]: wybrana na wysyłce, inaczej domyślna. Wspólna z
# generowaniem HU (huctl) — dlatego żyje w ui.views.core, nie tutaj.
pallet_height_cm = _shipment_pallet_height


def shipment_pallet_calc(shipment, **calc_kw):
    """(calc, scenariusz) liczone TYLKO dla wysokości wysyłki — scenariusz zawsze pasuje do
    wybranej wysokości (także spoza domyślnych 1,8/2,25 m). `calc_kw` → _calc_shipment_data
    (stow_eff, with_packing, instr_map). Scenariusz None, gdy calc nie ma scenariuszy."""
    calc = _calc_shipment_data(shipment, heights=[pallet_height_cm(shipment)], **calc_kw)
    return calc, (calc["scenarios"][0] if calc["scenarios"] else None)
