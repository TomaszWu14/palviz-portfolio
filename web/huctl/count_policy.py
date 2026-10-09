"""Czysta polityka bramki kontroli HU — wyciągnięta z hu_control_count, testowalna bez HTTP.

Widok czyta request/session/settings/DB i podaje PRYMITYWY; ten moduł NIE importuje niczego
z warstwy widoków (unika circular-importu przez huctl/views/__init__ star-export). Owija to,
co dotąd żyło jako ~120 linii `if ... return redirect(...)` w ciele POST-a: uporządkowaną
sekwencję bramek + mapowanie na komunikat/dokąd-wrócić, oraz predykaty blind-count /
dowodu-foto / całych-jednostek.
"""
from typing import NamedTuple

# Tolerancja zaokrąglenia 2-decymalnego przy liczeniu HU — jedno źródło dla skanera i raportów.
COUNT_TOLERANCE = 0.05


def qty_mismatch(counted_base, expected_base):
    """True gdy zliczona ilość odbiega od oczekiwanej ponad tolerancję zaokrąglenia."""
    return abs(counted_base - expected_base) > COUNT_TOLERANCE


class GateResult(NamedTuple):
    ok: bool
    message: str = ""
    redirect_to: str = ""     # "detail" | "menu" — symbolicznie; widok mapuje na reverse()
    code: str = ""            # kod odrzucenia dla sync offline (hu_control_sync, BIZ-006)


_OK = GateResult(True)


def count_gate(*, zone_label, zone_ok, type_ok, hu_status, recheck,
               recheck_by_original, scan_src, enforce):
    """Pierwsza niespełniona bramka wygrywa (jak kolejność `if` w widoku). ok=True → wolno
    liczyć; ok=False → komunikat + dokąd wrócić ("detail"/"menu").

    Kolejność MUSI się zgadzać z hu_control_count (pre-foto):
      1. strefa  2. typ pod kontrolą  3/4. statusy terminalne ok/escaped
      5. rekontrola przez pierwotnego kontrolera  6. scan-enforce
    item-error jest sprawdzane w widoku PO foto-dywersji (kolejność wrażliwa) — nie tutaj.
    """
    if not zone_ok:
        return GateResult(False, f"Brak uprawnień do kontroli w strefie „{zone_label}”.", "menu",
                          "zone_forbidden")
    if not type_ok:
        return GateResult(False, "Ten typ magazynu nie jest objęty kontrolą HU.", "menu",
                          "type_not_controlled")
    if hu_status == "ok":
        return GateResult(False, "HU jest zgodny i zablokowany — tylko podgląd.", "detail",
                          "hu_locked")
    if hu_status == "escaped":
        return GateResult(False, "HU oznaczony jako „wyjechało bez kontroli” — liczenie zablokowane.",
                          "detail", "hu_locked")
    if recheck and recheck_by_original:
        return GateResult(False, "Rekontrolę musi wykonać inny kontroler niż prowadzący "
                                 "pierwotną kontrolę.", "detail", "recheck_same_controller")
    if enforce and scan_src != "scan":
        return GateResult(False, "Zeskanuj kod HU SKANEREM przy palecie, aby liczyć.", "detail",
                          "scan_required")
    return _OK


def whole_units_ok(counted_base):
    """True gdy suma zliczona jest całkowita (najmniejsza jednostka pochłania resztę).
    2-decimalna tolerancja jak w widoku."""
    return abs(counted_base - round(counted_base)) <= COUNT_TOLERANCE


_PHOTO_REQUIRED = {"bad_placement": "Nieprawidłowe ułożenie", "damaged": "Uszkodzony towar"}


def photo_required_for_flags(flags, has_photo):
    """Zwróć etykietę pierwszej flagi wymagającej dowodu-foto, gdy zdjęcia brak; inaczej None.
    Uszkodzenie i złe ułożenie muszą być poparte zdjęciem (dowód roszczenia)."""
    if has_photo:
        return None
    for key, label in _PHOTO_REQUIRED.items():
        if flags.get(key):
            return label
    return None


def is_blind_recount_needed(counted_base, base_qty, *, sure, has_photo, recheck):
    """Liczenie „w ciemno": przy rozjeździe ilości każ kontrolerowi przeliczyć ponownie, zanim
    zaksięgujemy to jako błąd pickera (strażnik przed literówką kontrolera). Pomiń gdy jawnie
    potwierdził („sure"), gdy dołączył zdjęcie (flaga nie przechodzi round-tripem przez sesję),
    albo przy rekontroli (oczekiwana ilość i tak widoczna)."""
    return qty_mismatch(counted_base, base_qty) and not sure and not has_photo and not recheck
