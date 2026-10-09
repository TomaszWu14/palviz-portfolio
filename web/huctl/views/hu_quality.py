# Tor jakosci: zgloszenia, obcy towar, zalaczniki, powiadomienia skanera.

import logging
from ui.views.core import (
    GROUP_ADMIN, GROUP_LEADER, HandlingUnit, HUQualityIssue, JsonResponse, Product,
    _controller, _safe_next, get_object_or_404, messages, redirect, render,
    require_POST,
)
from django.utils import timezone
from django.urls import reverse
from ui.models import Task
from ui import master_data_client
from .hu_helpers import _controllable, _resolve_hu, _valid_photo, _zone_ok  # noqa: F401

log = logging.getLogger(__name__)


@_controller
def hu_prod_codes(request):
    """Autocomplete REF „nadmiarowy towar": top-20 indeksów dla q (min 2 znaki).
    Wcześniej CAŁY katalog był materializowany inline przy każdym otwarciu detalu HU
    — najcięższy element kontekstu skanera, dla rzadko używanego pola."""
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"products": []})
    prods = master_data_client.list_products(q=q, active=True, limit=20)
    return JsonResponse({"products": [{"code": p["code"], "name": p["name"]} for p in prods]})


@_controller
def hu_quality_issues(request):
    """Quality issues (damaged / wrong batch / foreign item…) — separate track,
    closed by a controller with a resolution note."""
    status = request.GET.get("status", "open")
    issues = (HUQualityIssue.objects
              .filter(hu__in=_controllable(request, HandlingUnit.objects.all()))
              .select_related("hu", "hu__shipment", "item", "raised_by", "closed_by")
              .order_by("-raised_at"))
    if status in ("open", "closed"):
        issues = issues.filter(status=status)
    return render(request, "ui/scanner/quality_issues.html",
                  {"issues": issues, "status": status})


@_controller
@require_POST
def hu_quality_close(request, issue_id):
    iss = get_object_or_404(HUQualityIssue.objects.select_related("hu"), pk=issue_id)
    if not _zone_ok(request.user, iss.hu):     # lista jest filtrowana — deep-link też musi być
        messages.error(request, f"Brak uprawnień do kontroli w strefie „{iss.hu.warehouse_type or '—'}”.")
        return redirect("ui:hu_quality_issues")
    # A resolution note is mandatory — closing must record what was done (audit trail).
    note = (request.POST.get("resolution_note") or "").strip()
    if not note:
        messages.error(request, "Podaj opis rozwiązania — bez niego nie zamkniesz zgłoszenia.")
        return _safe_next(request, "ui:hu_quality_issues")
    iss.status = "closed"
    iss.closed_by = request.user
    iss.closed_at = timezone.now()
    iss.resolution_note = note[:300]
    iss.save(update_fields=["status", "closed_by", "closed_at", "resolution_note"])
    # Zwrotka do zgłaszającego (grill 2026-09-05, pyt. 29): operator ma wiedzieć, co się
    # stało z jego zgłoszeniem — bez tego pętla zgłoszeń kończyła się ciszą.
    if iss.raised_by and iss.raised_by != request.user:
        try:
            from ui.notifications import notify
            notify([iss.raised_by],
                   f"Zgłoszenie jakościowe zamknięte (HU {iss.hu.ref})",
                   body=f"{iss.get_issue_type_display()}: {note[:200]}",
                   level="info", url=reverse("ui:hu_quality_issues"))
        except Exception:
            log.exception("Powiadomienie o zamknięciu zgłoszenia jakościowego nie wysłane")
    messages.success(request, "Zgłoszenie jakościowe zamknięte.")
    return _safe_next(request, "ui:hu_quality_issues")


