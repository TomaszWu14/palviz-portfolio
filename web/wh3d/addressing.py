"""Adresy miejsc paletowych modelu magazynu: szablon gniazda + reguła rzędu + wyjątki.

Czysty Python (bez ORM): funkcje czytają atrybuty obiektów, więc przyjmują zarówno
instancje modeli (BayTemplate / WarehouseModelRack / LocationOverride), jak i proste
obiekty w testach. Konwencja kodu EWM: `{strefa}-{przejście}-{gniazdo:02}{pozycja}{litera}`
+ `-1`/`-2` dla połówek, np. `B0-07-300C-1`.
"""
import re
from collections import defaultdict

CODE_RE = re.compile(r"^([A-Z0-9]+)-([A-Z0-9]+)-(\d{2})(\d)([A-Z])(?:-([12]))?$")
# Kolejność liter od podłogi w górę (B0: A–D/S = poziom 1 — B/C/D to półki jedna nad drugą,
# X/G/H/T = 2 — G i H to X podzielone pionowo, G niżej; Y/U = 3, Z/V = 4, W = 5).
# Musi być zgodna z ui.views.core.ewm_levels.letter_slot (pilnuje tego test).
LETTER_ORDER = "ABCDSXGHTYUZVW"


def letter_rank(letter):
    """Klucz sortowania liter poziomów: znane litery wg LETTER_ORDER, obce na końcu."""
    i = LETTER_ORDER.find(letter)
    return (i if i >= 0 else len(LETTER_ORDER), letter)


def parse_code(code):
    """Kod EWM → (strefa, przejście, gniazdo, pozycja, litera, połówka) albo None."""
    m = CODE_RE.match((code or "").strip().upper())
    if not m:
        return None
    zone, aisle, bay, pos, letter, half = m.groups()
    return zone, aisle, int(bay), int(pos), letter, int(half or 0)


def make_code(zone, aisle, bay, position, letter, half=0):
    return f"{zone}-{aisle}-{bay:02d}{position}{letter}" + (f"-{half}" if half else "")


def parse_bay_numbers(text):
    """„10-47,50” → [10, …, 47, 50]. Pusty tekst → []. Błędny zapis → ValueError."""
    out = []
    for part in (text or "").replace(" ", "").split(","):
        if not part:
            continue
        lo, sep, hi = part.partition("-")
        if not lo.isdigit() or (sep and not hi.isdigit()):
            raise ValueError(f'Nieprawidłowy zakres gniazd: „{part}”')
        lo, hi = int(lo), int(hi) if sep else int(lo)
        if hi < lo:
            raise ValueError(f'Zakres malejący: „{part}”')
        if hi - lo >= 1000:
            raise ValueError(f'Zakres zbyt długi: „{part}” (maks. 1000 gniazd)')
        if len(out) + hi - lo + 1 > 1000:
            raise ValueError("Za dużo gniazd w numeracji (maks. 1000)")
        out.extend(range(lo, hi + 1))
    if len(set(out)) != len(out):
        raise ValueError("Numery gniazd się powtarzają")
    return out


def format_bay_numbers(numbers):
    """[10, …, 47, 50] → „10-47,50” (odwrotność parse_bay_numbers)."""
    parts, nums, i = [], sorted(numbers), 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ",".join(parts)


def row_bay_numbers(row):
    """Numery gniazd rzędu w kolejności fizycznej; pusta reguła = 1..n_bays; nadmiar ucinany."""
    nums = parse_bay_numbers(row.bay_numbers)
    return nums[: row.n_bays] if nums else list(range(1, row.n_bays + 1))


def _bay_locations(row, bay, slot, tpl, loc_ov):
    """Miejsca jednego gniazda (numer `bay`, fizyczny indeks `slot`) wg szablonu i wyjątków miejsca."""
    bay_w = row.bay_width_cm / 100
    k = max(1, tpl.pallets_per_beam)
    pos_w = bay_w / k
    level_at, z = {}, 0.0
    for li, lvl in enumerate(tpl.levels):
        level_at[lvl["letter"]] = (li, z)
        z += lvl["height_mm"] / 1000
    cells = []                                   # (pozycja, litera, połówka, typ EWM)
    for lvl in tpl.levels:
        for position in range(k):
            ov = loc_ov.get((bay, position, lvl["letter"], 0), {})
            split = "split" in ov or (lvl.get("split") and "unsplit" not in ov)
            cells += [(position, lvl["letter"], h, lvl.get("ewm_type", "")) for h in ((1, 2) if split else (0,))]
    cells += [(p, letter, h, ov["add"].value) for (b, p, letter, h), ov in loc_ov.items()
              if b == bay and "add" in ov]
    out = []
    for position, letter, half, ewm_type in cells:
        ov = loc_ov.get((bay, position, letter, half), {})
        if "skip" in ov:
            continue
        li, z_m = level_at.get(letter, (len(tpl.levels), z))
        along = slot * bay_w + position * pos_w + (pos_w / 2 if not half else (half - 0.5) * pos_w / 2)
        out.append({
            "code": ov["rename"].value if "rename" in ov
            else make_code(row.zone, row.rack_id, bay, position, letter, half),
            "bay": bay, "position": position, "level_index": li, "letter": letter, "half": half,
            "ewm_type": ov["ewm_type"].value if "ewm_type" in ov else ewm_type,
            "blocked": "block" in ov,
            # reverse: numeracja od końca rzędu → lustrzane położenie wzdłuż rzędu
            "along_m": round(bay_w * row.n_bays - along if row.reverse else along, 3),
            "z_m": round(z_m, 3),
        })
    return out


def expand_row(row, template, overrides=()):
    """Rząd + szablon domyślny + wyjątki → lista miejsc (dict).

    Klucze: code, bay, position, level_index, letter, half, ewm_type, blocked, along_m, z_m.
    Wyjątki gniazda (letter == ""): `template` (inny szablon), `skip` (gniazdo bez miejsc).
    Wyjątki miejsca: skip / add / rename / ewm_type / block / split / unsplit."""
    bay_ov, loc_ov = defaultdict(dict), defaultdict(dict)
    for ov in overrides:
        if ov.letter:
            loc_ov[(ov.bay, ov.position, ov.letter, ov.half)][ov.action] = ov
        else:
            bay_ov[ov.bay][ov.action] = ov
    out = []
    for slot, bay in enumerate(row_bay_numbers(row)):
        b_ov = bay_ov.get(bay, {})
        tpl = b_ov["template"].template if "template" in b_ov else template
        if "skip" in b_ov or tpl is None:
            continue
        out += _bay_locations(row, bay, slot, tpl, loc_ov)
    return out


def expand_model(rows):
    """[(rząd, szablon, wyjątki), …] → (miejsca z kluczami zone/aisle, {kod: [„B0-07”, …]} duplikatów)."""
    locations, seen = [], defaultdict(list)
    for row, template, overrides in rows:
        for loc in expand_row(row, template, overrides):
            loc["zone"], loc["aisle"] = row.zone, row.rack_id
            locations.append(loc)
            seen[loc["code"]].append(f"{row.zone}-{row.rack_id}")
    return locations, {c: racks for c, racks in seen.items() if len(racks) > 1}
