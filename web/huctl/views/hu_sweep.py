# Sweep porzuconych kontroli (wydzielone z hu_helpers.py — limit 500 linii, wzorzec W3).

import logging
from django.utils import timezone

from ui.views.core import HandlingUnit, HUControlAttempt, settings

log = logging.getLogger(__name__)


def _notify_release(hu, title, body):
    """Powiadom kontrolera, któremu sweep zabrał paletę — cicha utrata HU myliła
    operatorów (grill 2026-09-05, pyt. 23/54). Nigdy nie wywraca sweepa."""
    try:
        from django.urls import reverse
        from ui.notifications import notify
        who = hu.assigned_to or hu.controlled_by
        if who:
            notify([who], title, body=body, level="warning",
                   url=reverse("ui:hu_control_detail", args=[hu.pk]))
    except Exception:
        log.exception("Powiadomienie o zwolnieniu porzuconej HU nie wysłane")


def _release_abandoned_incontrol():
    """Porzucona kontrola wraca do kolejki: HU 'in_control' bez ŻADNEGO liczenia przez
    HU_INCONTROL_RELEASE_HOURS (start i ostatnia próba starsze niż próg) → status 'planned',
    rezerwacja zdjęta — auto-next znów ją poda. Bez tego paleta wisiała do ręcznego
    takeover lidera (stan bez wyjścia). Policzone pozycje zostają; przejście logowane.

    OTWARTA inwestygacja (wyjaśnianie błędu) liczy się jako aktywność — pauza dłuższa
    niż próg to legalna praca, nie porzucenie (incydent: sweep zabierał HU w trakcie
    wyjaśniania, bo pauza nie tworzy prób liczenia)."""
    from datetime import timedelta
    from django.db.models import Exists, OuterRef, Q
    from .hu_helpers import _log_status   # lazy: hu_helpers importuje ten moduł
    from ..models_control import HUErrorInvestigation
    hours = float(getattr(settings, "HU_INCONTROL_RELEASE_HOURS", 4) or 0)
    if hours <= 0:
        return
    cut = timezone.now() - timedelta(hours=hours)
    recent = HUControlAttempt.objects.filter(hu=OuterRef("pk"), created_at__gte=cut)
    # Otwarta LUB świeżo zakończona inwestygacja = kontroler nad paletą pracuje.
    investigating = HUErrorInvestigation.objects.filter(
        Q(ended_at__isnull=True) | Q(ended_at__gte=cut), hu=OuterRef("pk"))
    stale = (HandlingUnit.objects
             .filter(status="in_control", control_started_at__isnull=False,
                     control_started_at__lt=cut)
             .annotate(_active=Exists(recent), _inv=Exists(investigating))
             .filter(_active=False, _inv=False))
    from django.db import transaction as _tx
    for hu in stale:        # ponytail: pętla per HU dla logu audytu; rzędy to pojedyncze sztuki
        with _tx.atomic():
            # Lock + re-check: kontroler mógł WŁAŚNIE wznowić liczenie (świeży attempt
            # między SELECT-em sweepa a save) — bez tego sweep wypierał aktywną kontrolę.
            locked = (HandlingUnit.objects.select_for_update(of=("self",))
                      .filter(pk=hu.pk, status="in_control").first())
            if locked is None or locked.control_attempts.filter(created_at__gte=cut).exists():
                continue
            if locked.investigations.filter(Q(ended_at__isnull=True) |
                                            Q(ended_at__gte=cut)).exists():
                continue
            _notify_release(locked, f"Paleta {locked.ref} wróciła do kolejki",
                            f"Auto-zwrot: brak liczenia > {hours:g} h. "
                            "Policzone pozycje zostały zachowane.")
            locked.status = "planned"
            locked.assigned_to = None
            locked.called_at = None
            locked.save(update_fields=["status", "assigned_to", "called_at"])
            _log_status(locked, "in_control", "planned", None,
                        f"auto-zwrot do kolejki: brak liczenia > {hours:g}h", kind="release")
