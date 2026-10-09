"""Motywy kolorystyczne stref (przewoźników) i typów wysyłki dla ekranów skanera.

Jedno źródło prawdy: szablon czyta gotowy dict, bez `{% if carrier == 'GLS' %}`
rozsianych po markupie. Framework-free (żadnych importów Django) — czyste dict + funkcje,
żeby dało się to testować i reużyć poza requestem.

Operator ma rozpoznawać strefę po kolorze, zanim przeczyta kod. Kolory bazują na marce
przewoźnika, przyciemnione do poziomu przechodzącego kontrast na jasnym tle (WCAG 4.5:1).
"""
import colorsys as _colorsys

# Kod strefy (HandlingUnit.warehouse_type, uppercase) → przewoźnik.
# Ustalone z użytkownikiem; nieznany/pusty kod → fallback GEIS (kolor bazowy aplikacji).
ZONE_CARRIER = {
    "92T3": "BUS",
    "92JU": "BUS",
    "92GE": "GEIS",
    "92EX": "EXPORT",
    "94GL": "GLS",   # (było błędnie „92GL" — realna strefa GLS to 94GL)
    # Strefy WC* (wydania całopaletowe) należą do TYCH SAMYCH procesów co 92*/94*
    # (decyzja 2026-09-02: 8 stref / 5 procesów) — bez tych wpisów spadały do
    # fallbacku GEIS i dostawały złą pigułkę.
    "WCEX": "EXPORT",
    "WCGE": "GEIS",
    "WCGL": "GLS",
}

# Pełny motyw per przewoźnik: kolory karty strefy + motyw całej aplikacji (§5 sekcja
# operatora). `carrier_label` to etykieta pigułki. Domyślny (fallback) = GEIS.
WAREHOUSE_THEMES = {
    "GLS": {
        "carrier_label": "GLS",
        # tuning: żółto-złoty (jaśniejszy niż musztarda) vs kontrast białego tekstu (min 4.5:1)
        "header_bg": "#8a6a06",
        "border": "#d9bd5c",
        "card_bg": "#fdf8e8",
        "ink": "#6b5200",
        "muted_num": "#a08f5e",
        "grid_bg": "#e5d69f",
        # motyw aplikacji (sekcja) — pasek górny przyciemniony do poziomu czytelnego z białym
        "app_bar_bg": "#5f4a00",
        "app_bar_text": "#ffeeae",
        "app_bg": "#fbf6e6",
        "summary_bg": "#f6efd6",
        "summary_border": "#d3bf7d",
        "pill_bg": "#ffd84d",
        "pill_text": "#3d2f00",
    },
    "BUS": {
        "carrier_label": "BUS",
        "header_bg": "#22317a",
        "border": "#9fb0e0",
        "card_bg": "#f2f5fd",
        "ink": "#22317a",
        "muted_num": "#8f9bc4",
        "grid_bg": "#ccd6ef",
        "app_bar_bg": "#1a2560",
        "app_bar_text": "#dbe4ff",
        "app_bg": "#f2f5fd",
        "summary_bg": "#e8edfb",
        "summary_border": "#9fb0e0",
        "pill_bg": "#98acec",
        "pill_text": "#141e52",
    },
    "GEIS": {
        # 92GE — przewoźnik nierozstrzygnięty (GEIS albo inny przewoźnik)
        "carrier_label": "GEIS/INNY",
        "header_bg": "#0f7d79",
        "border": "#a5d3cc",
        "card_bg": "#f3faf9",
        "ink": "#0c5c58",
        "muted_num": "#6b8d88",
        "grid_bg": "#cfe6e2",
        "app_bar_bg": "#0c5c58",
        "app_bar_text": "#d7f5ef",
        "app_bg": "#eef9f7",
        "summary_bg": "#e2f4f1",
        "summary_border": "#a5d3cc",
        "pill_bg": "#7fe3d8",
        "pill_text": "#063f3b",
    },
    "EXPORT": {
        "carrier_label": "EXPORT",
        "header_bg": "#8a2a86",
        "border": "#d9a8d6",
        "card_bg": "#fdf3fc",
        "ink": "#6e1f6b",
        "muted_num": "#a87ca6",
        "grid_bg": "#eccbea",
        "app_bar_bg": "#5f1a5c",
        "app_bar_text": "#f7dcf5",
        "app_bg": "#fdf3fc",
        "summary_bg": "#f7e6f5",
        "summary_border": "#d9a8d6",
        "pill_bg": "#e79ee3",
        "pill_text": "#4a1247",
    },
}

# Motyw badge'a wiersza HU wg typu wysyłki. PILNE jako jedyny z pełnym wypełnieniem —
# celowo, żeby wybijał się z listy przy przewijaniu. Delta fiolet spoza palety skanera,
# żeby nie mylił się z turkusowym VIP.
SHIPMENT_TYPE_THEMES = {
    # "ref" = kolor numeru HU w wierszu — celowo osobno od "edge": STANDARD ma jasną
    # krawędź (#dfe9e7), którą numer byłby nieczytelny na białym tle.
    "PILNE": {"edge": "#b3402f", "row_bg": "#fdf1ee", "ref": "#b3402f",
              "badge_bg": "#b3402f", "badge_text": "#ffffff", "badge_glyph": "●", "label": "PILNE"},
    "VIP": {"edge": "#13938b", "row_bg": "#eafaf7", "ref": "#0c6f67",
            "badge_bg": "#7fe3d8", "badge_text": "#063f3b", "badge_glyph": "★", "label": "VIP"},
    "DELTA": {"edge": "#6b4fa8", "row_bg": "#f3f0fb", "ref": "#4a3282",
               "badge_bg": "#e3dcf5", "badge_text": "#4a3282", "badge_glyph": "◆", "label": "DELTA"},
    "STANDARD": {"edge": "#dfe9e7", "row_bg": "", "ref": "#133b3a",
                 "badge_bg": "#eaf2f0", "badge_text": "#4a706c", "badge_glyph": "", "label": "STANDARD"},
}

