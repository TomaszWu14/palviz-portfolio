"""HU label printing (module "Wydruk HU").

A sequential label generator for the warehouse: pick a numbering project (Polska/Export,
Projekt Beta, Projekt Gamma, Delta…), enter how many labels, and stream a Zebra **ZPL** file — one
label per number: barcode(number) + the number + the project name.

Guarantees:
  • **No duplicate HU** — numbers are reserved under a row lock AND clamped above the
    highest number ever printed for the project, so a number can never be reissued (even
    if someone edits the counter backwards in the admin).
  • **Full audit** — every run records who printed how many, which range, and when
    (with a permanent username snapshot).

Gated by the "Magazyn" role (``_warehouse``).
"""
import re

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Sum
from django.http import Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ui.labels import iter_zpl_labels
from ui.models import HUPrintProject, HUPrintRun
from ui.platform_modules import module_required
from ui.roles import _warehouse

# Hard cap per run — well above the ~10 000 real batch size, guards against a typo
# reserving billions of numbers.
MAX_LABELS_PER_RUN = 100_000


@module_required("wydruk_hu")
def hu_print_home(request):
    """Print console: active projects + a quantity form, and a recent-runs summary."""
    projects = HUPrintProject.objects.filter(is_active=True)
    recent = HUPrintRun.objects.select_related("project", "user")[:15]
    total_printed = HUPrintRun.objects.aggregate(s=Sum("quantity"))["s"] or 0
    return render(request, "ui/hu_print/home.html",
                  {"projects": projects, "recent": recent, "total_printed": total_printed,
                   "max_labels": MAX_LABELS_PER_RUN})


@_warehouse
@require_POST
def hu_print_generate(request):
    """Reserve the next N numbers for a project (atomically, never reissuing a printed
    number) and stream the ZPL batch."""
    try:
        qty = int(request.POST.get("quantity", "0"))
    except (TypeError, ValueError):
        qty = 0
    symbology = "code39" if request.POST.get("symbology") == "code39" else "code128"

    if qty < 1:
        messages.error(request, "Podaj liczbę etykiet większą od zera.")
        return redirect("ui:hu_print_home")
    if qty > MAX_LABELS_PER_RUN:
        messages.error(request, f"Maksymalnie {MAX_LABELS_PER_RUN} etykiet na jeden wydruk.")
        return redirect("ui:hu_print_home")

    # Reserve the range under a row lock so concurrent prints never reuse numbers, AND
    # clamp the start above the highest number ever printed for this project — so even a
    # counter edited backwards in the admin can never reissue a printed HU.
    with transaction.atomic():
        # pk=<pusty/nienumeryczny> → na PostgreSQL DataError (500). Wymuszamy int (else 404).
        _pid = request.POST.get("project") or ""
        if not _pid.isdigit():
            raise Http404("Brak/niepoprawny projekt.")
        project = get_object_or_404(
            HUPrintProject.objects.select_for_update(), pk=_pid, is_active=True)
        highest_printed = project.runs.aggregate(m=Max("to_number"))["m"] or 0
        start = max(project.next_number, highest_printed + 1)
        end = start + qty - 1
        project.next_number = end + 1
        project.save(update_fields=["next_number"])
        HUPrintRun.objects.create(
            project=project, user=request.user, username=request.user.get_username(),
            quantity=qty, from_number=start, to_number=end, symbology=symbology)

    stream = iter_zpl_labels(project, range(start, end + 1), symbology)
    resp = StreamingHttpResponse(stream, content_type="application/octet-stream")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", project.name).strip("-") or "hu"
    resp["Content-Disposition"] = f'attachment; filename="{slug}_{start}-{end}.zpl"'
    return resp


@_warehouse
def hu_print_preview(request):
    """On-screen preview of what a label looks like for a project's next number — the
    linear barcode + number + name + the four corner fallback QR codes. Images are the
    live label PNGs (``ui:label_image``), so the preview matches what will print."""
    project = get_object_or_404(HUPrintProject, pk=request.GET.get("project"), is_active=True)
    symbology = "code39" if request.GET.get("symbology") == "code39" else "code128"
    highest_printed = project.runs.aggregate(m=Max("to_number"))["m"] or 0
    number = project.format_number(max(project.next_number, highest_printed + 1))
    return render(request, "ui/hu_print/preview.html",
                  {"project": project, "symbology": symbology, "number": number})


@_warehouse
def hu_print_log(request):
    """Full audit log: every print run (paginated) + per-user totals (kto i ile)."""
    runs = HUPrintRun.objects.select_related("project", "user").all()
    page = Paginator(runs, 100).get_page(request.GET.get("page"))
    # "kto i ile wydrukował" — total labels per user, all-time.
    per_user = (HUPrintRun.objects
                .values("username")
                .annotate(labels=Sum("quantity"), runs=Count("id"))
                .order_by("-labels"))
    total_printed = HUPrintRun.objects.aggregate(s=Sum("quantity"))["s"] or 0
    total_runs = HUPrintRun.objects.count()
    return render(request, "ui/hu_print/log.html",
                  {"page_obj": page, "per_user": per_user,
                   "total_printed": total_printed, "total_runs": total_runs})

__all__ = [
    "hu_print_home",
    "hu_print_generate",
    "hu_print_preview",
    "hu_print_log",
]
