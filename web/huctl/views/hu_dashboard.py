# Pulpit „Moja zmiana" — hybryda pracy kontrolera (spec: docs/superpowers/specs/
# 2026-09-03-hu-control-ux-design.md §1): domyślnie „WEŹ NASTĘPNĄ" wg rankingu,
# skan w każdej chwili przejmuje kontekst (istniejące hu_control_scan).

from ui.views.core import (
    HandlingUnit, _controller, messages, redirect, render,
)
from django.db import transaction
from django.views.decorators.http import require_POST
from ui.theme import theme_for_carrier, WAREHOUSE_THEMES
from .hu_helpers import _call_queue, _controllable, _reserve
from .hu_zone import SECTION_TITLES, _active_section, _needs_zone_select
from ..queue_rank import _key, families_in_progress, order_queue, queue_badge, rank_key


def _own_active(user):
    """„Dokończ swoją": moja paleta już w kontroli (najdawniej rozpoczęta) albo None.
    Oba wejścia kolejki (Następna HU / Weź następną) wracają do niej przed kolejką."""
    return (HandlingUnit.objects.filter(status="in_control", controlled_by=user)
            .order_by("control_started_at").first())


def _queue(request):
    """JEDNA kolejka (BIZ-005, Q-42) dla „Następna HU" i „Weź następną":
    filtr = `_call_queue` (strefa/typ, wolne albo moje, snooze w przyszłości ukrywa),
    kolejność = `order_queue` (moje rezerwacje → VIP → PILNE → rekontrole → rodziny → SLA).

    Do Pythona trafiają TYLKO HU aktywne — historia (ok/escaped) rosła bez końca
    (PERF-003). Dla rodzin z niej potrzebne są wyłącznie klucze odbiorców (DISTINCT)."""
    base = _controllable(request, HandlingUnit.objects.all())
    from .hu_helpers import _without_cancelled_planned   # BIZ-009: bez planned anulowanych
    all_hus = list(_without_cancelled_planned(
        base.filter(status__in=("planned", "in_control", "to_recheck")))
                   .select_related("shipment__customer"))
    done_keys = {_key(*row) for row in base.filter(status__in=("ok", "escaped"))
                 .values_list("shipment__kunnr", "shipment__recipient_name", "recipient_type")
                 .distinct()}
    fam = families_in_progress(all_hus, done_keys - {""})
    takeable = order_queue(list(_call_queue(request)), fam, request.user.id)
    return all_hus, takeable, fam


@_controller
def hu_my_shift(request):
    if _needs_zone_select(request.user):
        return redirect("ui:hu_zone_select")
    all_hus, takeable, fam = _queue(request)
    mine = [h for h in all_hus
            if (h.assigned_to_id == request.user.id and h.status == "planned")
            or (h.controlled_by_id == request.user.id and h.status == "in_control")]
    # „Następna" = dokładnie to, co da „Weź następną": najpierw moja paleta w kontroli.
    own = _own_active(request.user)
    nxt = own or (takeable[0] if takeable else None)
    nxt_badge = "dokończ swoją" if own else (queue_badge(nxt, fam, request.user.id) if nxt else "")
    # Trwające wyjaśnianie błędu mojego autorstwa — karta z licznikiem (spec §1.2).
    from ..models_control import HUErrorInvestigation
    my_inv = (HUErrorInvestigation.objects
              .filter(controller=request.user, ended_at__isnull=True)
              .select_related("hu", "confirmed_by").first())
    sec = _active_section(request.user)
    theme = theme_for_carrier(sec) or WAREHOUSE_THEMES["GEIS"]
    return render(request, "ui/scanner/my_shift.html", {
        "mine": mine, "my_inv": my_inv, "queue_count": len(takeable), "next_hu": nxt,
        "next_badge": nxt_badge, "section": sec,
        "section_title": SECTION_TITLES.get(sec, "wszystkie strefy"),
        "theme": theme,
    })


@_controller
@require_POST
def hu_take_next(request):
    """„WEŹ NASTĘPNĄ": najpierw moja paleta w kontroli („dokończ swoją"), potem pierwsza
    wg wspólnej kolejki (`_queue`) — ten sam wybór co „Następna HU"."""
    own = _own_active(request.user)
    if own:
        messages.info(request, f"Masz już paletę w kontroli ({own.ref}) — dokończ ją.")
        return redirect("ui:hu_control_detail", pk=own.pk)
    hu = _take_first(request, "wywołanie (Weź następną)")
    if hu:
        return redirect("ui:hu_control_detail", pk=hu.pk)
    messages.info(request, "Kolejka pusta — brak palet do wzięcia.")
    return redirect("ui:hu_my_shift")


def _take_first(request, note):
    """Rezerwuje pierwszą paletę wspólnej kolejki atomowo (pierwszy wygrywa); przy
    wyścigu bierze kolejną z czołówki zamiast błędu. Zwraca HU albo None (pusto)."""
    _, takeable, _f = _queue(request)
    for cand in takeable[:5]:  # wyścig dwóch kontrolerów → próbuj kolejne z czołówki
        with transaction.atomic():
            hu = (HandlingUnit.objects.select_for_update(of=("self",))
                  .select_related("shipment").filter(pk=cand.pk).first())
            if hu is None:                     # HU usunięta między rankingiem a lockiem
                continue
            if (hu.assigned_to_id not in (None, request.user.id)
                    or hu.status not in ("planned", "to_recheck")):
                continue
            if _reserve(hu, request.user, note):
                return hu
    return None


__all__ = ["hu_my_shift", "hu_take_next", "rank_key", "families_in_progress"]