DEFAULT_CARRIER = "GEIS"


def carrier_for(warehouse_type):
    """Kod strefy → przewoźnik. Nieznany/pusty → DEFAULT_CARRIER (GEIS)."""
    return ZONE_CARRIER.get((warehouse_type or "").upper(), DEFAULT_CARRIER)


def carrier_zone_filter(carrier):
    """Kody warehouse_type strefy kontroli → (codes, exclude).

    exclude=False: strefa = dokładnie `codes`. exclude=True (tylko DEFAULT_CARRIER/GEIS,
    bo nieznane kody spadają do niego): strefa = wszystko POZA `codes` innych stref.
    Użycie ORM: exclude → qs.exclude(warehouse_type__in=codes), inaczej filter(__in)."""
    carrier = (carrier or "").upper()
    if carrier == DEFAULT_CARRIER:
        return {z for z, c in ZONE_CARRIER.items() if c != DEFAULT_CARRIER}, True
    return {z for z, c in ZONE_CARRIER.items() if c == carrier}, False


# Etykieta pigułki nadpisana per STREFA (kolory zostają carrierowe) — 92T3 to odbiór
# własny klienta, choć kolorystycznie należy do grupy BUS.
ZONE_LABEL = {"92T3": "ODB. WŁASNY"}


def theme_for(warehouse_type):
    """Gotowy dict motywu strefy dla danego warehouse_type (fallback GEIS/teal)."""
    theme = WAREHOUSE_THEMES[carrier_for(warehouse_type)]
    label = ZONE_LABEL.get((warehouse_type or "").upper())
    return {**theme, "carrier_label": label} if label else theme


def theme_for_carrier(carrier):
    """Motyw wg kodu przewoźnika (np. UserProfile.section). Nieznany/pusty → None."""
    return WAREHOUSE_THEMES.get((carrier or "").upper())


# ── Kolor NAGŁÓWKA karty strefy: UNIKALNY per strefa (badge zostaje kolorem carriera) ──
# Dwie warstwy: badge = grupa/przewoźnik (WAREHOUSE_THEMES.pill/carrier), nagłówek = kolor
# przypisany deterministycznie per kod strefy. Framework-free.
_HUE_BUCKETS = 14          # 360/14 ≈ 25.7° → Δhue ≥ 25° między odcieniami
_HUE_STEP = 360 / _HUE_BUCKETS
# STONOWANE: niskie nasycenie — nagłówki są spokojne, odróżnialne subtelnym odcieniem,
# a jedynym mocnym akcentem jest badge przewoźnika. (Wcześniej sat 0.52 = zbyt kolorowo.)
_ZONE_SAT = 0.26
_ZONE_LIGHT = 0.28         # ciemne tło → biały tekst przechodzi kontrast (WCAG 4.5:1)


def _stable_hash(s):
    """Deterministyczny hash (FNV-1a) — stabilny między procesami/sesjami (inaczej niż hash())."""
    h = 2166136261
    for ch in (s or "").upper():
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h


def _hsl_hex(hue, sat, light):
    r, g, b = _colorsys.hls_to_rgb((hue % 360) / 360.0, light, sat)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _rel_luminance(hexcolor):
    c = hexcolor.lstrip("#")
    rgb = [int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    lin = [(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4) for v in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def zone_text_color(bg_hex):
    """Biały albo ciemny tekst na tle nagłówka — dobór wg luminancji (kontrast ≥ 4.5:1)."""
    return "#ffffff" if _rel_luminance(bg_hex) < 0.4 else "#0c2b2a"


def zone_colors_for(codes):
    """{code: {"bg","text"}} — UNIKALNE kolory dla zbioru stref: bazowy odcień per kod +
    de-kolizja (przesuwanie o krok odcienia). Deterministyczne dla stałego zbioru."""
    used, out = {}, {}
    for code in sorted({(c or "").upper() for c in codes if c}):
        b = _stable_hash(code) % _HUE_BUCKETS
        tried = 0
        while b in used and tried < _HUE_BUCKETS:
            b = (b + 1) % _HUE_BUCKETS
            tried += 1
        used[b] = code
        bg = _hsl_hex(b * _HUE_STEP, _ZONE_SAT, _ZONE_LIGHT)
        out[code] = {"bg": bg, "text": zone_text_color(bg)}
    return out


def shipment_type_for(hu):
    """Typ wysyłki HU → klucz SHIPMENT_TYPE_THEMES.

    Priorytet: is_priority → PILNE; inaczej kategoria klienta (vip/delta); reszta
    (export/beta/brak/None) → STANDARD. Odporne na brak shipment/customer.
    """
    if getattr(hu, "is_priority", False):
        return "PILNE"
    shipment = getattr(hu, "shipment", None)
    cust = getattr(shipment, "customer", None) if shipment else None
    if cust is None:
        return "STANDARD"
    cat = (getattr(cust, "category", "") or "").lower()
    if cat == "vip" or getattr(cust, "is_vip", False):
        return "VIP"
    if cat == "delta":
        return "DELTA"
    return "STANDARD"
