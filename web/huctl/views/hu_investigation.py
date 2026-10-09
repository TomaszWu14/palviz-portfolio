# Wyjaśnianie błędu (spec UX 2026-09-03 §3): start/stop pauzy licznika kontroli,
# potwierdzenie przez pickera/lidera (anty-nadużycie), akcje lidera.

import logging
from ui.views.core import (
    HandlingUnit, _controller, get_object_or_404, messages, redirect, settings,
)
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from core.roles import GROUP_WAREHOUSE
from ui.roles import (GROUP_ADMIN, GROUP_CONTROLLER, GROUP_LEADER,
                      _any_role, has_role, role_required)
from ..models_control import HUErrorInvestigation
from .hu_helpers import _log_status, _zone_ok

log = logging.getLogger(__name__)


def _confirm_window_min():
    return int(getattr(settings, "HU_INVESTIGATION_CONFIRM_MIN", 15))


def _can_confirm(user, inv):
    """Kto może potwierdzić błąd: lider/admin ALBO picker tej palety (login = pole
    picker z SAP, case-insensitive). Kontroler NIE potwierdza sam sobie."""
    if user.is_superuser or has_role(user, GROUP_LEADER) or has_role(user, GROUP_ADMIN):
        return True
    picker = (inv.hu.picker or "").strip().lower()
    return bool(picker) and user.get_username().lower() == picker


def _notify_confirmers(inv, request):
    """Komunikat do pickera (jeśli ma konto) + liderów: „potwierdź błąd"."""
    from django.contrib.auth import get_user_model
    from ui.notifications import notify
    url = reverse("ui:hu_investigation_detail", args=[inv.pk])
    targets = list(get_user_model().objects.filter(
        groups__name=GROUP_LEADER, is_active=True))
    picker = (inv.hu.picker or "").strip()
    if picker:
        pu = get_user_model().objects.filter(username__iexact=picker, is_active=True).first()
        if pu:
            targets.insert(0, pu)
    if targets:
        try:
            notify(targets, f"Potwierdź błąd na HU {inv.hu.ref} ({inv.get_error_type_display()})",
                   body=f"Kontroler {request.user.get_username()} rozpoczął wyjaśnianie — "
                        f"potwierdź, żeby pauza KPI była ważna.", level="warning", url=url)
        except Exception:
            log.exception("Powiadomienie o wyjaśnianiu błędu HU nie wysłane")


@_controller
@require_POST
def hu_investigation_start(request, pk):
    hu = get_object_or_404(HandlingUnit.objects.select_related("shipment"), pk=pk)
    if not _zone_ok(request.user, hu):
        messages.error(request, "Brak uprawnień do tej strefy.")
        return redirect("ui:hu_control_menu")
    if hu.investigations.filter(ended_at__isnull=True).exists():
        messages.info(request, "Wyjaśnianie tej palety już trwa.")
        return redirect("ui:hu_control_detail", pk=pk)
    # Bramka „po 2. liczeniu": wyjaśnianie tylko gdy jest pozycja z potwierdzonym błędem.
    if not hu.items.filter(result="error").exists():
        messages.warning(request, "Wyjaśnianie dopiero po potwierdzonym błędzie (2. liczenie).")
        return redirect("ui:hu_control_detail", pk=pk)
    etype = (request.POST.get("error_type") or "missing").strip()
    valid = {k for k, _ in HUErrorInvestigation.TYPES}
    # item TYLKO spośród pozycji TEJ palety (nienumeryczny/cudzy → pomijamy, nie 500).
    raw_item = (request.POST.get("item") or "").strip()
    item = hu.items.filter(pk=raw_item).first() if raw_item.isdigit() else None
    try:
        inv = HUErrorInvestigation.objects.create(
            hu=hu, item=item, controller=request.user,
            error_type=etype if etype in valid else "missing",
            note=(request.POST.get("note") or "")[:300])
    except IntegrityError:   # wyścig dwóch POST-ów o constraint one_open_investigation_per_hu
        messages.info(request, "Wyjaśnianie tej palety już trwa.")
        return redirect("ui:hu_control_detail", pk=pk)
    _log_status(hu, hu.status, hu.status, request.user,
                f"start wyjaśniania błędu ({inv.get_error_type_display()})", force=True)
    _notify_confirmers(inv, request)
    messages.success(request, "Wyjaśnianie rozpoczęte — licznik kontroli zatrzymany "
                              f"(wymaga potwierdzenia w {_confirm_window_min()} min).")
    return redirect("ui:hu_control_detail", pk=pk)


