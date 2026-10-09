# Tabele stalych wyniesione z base.py (base_tables ◅ base). Czyste literaly.
import re as _re

SUFFIX_LEVEL = {
    'A': 0, 'B': 0,
    'C': 1, 'D': 2, 'C-1': 1, 'C-2': 1, 'D-1': 2, 'D-2': 2,
    'S': 1, 'T': 2, 'U': 3, 'V': 4, 'W': 5,
    'X': 1, 'Y': 2, 'Z': 3,
    # G/H = miejsce X podzielone pionowo (G niżej, H wyżej) → ta sama belka co X.
    'G': 1, 'H': 1,
}

PALLET_BASE_HEIGHT_CM = 15


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

# Cardboard face colours (12 triangles: bottom×2, top×2, front×2, back×2, left×2, right×2)
_CARTON_FC  = ["#c8a87e","#c8a87e",   # bottom – warm tan
               "#f5e6c8","#f5e6c8",   # top – light wheat
               "#deb887","#deb887",   # front – burlywood
               "#deb887","#deb887",   # back
               "#e8d4a8","#e8d4a8",   # left
               "#eddcb4","#eddcb4"]   # right

# Per-layer cardboard palette — alternating warm shades for visual depth
_CARTON_LAYER_PALETTES = [
    ("#c8a87e", "#f5e6c8", "#deb887", "#e8d4a8"),  # warm tan
    ("#b8986e", "#e0d0b0", "#c8a07a", "#d8c098"),  # deeper brown
    ("#d2b48c", "#faecd4", "#e8c89a", "#f0dab8"),  # caramel
    ("#a89070", "#dcc8b0", "#c09878", "#d0b898"),  # dark earth
]


_PALLET_BOARD_FC = [
    "#A07838","#A07838",  # bottom – dark wood
    "#EAD8B0","#EAD8B0",  # top – light pine
    "#C8A464","#C8A464",  # front
    "#C8A464","#C8A464",  # back
    "#D4B278","#D4B278",  # left
    "#D4B278","#D4B278",  # right
]
_PALLET_STRINGER_FC = [
    "#7A5520","#7A5520",  # bottom
    "#B88C48","#B88C48",  # top
    "#8B6030","#8B6030",  # front
    "#8B6030","#8B6030",  # back
    "#9E7038","#9E7038",  # left
    "#9E7038","#9E7038",  # right
]
_UNIT_FC = [
    "#86efac","#86efac",  # bottom
    "#4ade80","#4ade80",  # top – bright green
    "#34d399","#34d399",  # front
    "#34d399","#34d399",  # back
    "#6ee7b7","#6ee7b7",  # left
    "#6ee7b7","#6ee7b7",  # right
]
_INNER_PACK_FC = [
    "#93c5fd","#93c5fd",  # bottom
    "#60a5fa","#60a5fa",  # top – bright blue
    "#3b82f6","#3b82f6",  # front
    "#3b82f6","#3b82f6",  # back
    "#7dd3fc","#7dd3fc",  # left
    "#7dd3fc","#7dd3fc",  # right
]
_RACK_FC = [
    "#7c2d12","#7c2d12",   # bottom – dark orange-red
    "#ea580c","#ea580c",   # top – orange
    "#c2410c","#c2410c",   # front
    "#c2410c","#c2410c",   # back
    "#dc4a0a","#dc4a0a",   # left
    "#dc4a0a","#dc4a0a",   # right
]
_FACE_I = [0,0, 4,4, 0,0, 3,3, 0,0, 1,1]
_FACE_J = [1,2, 5,6, 1,5, 2,6, 3,7, 2,6]
_FACE_K = [2,3, 6,7, 5,4, 6,7, 7,4, 6,5]

# ── Sales-unit figure (opakowanie handlowe) ─────────────────────────────────
_SALES_FC = ["#fde68a","#fde68a", "#fcd34d","#fcd34d", "#f59e0b","#f59e0b",
             "#f59e0b","#f59e0b", "#fbbf24","#fbbf24", "#fbbf24","#fbbf24"]






# ═══════════════════════════════════════════════════════════════════════════════
#  Analytics
# ═══════════════════════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════════════════════
#  Celery task views
# ═══════════════════════════════════════════════════════════════════════════════







# ─── Warehouse 3D Map ─────────────────────────────────────────────────────────



# SAP WMS column name map (Polish SAP export)
_SAP_COLS = {
    "location":     ["miejsce składowania", "miejsce skladowania", "location", "lokalizacja", "adres lokalizacji"],
    "wh_type":      ["typ magazynu"],
    "section":      ["sekcja magazynu"],
    "storage_group":["podz. miej. skład.", "podz. miej. sklad.", "grupa składowania", "grupa skladowania"],
    "is_empty":     ["puste miejsce skład.", "puste miejsce sklad."],
    "blocked_pick": ["blok. wyd. z magaz."],
    "blocked_put":  ["blokada um. w magaz."],
    "capacity":     ["całkowite zdolności", "calkowite zdolnosci"],
    "aisle":        ["przej. w miej. sk."],
    "stack":        ["stos miejsca skład.", "stos miejsca sklad."],
    "level":        ["poziom miejsca skł.", "poziom miejsca skl."],
    # Simple format columns (location, product, quantity)
    "product":      ["product", "sku", "material", "materiał", "produkt", "towar"],
    "quantity":     ["quantity", "qty", "ilość", "ilosc", "menge", "ilosc_szt"],
    # Stock-at-location export: a handling unit present at a location ⇒ occupied
    "handling_unit":["jednostka obsługi", "jednostka obslugi", "handling unit", "hu",
                     "nr ho", "nr ho/hu", "lenum"],
}





