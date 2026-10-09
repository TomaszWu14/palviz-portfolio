# Wybór AKTYWNEJ strefy kontroli (EXPORT / BUS+odbiory własne / GLS / GEIS).
# Operator z przypisanymi strefami (UserProfile.allowed_sections) pracuje w jednej
# strefie naraz; przełączenie = powrót na ten ekran (badge w nagłówku skanera).

from ui.views.core import (ControllerZone, GROUP_ADMIN, GROUP_LEADER, _controller,
                           messages, redirect, render)
from ui.theme import WAREHOUSE_THEMES, carrier_for, carrier_zone_filter

# Nazwy stref na kaflach/komunikatach (ustalenia przebudowy 2026-09-01).
SECTION_TITLES = {"EXPORT": "Eksport", "BUS": "BUS + odbiory własne",
                  "GLS": "Paczka GLS", "GEIS": "GEIS"}


def _active_section(user):
    """Aktywna strefa kontroli (EXPORT/BUS/GLS/GEIS) użytkownika.

    None = bez filtra strefowego (lider/admin, superuser albo konto bez przypisanych
    `allowed_sections` — zachowanie sprzed przebudowy). "" = użytkownik MA przypisane
    strefy, ale żadna nie jest aktywna/dozwolona → musi wybrać strefę (zero dostępu)."""
    prof = getattr(user, "profile", None)
    if prof is None or not prof.allowed_sections:
        return None
    from ui.roles import has_role
    if user.is_superuser or has_role(user, GROUP_ADMIN) or has_role(user, GROUP_LEADER):
        return None
    sec = prof.section
    return sec if sec in prof.allowed_sections_list else ""


def _section_filter(qs, user, field="warehouse_type"):
    """Zawęź queryset do typów magazynu AKTYWNEJ strefy kontroli użytkownika."""
    sec = _active_section(user)
    if sec is None:
        return qs
    if not sec:
        return qs.none()
    codes, excl = carrier_zone_filter(sec)
    kw = {f"{field}__in": codes}
    return qs.exclude(**kw) if excl else qs.filter(**kw)


def _zone_ok(user, hu):
    """True if the user is permitted to control this HU's warehouse zone (type)
    AND the HU belongs to the user's active control section (if sections assigned)."""
    if not ControllerZone.can_control(user, hu.warehouse_type):
        return False
    sec = _active_section(user)
    if sec is None:
        return True
    return bool(sec) and carrier_for(hu.warehouse_type) == sec


def _needs_zone_select(user):
    """True gdy operator ma przypisane strefy, ale żadna nie jest aktywna/dozwolona."""
    return _active_section(user) == ""


@_controller
def hu_zone_select(request):
    prof = getattr(request.user, "profile", None)
    allowed = prof.allowed_sections_list if prof else []
    if not allowed:
        return redirect("ui:hu_control_menu")
    if request.method == "POST":
        sec = (request.POST.get("section") or "").strip().upper()
        if sec in allowed:
            prof.section = sec
            prof.save(update_fields=["section"])
            messages.success(request, f"Strefa kontroli: {SECTION_TITLES.get(sec, sec)}.")
            return redirect("ui:hu_control_menu")
        messages.error(request, "Nie masz uprawnień do tej strefy.")
    elif len(allowed) == 1:
        # Jedna dozwolona strefa — bez pytania: ustaw i wejdź od razu.
        if prof.section != allowed[0]:
            prof.section = allowed[0]
            prof.save(update_fields=["section"])
        return redirect("ui:hu_control_menu")
    tiles = [{"code": s, "title": SECTION_TITLES.get(s, s),
              "theme": WAREHOUSE_THEMES.get(s), "active": prof.section == s}
             for s in allowed]
    return render(request, "ui/scanner/zone_select.html", {"tiles": tiles})


__all__ = ["hu_zone_select", "_needs_zone_select", "_active_section",
           "_section_filter", "_zone_ok", "SECTION_TITLES"]