@_controller
@require_POST
def hu_investigation_end(request, pk):
    inv = get_object_or_404(HUErrorInvestigation.objects.select_related("hu"), pk=pk,
                            ended_at__isnull=True)
    if inv.controller_id != request.user.id and not has_role(request.user, GROUP_LEADER) \
            and not request.user.is_superuser:
        messages.error(request, "Wyjaśnianie może zakończyć jego autor albo lider.")
        return redirect("ui:hu_control_detail", pk=inv.hu_id)
    inv.ended_at = timezone.now()
    inv.save(update_fields=["ended_at"])
    _log_status(inv.hu, inv.hu.status, inv.hu.status, request.user,
                f"koniec wyjaśniania ({int(inv.duration_s() // 60)} min)", force=True)
    messages.success(request, "Wyjaśnianie zakończone — licznik kontroli wznowiony.")
    return redirect("ui:hu_control_detail", pk=inv.hu_id)


@_any_role
def hu_investigation_detail(request, pk):
    """Strona potwierdzenia (link z powiadomienia): lider/picker widzi kontekst i
    potwierdza albo odrzuca. GET = podgląd, akcje POST-em niżej."""
    inv = get_object_or_404(
        HUErrorInvestigation.objects.select_related("hu__shipment", "controller", "item"),
        pk=pk)
    # Odczyt tylko dla ról operacyjnych albo uprawnionych do potwierdzenia — role
    # podglądowe (Podgląd/Obsługa klienta) nie enumerują błędów cudzych stref.
    if not (_can_confirm(request.user, inv)
            or has_role(request.user, GROUP_CONTROLLER, GROUP_LEADER,
                        GROUP_ADMIN, GROUP_WAREHOUSE)):
        messages.error(request, "Brak dostępu do tego wyjaśniania.")
        return redirect("ui:hu_control_menu")
    from django.shortcuts import render
    return render(request, "ui/scanner/investigation_detail.html",
                  {"inv": inv, "can_confirm": _can_confirm(request.user, inv),
                   "window_min": _confirm_window_min()})


@_any_role
@require_POST
def hu_investigation_confirm(request, pk):
    inv = get_object_or_404(HUErrorInvestigation.objects.select_related("hu"), pk=pk)
    if not _can_confirm(request.user, inv):
        messages.error(request, "Błąd może potwierdzić picker tej palety albo lider.")
        return redirect("ui:hu_control_menu")
    if inv.rejected:
        messages.warning(request, "To wyjaśnianie zostało już ODRZUCONE przez lidera.")
        return redirect("ui:hu_investigation_detail", pk=pk)
    if not inv.confirmed_at and inv.is_overdue():
        messages.warning(request, f"Okno potwierdzenia ({inv.confirm_window_min()} min) minęło — "
                                  "czas wyjaśniania liczy się do kontroli.")
        return redirect("ui:hu_investigation_detail", pk=pk)
    if not inv.confirmed_at:
        inv.confirmed_by, inv.confirmed_at = request.user, timezone.now()
        inv.save(update_fields=["confirmed_by", "confirmed_at"])
        _log_status(inv.hu, inv.hu.status, inv.hu.status, request.user,
                    "błąd potwierdzony — pauza KPI ważna", force=True)
    messages.success(request, f"Błąd na HU {inv.hu.ref} potwierdzony.")
    return redirect("ui:hu_investigation_detail", pk=pk)


@role_required(GROUP_LEADER, GROUP_ADMIN)
@require_POST
def hu_investigation_reject(request, pk):
    inv = get_object_or_404(HUErrorInvestigation.objects.select_related("hu"), pk=pk)
    inv.rejected = True
    inv.confirmed_by, inv.confirmed_at = request.user, timezone.now()
    if not inv.ended_at:
        inv.ended_at = timezone.now()
    inv.save(update_fields=["rejected", "confirmed_by", "confirmed_at", "ended_at"])
    _log_status(inv.hu, inv.hu.status, inv.hu.status, request.user,
                "wyjaśnianie ODRZUCONE — czas wraca do licznika kontroli", force=True)
    messages.warning(request, "Wyjaśnianie odrzucone — czas liczy się do kontroli.")
    return redirect("ui:hu_investigation_detail", pk=pk)


__all__ = ["hu_investigation_start", "hu_investigation_end", "hu_investigation_detail",
           "hu_investigation_confirm", "hu_investigation_reject", "_can_confirm"]
