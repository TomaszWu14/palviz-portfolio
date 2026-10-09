# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from .core import (
    _any_role, get_object_or_404, PalletizationInstruction, ErrorReportForm, settings,
    send_mail, logging, render, _planner, ErrorReport, Paginator, has_role,
    GROUP_ADMIN, GROUP_MASTER_DATA, ErrorReportStatusForm, messages, redirect
)
from .core.base import _pk4  # int4-safe pk (overflow/nienumeryczny → 404, nie DataError 500)


@_any_role
def report_error(request, instr_id: int):
    instr = get_object_or_404(PalletizationInstruction, id=_pk4(instr_id))
    if request.method == "POST":
        form = ErrorReportForm(request.POST)
        if form.is_valid():
            report = form.save(commit=False)
            report.instruction = instr
            report.product_code = instr.product.code
            report.save()
            # Send email notification (if configured)
            try:
                admin_email = getattr(settings, "ADMIN_REPORT_EMAIL", None)
                if admin_email:
                    send_mail(
                        subject=f"[{settings.APP_NAME}] Nowe zgłoszenie błędu — {instr.product.code}",
                        message=(
                            f"Produkt: {instr.product.code} — {instr.product.name}\n"
                            f"Instrukcja: v{instr.version}\n"
                            f"Zgłaszający: {report.reporter_name or 'anonimowy'} / {report.reporter_location}\n\n"
                            f"Opis:\n{report.description}\n\n"
                            f"Link: /planner/reports/{report.id}/"
                        ),
                        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@palviz.local"),
                        recipient_list=[admin_email],
                        fail_silently=True,
                    )
            except Exception as exc:
                logging.getLogger(__name__).warning("report_error notification failed: %s", exc)
            return render(request, "ui/warehouse/report_sent.html", {"instr": instr})
    else:
        form = ErrorReportForm()
    return render(request, "ui/warehouse/error_report.html", {"instr": instr, "form": form})

@_any_role
def report_sent(request):
    return render(request, "ui/warehouse/report_sent.html", {})

@_planner
def planner_reports(request):
    status_filter = request.GET.get("status", "")
    qs = ErrorReport.objects.select_related("instruction__product").order_by("-created_at")
    if status_filter:
        qs = qs.filter(status=status_filter)
    page = Paginator(qs, 20).get_page(request.GET.get("page", 1))
    return render(request, "ui/planner/reports.html", {
        "page_obj": page,
        "status_filter": status_filter,
        "status_choices": ErrorReport.STATUS_CHOICES,
        "new_count": ErrorReport.objects.filter(status="new").count(),
    })

@_planner
def planner_report_detail(request, pk: int):
    report = get_object_or_404(ErrorReport, pk=_pk4(pk))
    # Read open to any planner; status change requires master-data role.
    if request.method == "POST" and not has_role(request.user, GROUP_ADMIN, GROUP_MASTER_DATA):
        return render(request, "ui/403.html", status=403)
    if request.method == "POST":
        form = ErrorReportStatusForm(request.POST, instance=report)
        if form.is_valid():
            form.save()
            messages.success(request, "Status zaktualizowany.")
            return redirect("ui:planner_report_detail", pk=pk)
    else:
        form = ErrorReportStatusForm(instance=report)
    return render(request, "ui/planner/report_detail.html", {"report": report, "form": form})

__all__ = [
    'report_error',
    'report_sent',
    'planner_reports',
    'planner_report_detail',
]
