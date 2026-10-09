# Import zadań magazynowych EWM (WT) z /SCWM/MON: lista importów → podgląd pliku (mapowanie
# kolumn, próbka, błędy) → import (duże pliki przez Celery) → raport (statystyki, lokalizacje
# spoza modelu). Zadania zasilają wózki w animacji przepływów widoku 3D modelu.
import logging
import threading
from datetime import timedelta
from pathlib import Path

from django.db import connection
from django.utils import timezone
from django.views.decorators.cache import never_cache

from ui.views.core import (
    _md_role, get_object_or_404, JsonResponse, messages, redirect, render, require_POST, WarehouseModel,
)
from wh3d.ewm_tasks import KIND_LABELS, Scan, parse_overrides
from wh3d.ewm_tasks_import import EXTENSIONS, location_report, run_import, save_upload, upload_path
from wh3d.models_tasks import WarehouseTaskBatch
from wh3d.tasks import import_warehouse_tasks

__all__ = ["ewm_tasks_list", "ewm_tasks_preview", "ewm_tasks_import", "ewm_tasks_detail",
           "ewm_tasks_status", "ewm_tasks_delete"]

MAX_UPLOAD_MB = 300
SYNC_MAX_BYTES = 5 * 1024 * 1024       # mniejsze pliki import od razu, większe przez Celery
PREVIEW_ROWS = 2000
SAMPLE_ROWS = 15
TZ_VALUES = [tz for tz, _ in WarehouseTaskBatch.TZ_CHOICES]
STALE_AFTER = timedelta(hours=6)       # „w tle” dłużej = proces padł (deploy/restart) — do ponowienia

log = logging.getLogger(__name__)


def _run_in_thread(batch_id, token):
    try:
        run_import(batch_id, token)
    finally:
        connection.close()                 # wątek ma własne połączenie z bazą


def _dispatch(batch_id, token):
    """Duży plik → Celery; gdy brokera nie ma (produkcja bez Redisa/workera) → wątek w tle
    procesu web. ponytail: wątek ginie przy restarcie kontenera (partia zostaje „w tle” →
    STALE_AFTER); docelowo Redis + worker Celery w Coolify."""
    try:
        import_warehouse_tasks.apply_async((batch_id, token), retry=False)
    except Exception as exc:               # kombu OperationalError itp. — brak brokera
        log.warning("Celery niedostępne (%s) — import zadań EWM %s w wątku", exc, batch_id)
        threading.Thread(target=_run_in_thread, args=(batch_id, token), daemon=True).start()


def _form(request):
    """Wspólne pola formularza (upload i potwierdzenie): nazwa, strefa czasowa, nadpisania."""
    tz = request.POST.get("tz")
    kind_map_text = request.POST.get("kind_map", "")
    overrides, bad = parse_overrides(kind_map_text)
    return {"name": request.POST.get("name", "").strip()[:200], "tz": tz if tz in TZ_VALUES else TZ_VALUES[0],
            "kind_map_text": kind_map_text, "overrides": overrides, "override_errors": bad}


@_md_role
def ewm_tasks_list(request):
    return render(request, "ui/ewm_tasks/list.html", {
        "batches": WarehouseTaskBatch.objects.select_related("uploaded_by")[:50],
        "tz_choices": WarehouseTaskBatch.TZ_CHOICES, "kind_labels": KIND_LABELS,
        "max_mb": MAX_UPLOAD_MB, "accept": ",".join(f".{e}" for e in EXTENSIONS),
    })


