# Transakcja kontroli: karta HU, start/next, bramki wymagan, wyjatki MD (Fala 4), finalizacja, reopen.

import logging
from ui.views.core import (
    GROUP_ADMIN, GROUP_MASTER_DATA, HandlingUnitItem, _controller, _leader, _safe_next,
    get_object_or_404, messages, redirect, render, require_POST,
)
from django.urls import reverse
from ui.models import Task
from .hu_helpers import _ensure_started  # noqa: F401
from .hu_count import _alt_conv, _annotate_picked_units, _count_tiles, _prefetch_instructions, _unit_factors  # noqa: F401
from .hu_helpers import _call_queue, _is_gls, _log_status, _maybe_escalate_recheck, _notify_groups, _raise_corrective_tasks, _reserve, _type_controlled, _valid_photo, _zone_ok  # noqa: F401
from .hu_quality import _notify_recheck  # noqa: F401
from .hu_queue import _recipient_siblings  # noqa: F401
from .hu_transaction_card import MD_ASPECTS  # noqa: F401

log = logging.getLogger(__name__)

@_controller
@require_POST
def hu_md_report(request, item_id):
    """Niezgodność master daty pozycji jako REGULARNE zgłoszenie na wzór MATINFO
    (PackagingIssue) — NIEblokujące kontroli i poza statystyką niezgodności inwentaryzacyjnych.
    Osobny tor/raport („Moje zgłoszenia" / reports_admin), nie kod błędu pozycji."""
    from ui.models import PackagingIssue
    it = get_object_or_404(HandlingUnitItem.objects.select_related("hu"), pk=item_id)
    if not _zone_ok(request.user, it.hu):
        return render(request, "ui/403.html", status=403)
    aspect = (request.POST.get("md_aspect") or "inne").strip()
    aspect_label = dict(MD_ASPECTS).get(aspect, "Inne")
    desc = (request.POST.get("md_desc") or "").strip()[:400]
    # AJM: operator wskazuje brakujący/poprawny przelicznik, np. 1 KARTON = 10 OP.
    if aspect == "ajm":
        u = (request.POST.get("md_unit") or "").strip().upper()[:10]
        f = (request.POST.get("md_factor") or "").strip()
        b = (request.POST.get("md_base") or "OP").strip().upper()[:10]
        if u and f:
            desc = (f"1 {u} = {f} {b}" + (f" — {desc}" if desc else ""))[:400]
    itype = "wrong_conversion" if aspect in ("przelicznik", "ajm") else "other"
    issue = PackagingIssue.objects.create(
        ref_code=(it.ref_code or "")[:50], product=it.product, issue_type=itype,
        description=f"[master data · {aspect_label}] {desc}"[:500],
        photo=_valid_photo(request), reporter=request.user)
    try:
        from ui.notifications import emit_event
        emit_event("matinfo_issue", {"issue_id": issue.pk, "issue_type": itype,
                   "issue_type_display": issue.get_issue_type_display(), "ref_code": it.ref_code})
        _notify_groups([GROUP_ADMIN, GROUP_MASTER_DATA],
                       f"Niezgodność master daty: {it.ref_code}",
                       desc or aspect_label, reverse("ui:phv_my_issues"), level="info")
    except Exception:
        log.exception("Zdarzenie/powiadomienie o niezgodności master daty nie wysłane")
    messages.success(request, f"Zgłoszono niezgodność master daty dla {it.ref_code} (zgłoszenie MATINFO).")
    return redirect("ui:hu_control_detail", pk=it.hu_id)


@_controller
@require_POST
def hu_item_md_exception(request, item_id):
    """Kontroler zgłasza niezgodność master daty pozycji (np. zły przelicznik, złe
    wymiary). Pozycja dostaje flagę wyjątku (NIE blokuje zamknięcia HU), a Master
    Data zadanie z linkiem — pętla uzupełniania master daty."""
    it = get_object_or_404(HandlingUnitItem.objects.select_related("hu"), pk=item_id)
    if not _zone_ok(request.user, it.hu):
        return render(request, "ui/403.html", status=403)
    _raise_md_exception(it, (request.POST.get("note") or "").strip())
    messages.success(request, f"Zgłoszono niezgodność master daty dla {it.ref_code}.")
    return redirect("ui:hu_control_detail", pk=it.hu_id)


def _raise_md_exception(it, note):
    """Oznacz pozycję wyjątkiem master daty + załóż zadanie Master Daty i powiadom grupy.
    Wspólne dla osobnego endpointu i liczenia inline (lista „Zgłoś błędy”)."""
    it.md_exception = True
    it.md_exception_note = (note or "")[:200]
    it.save(update_fields=["md_exception", "md_exception_note"])
    Task.objects.get_or_create(
        dedup_key=f"md_exc:{it.pk}"[:120],
        defaults=dict(
            title=f"Niezgodność master daty: {it.ref_code}",
            description=(f"Pozycja {it.ref_code} na HU {it.hu.ref}: {note or 'brak opisu'}. "
                         "Sprawdź i popraw master datę (Data Center), potem zamknij wyjątek "
                         "w panelu lidera."),
            category="md_exception", related_hu=it.hu, related_product=it.product,
            url=reverse("ui:hu_control_detail", args=[it.hu_id])))
    _notify_groups([GROUP_ADMIN, GROUP_MASTER_DATA],
                   f"Niezgodność master daty: {it.ref_code}",
                   note or "Zgłoszono z kontroli HU.", reverse("ui:hu_control_detail", args=[it.hu_id]))


def _close_md_task(item):
    Task.objects.filter(dedup_key=f"md_exc:{item.pk}"[:120]).update(status="done")


@_leader
@require_POST
def hu_item_md_exception_close(request, item_id):
    """Lider zamyka wyjątek (master data poprawiona) — flaga znika, zadanie done."""
    it = get_object_or_404(HandlingUnitItem.objects.select_related("hu"), pk=item_id)
    it.md_exception = False
    it.save(update_fields=["md_exception"])
    _close_md_task(it)
    _log_status(it.hu, it.hu.status, it.hu.status, request.user,
                note=f"Wyjątek MD zamknięty: {it.ref_code}", force=True)
    messages.success(request, f"Zamknięto wyjątek master daty ({it.ref_code}).")
    return _safe_next(request, "ui:hu_control_leader")


@_leader
@require_POST
def hu_item_md_exception_delete(request, item_id):
    """Lider usuwa pozycję zgłoszoną jako niezgodna z master datą (np. duplikat z
    feedu). Ślad zostaje w historii statusów HU (audyt)."""
    it = get_object_or_404(HandlingUnitItem.objects.select_related("hu"), pk=item_id)
    hu, ref = it.hu, it.ref_code
    _close_md_task(it)
    it.delete()
    _log_status(hu, hu.status, hu.status, request.user,
                note=f"Usunięto pozycję {ref} (wyjątek MD)", force=True)
    messages.success(request, f"Usunięto pozycję {ref} z HU {hu.ref}.")
    return _safe_next(request, "ui:hu_control_leader")

__all__ = [
    "hu_md_report",
    "hu_item_md_exception",
    "_raise_md_exception",
    "_close_md_task",
    "hu_item_md_exception_close",
    "hu_item_md_exception_delete",
]