@_controller
@require_POST
def hu_quality_add_foreign(request, pk):
    """Report a foreign/extra item found on the pallet (REF + qty, name suggested)."""
    hu = get_object_or_404(HandlingUnit, pk=pk)
    if not _zone_ok(request.user, hu):
        messages.error(request, f"Brak uprawnień do kontroli w strefie „{hu.warehouse_type or '—'}”.")
        return redirect("ui:hu_control_menu")
    ref = (request.POST.get("ref_code") or "").strip()[:50]
    if not ref:
        messages.error(request, "Podaj REF obcego towaru.")
        return redirect("ui:hu_control_detail", pk=pk)
    # Obowiązkowe wskazanie MIEJSCA znaleziska: HU (skan/pickHU, domyślnie bieżąca) ALBO
    # świadomy wybór „towar luzem / poza HU". Bez tego zgłoszenie nie przechodzi.
    loose = request.POST.get("loose") == "1"
    hu_code = (request.POST.get("hu_code") or "").strip()
    if hu_code and hu_code.lower() not in (hu.code.lower(), hu.ref.lower()):
        found = _resolve_hu(hu_code)
        if found and _zone_ok(request.user, found):
            hu = found                              # zgłoszenie wiążemy z realnie wskazaną HU
        elif not loose:
            messages.error(request, f"Nie znaleziono HU „{hu_code}” — zeskanuj albo zaznacz „towar luzem”.")
            return redirect("ui:hu_control_detail", pk=pk)
    elif not hu_code and not loose:
        messages.error(request, "Wskaż HU (skan) albo zaznacz „towar luzem / poza HU”.")
        return redirect("ui:hu_control_detail", pk=pk)
    try:
        qty = float((request.POST.get("qty") or "0").replace(",", "."))
    except ValueError:
        qty = 0.0
    prod = Product.objects.filter(code=ref).first()
    _note = (("[LUZEM] " if loose else "") + (prod.name if prod else ""))[:300]
    HUQualityIssue.objects.create(hu=hu, issue_type="foreign_item", ref_code=ref, qty=qty,
                                  note=_note, raised_by=request.user,
                                  photo=_valid_photo(request))
    # Auto-raise a top-priority team task: put the excess/foreign goods back to the source
    # location it was picked from. Team task (assignee NULL) so the checking operators see
    # it among the most urgent; dedup_key prevents duplicates for the same HU+REF.
    dedup = f"foreign_return:{hu.pk}:{ref}"
    if not Task.objects.filter(dedup_key=dedup).exclude(status="done").exists():
        Task.objects.create(
            title=f"Odłóż nadmiarowy/obcy towar {ref} (HU {hu.ref})"[:200],
            description=(f"Kontrola HU {hu.ref} (lok. {hu.location or '—'}): obcy/nadmiarowy "
                        f"towar {ref}{(' — ' + prod.name) if prod else ''}, ilość {qty:g}. "
                        f"Odłóż nadmiar do lokalizacji źródłowej, z której go pobrano."),
            category="manual", priority="high",
            source_ref=f"HU {hu.ref}"[:80], related_hu=hu, related_product=prod,
            # Durable business-key reference (survives the master-data DB split); the operator
            # typed the REF, so it's the product code even when no local Product row matches.
            related_product_code=(prod.code if prod else ref)[:50],
            related_location=(hu.location[:50] if hu.location else ""),
            dedup_key=dedup[:120], created_by=request.user,
            url=reverse("ui:hu_control_detail", args=[hu.pk])[:300])
    messages.success(request, f"Zgłoszono obcy/nadmiarowy towar: {ref}. Utworzono pilne zadanie odłożenia.")
    return redirect("ui:hu_control_detail", pk=pk)


@_controller
@require_POST
def hu_quality_attach(request, issue_id):
    """Attach a camera photo to an existing quality issue (proof for damaged etc.)."""
    iss = get_object_or_404(HUQualityIssue.objects.select_related("hu"), pk=issue_id)
    if not _zone_ok(request.user, iss.hu):     # jak przy zamykaniu — bramka też na deep-linku
        messages.error(request, f"Brak uprawnień do kontroli w strefie „{iss.hu.warehouse_type or '—'}”.")
        return redirect("ui:hu_quality_issues")
    photo = _valid_photo(request)
    if photo:
        iss.photo = photo
        iss.save(update_fields=["photo"])
        messages.success(request, "Dodano zdjęcie do zgłoszenia.")
    return _safe_next(request, "ui:hu_quality_issues")


@_controller
def hu_notifications(request):
    """Powiadomienia na skanerze (P1 #12): lista własnych powiadomień operatora.
    Wejście na listę oznacza wszystkie jako przeczytane (znika badge przy dzwonku) —
    skaner nie ma osobnego 'oznacz przeczytane', to byłby drugi tap na małym ekranie."""
    from ui.models import Notification
    items = list(Notification.objects.filter(recipient=request.user)[:50])
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return render(request, "ui/scanner/notifications.html", {"items": items})


def _notify_recheck(hu, by_user):
    """Mark a discrepancy HU as priority and ping leaders/admins in-app (best-effort)."""
    try:
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from ui.notifications import notify
        leaders = (get_user_model().objects
                   .filter(groups__name__in=[GROUP_ADMIN, GROUP_LEADER], is_active=True)
                   .distinct())
        if leaders:
            who = by_user.get_full_name() or by_user.get_username()
            notify(leaders, f"HU {hu.ref} → do rekontroli (priorytet)",
                   body=f"Niezgodność wykryta przez {who}. Lokalizacja: {hu.location or '—'}.",
                   level="warning", url=reverse("ui:hu_control_detail", args=[hu.pk]))
        # P1 #12: powiadom też kontrolera, którego liczenie zakwestionowano (widzi to
        # na dzwonku skanera) — o ile to nie on sam zgłosił niezgodność.
        ctrl = hu.controlled_by
        if ctrl and ctrl.is_active and ctrl != by_user:
            notify([ctrl], f"HU {hu.ref} wraca do rekontroli",
                   body=f"Twoje liczenie zostało zakwestionowane. Lokalizacja: {hu.location or '—'}.",
                   level="warning", url=reverse("ui:hu_control_detail", args=[hu.pk]))
    except Exception:
        log.exception("Powiadomienie o rekontroli HU nie wysłane")

__all__ = [
    "hu_prod_codes",
    "hu_quality_issues",
    "hu_quality_close",
    "hu_quality_add_foreign",
    "hu_quality_attach",
    "hu_notifications",
    "_notify_recheck",
]
