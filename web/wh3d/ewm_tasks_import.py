"""Import zadań magazynowych EWM do bazy: `ewm_tasks.Scan` (strumień) → bulk_create partiami.

Plik z uploadu czeka w MEDIA_ROOT/ewm_tasks/ między podglądem a importem (duże pliki
importuje worker Celery, więc plik musi leżeć na wspólnym wolumenie media, nie w /tmp).
"""
import logging
import re
import time
import uuid
from pathlib import Path

from django.conf import settings

from .ewm_tasks import Scan
from .models_tasks import WarehouseTask, WarehouseTaskBatch

log = logging.getLogger(__name__)

CHUNK = 2000
EXTENSIONS = ("csv", "txt", "xlsx", "xlsm")
TOKEN_RE = re.compile(r"^[0-9a-f]{32}\.(%s)$" % "|".join(EXTENSIONS))
STALE_HOURS = 24


def upload_dir():
    path = Path(settings.MEDIA_ROOT) / "ewm_tasks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def purge_stale():
    """Porzucone podglądy (nikt nie kliknął „Importuj”) — kasowane po dobie."""
    cutoff = time.time() - STALE_HOURS * 3600
    for p in upload_dir().iterdir():
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def save_upload(f):
    """Zapis uploadu na dysk kawałkami → token (nazwa pliku) do podglądu i importu."""
    ext = Path(f.name).suffix.lower().lstrip(".")
    if ext not in EXTENSIONS:
        raise ValueError("Obsługiwane pliki: CSV, TXT, XLSX.")
    purge_stale()
    token = f"{uuid.uuid4().hex}.{ext}"
    with open(upload_dir() / token, "wb") as out:
        for chunk in f.chunks():
            out.write(chunk)
    return token


def upload_path(token):
    """Ścieżka pliku po tokenie z formularza — tylko nasz format nazwy (bez path traversal)."""
    if not TOKEN_RE.match(token or ""):
        return None
    path = upload_dir() / token
    return path if path.exists() else None


def run_import(batch_id, token):
    """Import całego pliku do partii. Błąd w trakcie → partia „error”, zapisane wiersze usunięte."""
    batch = WarehouseTaskBatch.objects.get(pk=batch_id)
    path = upload_path(token)
    if path is None:
        batch.status, batch.message = "error", "Plik importu wygasł albo nie istnieje — wgraj go ponownie."
        batch.save(update_fields=["status", "message"])
        return batch
    batch.status = "running"
    batch.save(update_fields=["status"])
    scan = Scan(path, batch.file_name, batch.tz, batch.kind_map)
    try:
        if scan.missing:
            raise ValueError("brak kolumn: " + ", ".join(scan.missing))
        buf = []
        for row in scan:
            buf.append(WarehouseTask(batch_id=batch.pk, **row))
            if len(buf) >= CHUNK:
                WarehouseTask.objects.bulk_create(buf)
                buf.clear()
        WarehouseTask.objects.bulk_create(buf)
    except Exception as exc:                               # granica zadania: raport zamiast 500/retry
        log.exception("Import zadań EWM %s przerwany", batch.pk)
        batch.tasks.all().delete()
        batch.status, batch.message = "error", f"Import przerwany (wiersz {scan.line}): {exc}"
        batch.stats = scan.stats()
        batch.save(update_fields=["status", "message", "stats"])
        return batch
    finally:
        scan.close()
        path.unlink(missing_ok=True)
    batch.status = "done"
    batch.row_count, batch.error_count = scan.imported, scan.error_count
    batch.first_confirmed, batch.last_confirmed = scan.first, scan.last
    batch.stats = scan.stats()
    batch.message = (f"Zaimportowano {scan.imported} zadań z {scan.rows} wierszy"
                     + (f", {scan.error_count} błędnych" if scan.error_count else "") + ".")
    batch.save()
    return batch


def location_report(batch, wm, examples=30):
    """Lokalizacje z zadań partii vs regały modelu: ile trafia w gniazda, ile jest spoza modelu
    (doki, strefy buforowe, inne hale) — te zadania animacja pomija."""
    from .blender_scene import model_racks
    from .blender_stock import SlotLocator, load_master_levels

    tasks = batch.tasks.all()
    codes = (set(tasks.exclude(src_location="").values_list("src_location", flat=True).distinct())
             | set(tasks.exclude(dst_location="").values_list("dst_location", flat=True).distinct()))
    locator = SlotLocator(model_racks(wm), codes, load_master_levels())
    unmapped = sorted(c for c in codes if locator.rack_and_bay(c) is None)
    return {"model": wm, "locations": len(codes), "mapped": len(codes) - len(unmapped),
            "unmapped": len(unmapped), "examples": unmapped[:examples]}
