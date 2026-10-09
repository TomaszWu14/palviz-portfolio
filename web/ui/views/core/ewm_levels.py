"""Litera kodu lokalizacji EWM → fizyczne miejsce w stosie (JEDNO źródło prawdy).

Kod EWM: ``{strefa}-{przejście}-{gniazdo:02}{pozycja}{litera}[-1|-2]``, np. ``B0-07-300C-1``.
Litera NIE jest kolumną w boku — to poziom (i ewentualnie półka / część) w jednym stosie.
Reguły potwierdzone na realnym eksporcie EWM (35 780 lokalizacji regałowych):

Hala B (każda strefa poza ``A0``–``A3``), kolejność od podłogi:
  • poziom 1 (dół, picking): ``A`` = pełna paleta; ALBO ``B``/``C``/``D`` = półki JEDNA NAD
    DRUGĄ w otworze poziomu 1 (B najniżej, D najwyżej); ``S`` = poziom 1 regału 5-poziomowego,
  • poziom 2: ``X``; ALBO ``G`` + ``H`` = miejsce X PODZIELONE na dwie części (G niżej,
    H wyżej — podział pionowy, potwierdzony: stos B → C → D → G → H albo B → C → D → X); ``T``,
  • poziom 3: ``Y`` / ``U``;  poziom 4: ``Z`` / ``V``;  poziom 5: ``W``.
  Końcówka ``-1``/``-2`` (np. ``C-1``, ``D-2``) = lewa/prawa połówka (~40 cm) tego miejsca.

Hala A (strefy ``A<cyfry>``, typ 0070): ``A``..``E`` = poziomy 1..5 (pełne palety).

Kolumna EWM „Poziom miejsca skł.” jest niewiarygodna (dla półek C-1…D-2 zawiera kolejne
numery), więc poziom ZAWSZE wynika z litery. Nieznana litera (np. legacy J/K/L/M/N/O
generatora) → ``None``: konsument zachowuje swój dotychczasowy fallback.

Czysty Python (bez ORM) — testowalny bez bazy.
"""
import re
from typing import NamedTuple


class LetterSlot(NamedTuple):
    """Fizyczne miejsce litery w stosie.

    level — poziom 1..5 (1 = dół / picking),
    shelf — 0 = całe miejsce poziomu; 1.. = półka/część od dołu w otworze poziomu
            (B=1, C=2, D=3 w poziomie 1; G=1, H=2 w poziomie 2 przy podziale pionowym),
    side  — 0 = cała szerokość; 1 = lewa, 2 = prawa połówka (końcówka -1/-2; także G/H,
            gdyby podział X był poziomy — patrz GH_SPLIT_VERTICAL).
    """
    level: int
    shelf: int
    side: int


# G/H dzielą miejsce X (poziom 2, każde ~1,1 m³ zamiast 2,2 m³). Potwierdzone: podział
# PIONOWY (G niżej, H wyżej). False = podział na lewą/prawą część (side 1/2).
GH_SPLIT_VERTICAL = True

_HALL_A_ZONE = re.compile(r"^A\d+$")
_HALL_A = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}
# litera → (poziom, półka) dla hali B
_HALL_B = {
    "A": (1, 0), "B": (1, 1), "C": (1, 2), "D": (1, 3), "S": (1, 0),
    "X": (2, 0), "T": (2, 0),
    "Y": (3, 0), "U": (3, 0),
    "Z": (4, 0), "V": (4, 0),
    "W": (5, 0),
}
_GH_PART = {"G": 1, "H": 2}


def is_hall_a(zone):
    """Strefa hali A (A0–A3…): litery A–E to kolejne poziomy."""
    return bool(_HALL_A_ZONE.match((zone or "").strip().upper()))


def _split_letter(letter, half):
    """„C-1” → („C”, 1); „c” → („C”, half)."""
    s = (letter or "").strip().upper()
    base, _, suffix = s.partition("-")
    if suffix.isdigit():
        half = int(suffix)
    return base, int(half or 0)


def letter_slot(zone, letter, half=0):
    """(strefa, litera[, połówka 1/2]) → LetterSlot albo None dla nieznanej litery.

    ``letter`` może zawierać końcówkę połówki („C-1”) — wtedy nadpisuje ``half``."""
    base, half = _split_letter(letter, half)
    side = half if half in (1, 2) else 0
    if len(base) != 1:
        return None
    if is_hall_a(zone):
        level = _HALL_A.get(base)
        return LetterSlot(level, 0, side) if level else None
    if base in _GH_PART:
        part = _GH_PART[base]
        return LetterSlot(2, part, side) if GH_SPLIT_VERTICAL else LetterSlot(2, 0, part)
    hit = _HALL_B.get(base)
    return LetterSlot(hit[0], hit[1], side) if hit else None


def code_slot(code):
    """Pełny kod EWM („B0-07-300C-1”) → LetterSlot albo None (brak litery / nieznana /
    4-członowy kod generatora „B0-01-100-2X”, gdzie litera jest kolumną, nie poziomem)."""
    parts = (code or "").strip().upper().split("-")
    if len(parts) < 3 or not parts[2][-1:].isalpha():
        return None
    half = parts[3] if len(parts) > 3 and parts[3] in ("1", "2") else 0
    return letter_slot(parts[0], parts[2][-1], int(half))


# Ile półek/części dzieli otwór poziomu (hala B): B/C/D → 3 w poziomie 1, G/H → 2 w poziomie 2.
_SHELVES_IN_OPENING = {1: 3, 2: 2}


def shelves_in_opening(slot):
    """Na ile części (w pionie) dzielony jest otwór poziomu danej półki; całe miejsce → 1."""
    if not slot or not slot.shelf:
        return 1
    return _SHELVES_IN_OPENING.get(slot.level, slot.shelf)


def letter_level(zone, letter, default=None):
    """Sam numer poziomu 1..5 (``default`` dla nieznanej litery)."""
    slot = letter_slot(zone, letter)
    return slot.level if slot else default


def level_height_keys(zone, letter, level):
    """Klucze ``WarehouseRackType.level_heights`` w kolejności wyszukiwania.

    Półka/część poziomu (B/C/D w poz. 1, G/H w poz. 2) najpierw szuka własnej wysokości
    „1B”/„1C”/„1D”/„2G”/„2H”, potem wysokości całego poziomu „1”/„2”. Pełne miejsca → „<poziom>”."""
    slot = letter_slot(zone, letter)
    base, _ = _split_letter(letter, 0)
    keys = [str(level)]
    if slot and slot.shelf:
        keys.insert(0, f"{level}{base}")
    return keys


def level_height_mm(level_heights, zone, letter, level):
    """Wysokość miejsca [mm] ze słownika typu regału (patrz ``level_height_keys``);
    brak klucza → największa wysokość typu (dotychczasowy fallback); pusty słownik → 0."""
    lh = level_heights or {}
    for key in level_height_keys(zone, letter, level):
        if lh.get(key):
            return int(lh[key])
    return int(max(lh.values())) if lh else 0