_COL_IDX = {'A': 0, 'B': 1, 'C': 2, 'D': 3,
             'S': 0, 'T': 1, 'U': 2, 'V': 3, 'W': 4,
             'G': 0, 'H': 1,
             'X': 0, 'J': 1, 'K': 2,
             'Y': 0, 'L': 1, 'M': 2,
             'Z': 0, 'N': 1, 'O': 2}


















# ── Combined upload (layout cells + master data + rack types) ─────────────────

# Column alias mapping for the combined upload
_COMBINED_COL_ALIASES = {
    # Dopasowanie: substring, PIERWSZA pasująca komórka w kolejności kolumn wygrywa —
    # dzięki temu w surowym eksporcie EWM "typ"→"Typ magazynu" (kol. B) łapie się przed
    # "Typ st. miej. skł.", a "waga"→"Maksymalna waga" przed "Wykorzyst. waga".
    "location_code":  ["lokalizacja", "adres", "miejsce składowania", "miejsce",
                       "location", "bin", "location_code"],
    "warehouse_type": ["typ magazynu", "typ", "type", "rack_type", "typ_lok", "warehouse_type"],
    "height_mm":      ["wysokość", "wysokosc", "height", "clearance", "height_mm"],
    "width_mm":       ["szerokość", "szerokosc", "width", "width_mm"],
    "depth_mm":       ["głębokość", "glebokosc", "depth", "depth_mm"],
    "max_weight_kg":  ["maksymalna waga", "waga_max", "max_weight", "waga", "max_weight_kg"],
    # EWM: pełna nazwa z diakrytykami — samo "objetosc" nie łapie "Maksymalna objętość",
    # a generyczne "objętość" złapałoby wcześniejszą kolumnę "Objętość ładunku".
    "max_volume_m3":  ["maksymalna objętość", "objetosc_max", "max_volume", "objetosc",
                       "max_volume_m3"],
    "level_heights":  ["level_heights", "wysokosci_poziomow"],
    "level_cols":     ["level_cols", "kolumny_poziomow"],
}

# FALLBACK (col_idx, level) dla liter SPOZA konwencji EWM (legacy J/K/L/M/N/O) oraz
# kolumna boku dla 4-członowych kodów generatora (B0-01-100-2B). Litery EWM w kodach
# 3-członowych idą przez ewm_levels.letter_slot (jedno źródło prawdy litera → poziom).
_COL_CODE_MAP = {
    'A': (0, 1), 'B': (1, 1), 'C': (2, 1), 'D': (3, 1),
    'S': (0, 1), 'T': (1, 1), 'U': (2, 1), 'V': (3, 1),
    'G': (0, 1), 'H': (1, 1),
    'X': (0, 2), 'J': (1, 2), 'K': (2, 2),
    'Y': (0, 3), 'L': (1, 3), 'M': (2, 3),
    'Z': (0, 4), 'N': (1, 4), 'O': (2, 4),
}
















# ── Rack Type CRUD ────────────────────────────────────────────────────────────







# ═══════════════════════════════════════════════════════════════════════════════
#  Warehouse editor — 2D interactive map
# ═══════════════════════════════════════════════════════════════════════════════

# Module-level constants for editor3d (avoid re-allocating per request).
# Levels come from the canonical SUFFIX_LEVEL map (defined near the top).
_ED3D_SUFFIX_LEVEL  = SUFFIX_LEVEL
_ED3D_COMPACT       = {'S', 'T', 'U', 'V', 'W'}
_ED3D_STANDARD      = {'X', 'Y', 'Z'}
_ED3D_FLOOR         = {'A', 'B', 'C', 'D', 'C-1', 'C-2', 'D-1', 'D-2'}
_ED3D_PICKING_TYPES = {'0050', '0052'}
_ED3D_LOC_PAT       = _re.compile(r'^([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)-(\d+)-(\d+)([A-Z].*)$')








# ─── Shipment calculation helper ─────────────────────────────────────────────

# Palette of distinct hex colors for products in 3D viz (warm/earthy but distinct)
_PRODUCT_COLORS = [
    "#C8A87E", "#8FBC8F", "#F0C040", "#E07050", "#70A0C8",
    "#B07CC8", "#50B8A0", "#D4884C", "#6890B0", "#C8785C",
]





# ─── Carrier views ────────────────────────────────────────────────────────────









# ─── Shipment views ───────────────────────────────────────────────────────────

















# ─── User management (admin only) ─────────────────────────────────────────────











# ═══════════════════════════════════════════════════════════════════════════════
#  Picker Activity Heatmap
# ═══════════════════════════════════════════════════════════════════════════════



_DT_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%Y-%m-%dT%H:%M",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d %H:%M",
]

__all__ = [
    # SUFFIX_LEVEL: not in the pristine star-import union (no view/ file consumes it
    # unqualified), but the test suite imports it directly via `ui.views.SUFFIX_LEVEL`
    # (test_location_codes.py) — must stay bound + re-exported through base.py.
    'SUFFIX_LEVEL',
    'PALLET_BASE_HEIGHT_CM', '_CARTON_FC', '_CARTON_LAYER_PALETTES', '_COL_CODE_MAP',
    '_COL_IDX', '_COMBINED_COL_ALIASES', '_DT_FORMATS', '_ED3D_COMPACT', '_ED3D_FLOOR',
    '_ED3D_LOC_PAT', '_ED3D_PICKING_TYPES', '_ED3D_STANDARD', '_ED3D_SUFFIX_LEVEL',
    '_FACE_I', '_FACE_J', '_FACE_K', '_INNER_PACK_FC', '_PALLET_BOARD_FC',
    '_PALLET_STRINGER_FC', '_PRODUCT_COLORS', '_RACK_FC', '_SALES_FC', '_SAP_COLS',
    '_UNIT_FC', '_re',
]
