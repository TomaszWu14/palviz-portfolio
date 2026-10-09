"""Ikony Lucide (sprite static/ui/icons/lucide.svg) — ZWYKŁY moduł, bez zależności od
maszynerii templatetags. Dzięki temu context processor może policzyć ikonę zwykłym
importem, bez wyścigu z ładowaniem biblioteki szablonów (partial import pod
--parallel/coverage powodował „ImportError: cannot import name 'nav_icon'").
Tagi {% icon %} / {% icon_button %} (palviz_extras) delegują tutaj."""
from django.templatetags.static import static
from django.utils.html import format_html
from django.utils.safestring import mark_safe

ICON_SIZES = (16, 20, 24)

# Klucz modułu (core.platform_modules.MODULES) → ikona Lucide. Jedno źródło dla kafla huba,
# nawigacji i launchera Ctrl+K. Pole `icon` (emoji) modułu zostaje dla starszych miejsc.
MODULE_ICONS = {
    "data_center": "database",
    "paletyzacja": "layers",
    "transport": "truck",
    "magazyn": "warehouse",
    "kontrola_hu": "scan-barcode",
    "wydruk_hu": "printer",
    "zadania": "bell",
    "klienci": "users",
    "zaria": "sparkles",
    "wysylka_ukraina": "globe",
    "phv": "boxes",
    "carton_opt": "package",
}
_FALLBACK = "layout-grid"


def icon_svg(name, size=16, label="", cls=""):
    """<svg><use href=sprite#name> — dekoracyjna (aria-hidden) albo z etykietą (role=img).
    Atrybuty obrysu inline, żeby działała też na ekranach bez app.css (skaner)."""
    size = int(size)
    if size not in ICON_SIZES:
        raise ValueError(f"icon: rozmiar {size} spoza {ICON_SIZES}")
    href = f"{static('ui/icons/lucide.svg')}#{name}"
    a11y = format_html('role="img" aria-label="{}"', label) if label else mark_safe('aria-hidden="true"')
    return format_html(
        '<svg class="icon icon--{} {}" width="{}" height="{}" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" focusable="false" {}>'
        '<use href="{}" xlink:href="{}"></use></svg>',
        size, cls, size, size, a11y, href, href,
    )


def module_icon_svg(key, size=20, cls=""):
    return icon_svg(MODULE_ICONS.get(str(key or ""), _FALLBACK), size, cls=cls)


def nav_icon_svg(key):
    """Ikona modułu w nawigacji (20 px)."""
    return module_icon_svg(key, 20, cls="topnav__ico")