@_md_role
@require_POST
def ewm_tasks_preview(request):
    """Plik → dysk (token) → podgląd pierwszych PREVIEW_ROWS wierszy; nic nie trafia do bazy."""
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Nie wybrano pliku.")
        return redirect("ui:ewm_tasks_list")
    if f.size > MAX_UPLOAD_MB * 1024 * 1024:
        messages.error(request, f"Plik jest zbyt duży (max {MAX_UPLOAD_MB} MB) — podziel eksport na okresy.")
        return redirect("ui:ewm_tasks_list")
    form = _form(request)
    try:
        token = save_upload(f)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui:ewm_tasks_list")
    scan = Scan(upload_path(token), f.name, form["tz"], form["overrides"])
    sample = []
    try:
        for row in scan:
            if len(sample) < SAMPLE_ROWS:
                sample.append({**row, "kind_label": KIND_LABELS[row["kind"]]})
            if scan.rows >= PREVIEW_ROWS:
                break
    except Exception as exc:                               # uszkodzony XLSX, zły format
        scan.close()
        upload_path(token).unlink(missing_ok=True)
        messages.error(request, f"Nie udało się odczytać pliku: {exc}")
        return redirect("ui:ewm_tasks_list")
    scan.close()
    return render(request, "ui/ewm_tasks/preview.html", {
        **form, "name": form["name"] or Path(f.name).stem[:200], "file_name": f.name[:255],
        "token": token, "sample": sample, "stats": scan.stats(), "missing": scan.missing,
        "first": scan.first, "last": scan.last, "size": f.size,
        "partial": scan.rows >= PREVIEW_ROWS, "preview_rows": PREVIEW_ROWS,
        "background": f.size > SYNC_MAX_BYTES,
    })


@_md_role
@require_POST
def ewm_tasks_import(request):
    """Potwierdzenie podglądu: partia + import (mały plik od razu, duży w Celery)."""
    token = request.POST.get("token", "")
    path = upload_path(token)
    if path is None:
        messages.error(request, "Plik podglądu wygasł albo nie istnieje — wgraj go ponownie.")
        return redirect("ui:ewm_tasks_list")
    form = _form(request)
    batch = WarehouseTaskBatch.objects.create(
        name=form["name"] or "Import zadań EWM", file_name=request.POST.get("file_name", "")[:255],
        uploaded_by=request.user, tz=form["tz"], kind_map=form["overrides"])
    if path.stat().st_size <= SYNC_MAX_BYTES:
        run_import(batch.pk, token)
    else:
        _dispatch(batch.pk, token)
    return redirect("ui:ewm_tasks_detail", pk=batch.pk)


def _progress(batch):
    """(pending, stale): import „w tle” trwa — albo wisi dłużej niż STALE_AFTER (proces padł)."""
    in_progress = batch.status in ("queued", "running")
    stale = in_progress and batch.uploaded_at < timezone.now() - STALE_AFTER
    return in_progress and not stale, stale


@_md_role
def ewm_tasks_detail(request, pk):
    batch = get_object_or_404(WarehouseTaskBatch, pk=pk)
    pending, stale = _progress(batch)
    models_ = list(WarehouseModel.objects.order_by("name").only("pk", "name"))
    wm = next((m for m in models_ if str(m.pk) == request.GET.get("model")), models_[0] if models_ else None)
    report = location_report(batch, wm) if wm is not None and batch.status == "done" else None
    return render(request, "ui/ewm_tasks/detail.html", {
        "batch": batch, "stats": batch.stats or {}, "models": models_, "report": report,
        "pending": pending, "stale": stale,
        "overrides": [(code, KIND_LABELS.get(kind, kind)) for code, kind in (batch.kind_map or {}).items()],
    })


@_md_role
@never_cache
def ewm_tasks_status(request, pk):
    """Lekki status partii dla pollingu szczegółów (fetch co 5 s zamiast meta-refresh, UX-004)."""
    batch = get_object_or_404(WarehouseTaskBatch.objects.only(
        "status", "uploaded_at", "row_count", "message"), pk=pk)
    pending, stale = _progress(batch)
    return JsonResponse({"status": batch.status, "status_display": batch.get_status_display(),
                         "pending": pending, "stale": stale, "row_count": batch.row_count,
                         "message": batch.message})


@_md_role
@require_POST
def ewm_tasks_delete(request, pk):
    batch = get_object_or_404(WarehouseTaskBatch, pk=pk)
    name = batch.name
    batch.delete()
    messages.success(request, f"Usunięto import „{name}”.")
    return redirect("ui:ewm_tasks_list")
