from django import template

register = template.Library()


@register.filter
def pl_palety(n):
    """Polska odmiana rzeczownika „paleta" wg liczby: 1 paleta / 2–4 palety / 5+ palet
    (z wyjątkiem nastek 12–14 → palet)."""
    try:
        n = int(n)
    except (ValueError, TypeError):
        return "palet"
    if n == 1:
        return "paleta"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "palety"
    return "palet"


@register.filter
def get_item(d, key):
    """Look up a dict value by a variable key in templates (e.g. JSON error_flags)."""
    try:
        return d.get(key)
    except (AttributeError, TypeError):
        return None


# ── Zestaw ikon PV (Claude Design 2026-08): emoji → spójny PNG 24px-grid ─────────
# 98 emoji UI ma odpowiedniki w static/ui/icons/set/{dark,light}/U<kody>.png.
# Filtr podmienia emoji na <img>, a gdy ikony brak — zostawia emoji (fallback),
# więc wdrożenie jest bezpieczne i stopniowe.
import os as _os
from django.templatetags.static import static as _static
from django.utils.safestring import mark_safe as _ms

_ICON_DIR = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                          "static", "ui", "icons", "set", "dark")
try:
    _ICON_SET = {f[1:-4] for f in _os.listdir(_ICON_DIR) if f.startswith("U")}
except OSError:
    _ICON_SET = set()


@register.filter
def pv_icon(char, size=24):
    """Emoji → <img> z zestawu ikon (wariant dark). Nieznane emoji → oryginalny znak."""
    s = str(char or "").strip()
    if not s:
        return ""
    code = "-".join(f"{ord(c):04X}" for c in s if ord(c) >= 0x2000)
    # Flagi (para regional indicators) i warianty z FE0F: spróbuj też pierwszego znaku.
    for cand in (code, code.split("-")[0] if "-" in code else None):
        if cand and cand in _ICON_SET:
            url = _static(f"ui/icons/set/dark/U{cand}.png")
            return _ms(f'<img src="{url}" width="{size}" height="{size}" alt="{s}" '
                       f'style="vertical-align:-{int(size)//5}px;border-radius:20%">')
    return s


# ── Ikony Lucide (etap 3 audytu UX): sprite static/ui/icons/lucide.svg ────────
# {% icon "package" %} → dekoracyjna (aria-hidden); {% icon "trash-2" label="Usuń" %} →
# znacząca (role=img + aria-label). Rozmiary 16/20/24. Atrybuty obrysu inline, żeby ikona
# działała też na ekranach bez app.css (skaner). Nowe ikony: audit/tools/build_icons.py.
from django.template import TemplateSyntaxError as _TSE
from django.utils.html import format_html as _fh

from ui.nav_icons import icon_svg as _icon_svg, module_icon_svg as _module_icon_svg


@register.simple_tag
def icon(name, size=16, label="", cls=""):
    try:
        return _icon_svg(name, size, label, cls)
    except ValueError as e:
        raise _TSE(str(e)) from e


@register.filter
def module_icon(key, size=24):
    """Klucz modułu → ikona Lucide (kafel huba, launcher). Mapa: ui/nav_icons.MODULE_ICONS."""
    return _module_icon_svg(key, size)


@register.simple_tag
def icon_button(name, label="", href="", variant="btn-ghost", size="", type="button",
                confirm="", testid=""):
    """Przycisk z samą ikoną. `label` OBOWIĄZKOWY (aria-label + title) — bez niego czytnik
    ekranu ogłasza „przycisk" bez nazwy (axe button-name). `confirm` → modal potwierdzenia."""
    if not label:
        raise _TSE(f"icon_button '{name}': brak label (nazwa dostępna jest obowiązkowa)")
    cls = f"btn btn-icon {variant}" + (f" btn-{size}" if size else "")
    extra = _ms((_fh(' data-confirm="{}"', confirm) if confirm else "")
                + (_fh(' data-testid="{}"', testid) if testid else ""))
    ico = icon(name, 16)
    if href:
        return _fh('<a href="{}" class="{}" aria-label="{}" title="{}"{}>{}</a>',
                   href, cls, label, label, extra, ico)
    return _fh('<button type="{}" class="{}" aria-label="{}" title="{}"{}>{}</button>',
               type, cls, label, label, extra, ico)
