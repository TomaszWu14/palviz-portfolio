"""Jedno źródło prawdy dla formatu kodu lokalizacji magazynowej.

Kształt: „B0-01-100A" / „B0-07-300C-1" (strefa-rząd-kolumna+poziom[-podział]).
Reużywane przez skaner PHV (QR + ręczne wpisanie) i import lokalizacji —
wcześniej dwa niezależne, ręcznie synchronizowane regexy (drift = ciche
niedopasowanie lookupów).
"""
import re

LOCATION_CODE_RE = re.compile(r"^([A-Za-z]\d+)-(\d+)-(\d+)([A-Za-z](?:-\d+)?)$")


def match_location_code(code):
    """Match znormalizowanego kodu lokalizacji (None gdy format nie pasuje)."""
    return LOCATION_CODE_RE.match((code or "").strip())
