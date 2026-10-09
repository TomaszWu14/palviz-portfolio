"""
Celery tasks for PalViz.

In DEBUG mode (CELERY_TASK_ALWAYS_EAGER=True) these run synchronously.
In production start a worker with:  celery -A palletweb worker -l info
"""
from celery import shared_task
from django.conf import settings
from django.db import transaction


@shared_task(bind=True, name="ui.recalculate_instruction", max_retries=3, default_retry_delay=10)
def recalculate_instruction_task(self, instr_pk: int) -> dict:
    """Run full pallet optimization for one instruction and save results."""
    from .models import PalletizationInstruction
    from .views import _recalculate_instruction

    self.update_state(state="PROGRESS", meta={"step": "Pobieranie instrukcji…"})

    try:
        instr = PalletizationInstruction.objects.select_related("product", "carton").get(pk=instr_pk)
    except PalletizationInstruction.DoesNotExist:
        return {"status": "skipped", "reason": f"Instruction {instr_pk} was deleted before task ran."}

    try:
        self.update_state(state="PROGRESS", meta={"step": "Obliczanie układów palet…"})
        with transaction.atomic():
            _recalculate_instruction(instr)
    except ValueError as exc:
        # Permanent error (e.g. oversized carton fails validation) — retrying is futile.
        return {"status": "failed", "instr_pk": instr_pk, "reason": str(exc)}
    except Exception as exc:
        raise self.retry(exc=exc) from exc

    layout = instr.get_selected_layout()
    cpp = layout.get("cartons_per_pallet", 0) if layout else 0
    area = layout.get("area_used_pct", 0) if layout else 0

    return {
        "status": "done",
        "instr_pk": instr_pk,
        "layouts_count": len(instr.layouts),
        "cartons_per_pallet": cpp,
        "area_pct": area,
    }


@shared_task(bind=True, name="ui.recalculate_all_instructions")
def recalculate_all_task(self) -> dict:
    """Recalculate all active instructions — use for batch refresh after pallet rule changes."""
    from .models import PalletizationInstruction
    from .views import _recalculate_instruction

    instructions = list(PalletizationInstruction.objects.filter(is_active=True)
                        .select_related("product", "carton"))
    total = len(instructions)

    self.update_state(state="PROGRESS", meta={"step": f"Przeliczanie 0/{total}…", "done": 0, "total": total})

    done = 0
    errors = []
    for instr in instructions:
        try:
            with transaction.atomic():
                _recalculate_instruction(instr)
        except Exception as exc:
            errors.append({
                "pk": instr.pk,
                "code": getattr(getattr(instr, "product", None), "code", "?"),
                "error": str(exc),
            })
        done += 1
        if done % 5 == 0:
            self.update_state(
                state="PROGRESS",
                meta={"step": f"Przeliczono {done}/{total}", "done": done, "total": total}
            )

    status = "partial" if errors else "done"
    return {"status": status, "total": total, "done": done - len(errors), "errors": errors}


@shared_task(name="ui.send_driver_reminders")
def send_driver_reminders() -> dict:
    """Beat task: re-send the pickup-confirmation SMS to drivers who haven't confirmed.

    Each pending driver (with a phone and filled data) is reminded at most every
    DRIVER_REMINDER_INTERVAL_MIN minutes, up to DRIVER_REMINDER_MAX times. Reminders
    stop once the driver confirms/declines or the cap is reached."""
    from datetime import timedelta
    from django.conf import settings
    from django.urls import reverse
    from django.utils import timezone
    from .models import DriverAssignment
    from transport.sms import send_sms as _send_sms

    interval = timedelta(minutes=getattr(settings, "DRIVER_REMINDER_INTERVAL_MIN", 30))
    max_n = getattr(settings, "DRIVER_REMINDER_MAX", 5)
    base = (getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/")
    now = timezone.now()
    if not base:                       # bez hosta link byłby względny (nieklikalny) — nie wysyłaj
        import logging
        logging.getLogger(__name__).warning(
            "driver reminders: SITE_BASE_URL nieustawione — pomijam (link byłby względny).")
        return {"reminders_sent": 0, "skipped": "no_site_base_url"}

    sent = 0
    qs = (DriverAssignment.objects.filter(pickup_status="pending", filled_at__isnull=False)
          .exclude(driver_phone="").select_related("shipment"))
    for da in qs:
        if da.sms_count >= max_n:
            continue
        if da.sms_last_at and (now - da.sms_last_at) < interval:
            continue
        link = base + reverse("ui:driver_confirm", args=[da.confirm_token])
        body = f"Przypomnienie — potwierdź odbiór przesyłki {da.shipment.name}: {link}"
        # Warunkowy UPDATE (F) najpierw — nakładające się beaty/eager czytały ten sam
        # sms_count i gubiły inkrementy / dublowały SMS-y ponad cap.
        from django.db.models import F as _F
        claimed = (DriverAssignment.objects
                   .filter(pk=da.pk, sms_count__lt=max_n, sms_last_at=da.sms_last_at)
                   .update(sms_count=_F("sms_count") + 1, sms_last_at=now))
        if claimed and _send_sms(da.driver_phone, body):
            sent += 1
    return {"reminders_sent": sent}


def _generate_due_recurring(today=None):
    """Create a Task for every active RecurringTask due on/before `today`, advancing each
    template's next_run. Returns the number of tasks generated."""
    from datetime import timedelta
    from django.utils import timezone
    from .models import RecurringTask, Task
    today = today or timezone.localdate()
    step = {"daily": 1, "weekly": 7, "monthly": 30}
    created = 0
    from django.db import transaction
    with transaction.atomic():
        # select_for_update: nakładające się przebiegi beat (restart / eager+worker)
        # tworzyły zadanie DWA razy zanim next_run się przesunął.
        # of=("self",) — lock TYLKO wiersza RecurringTask; bez tego Postgres odrzuca
        # FOR UPDATE na nullowalnej stronie LEFT JOIN (select_related po assignee).
        due = (RecurringTask.objects.select_for_update(of=("self",))
               .filter(is_active=True, next_run__lte=today).select_related("assignee"))
        for r in due:
            Task.objects.create(title=r.title, description=r.description, priority=r.priority,
                                assignee=r.assignee, category="recurring", due_date=today)
            # Kotwica do next_run (nie today) — inaczej termin dryfuje, gdy beat opuści dzień.
            # Przeskok o pełne kroki tuż za dzisiaj: zachowuje fazę, bez spamu backfillu.
            st = step.get(r.interval, 7)
            nxt = r.next_run + timedelta(days=st)
            while nxt <= today:
                nxt += timedelta(days=st)
            r.next_run = nxt
            r.save(update_fields=["next_run"])
            created += 1
    return created


@shared_task(name="ui.generate_recurring_tasks")
def generate_recurring_tasks():
    """Beat task: generate tasks from recurring templates that are due."""
    return {"created": _generate_due_recurring()}


@shared_task(name="ui.send_hu_daily_report")
def send_hu_daily_report():
    """Beat task (06:00): raport wczorajszej kontroli HU e-mailem do liderów.

    Liczby z _kpi_stats — dokładnie te same co ekran KPI (jedno źródło prawdy).
    W poniedziałek dokłada podsumowanie ostatnich 7 dni. Adresaci: grupa
    „Lider kontroli" (z e-mailem, szanując opt-out) + WAREHOUSE_EMAIL."""
    from datetime import timedelta
    from django.contrib.auth.models import User
    from django.core.mail import send_mail
    from django.utils import timezone
    from .models import HandlingUnit, HUQualityIssue
    from .roles import GROUP_LEADER
    from huctl.kpi import kpi_stats as _kpi_stats

    if not getattr(settings, "EMAIL_HOST", ""):
        return {"ok": False, "reason": "no_smtp"}

    now = timezone.localtime()
    day_end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start = day_end - timedelta(days=1)
    rows, totals = _kpi_stats(day_start, day_end)

    recipients = sorted({
        u.email for u in User.objects.filter(groups__name=GROUP_LEADER, is_active=True)
        if u.email and getattr(getattr(u, "profile", None), "email_notifications", True)})
    wh = (getattr(settings, "WAREHOUSE_EMAIL", "") or "").strip()
    if wh and wh not in recipients:
        recipients.append(wh)
    if not recipients:
        return {"ok": False, "reason": "no_recipients"}

    backlog = HandlingUnit.objects.filter(status="to_recheck").count()
    open_issues = HUQualityIssue.objects.filter(status="open").count()

    lines = [f"Raport kontroli HU — {day_start:%d.%m.%Y}", "",
             f"Skontrolowane HU: {totals['hus']}",
             f"Pozycje: {totals['positions']}   Wykryte błędy: {totals['errors']}",
             f"Jednostki: {totals['units'] or '—'}",
             f"Kontrolerzy: {totals['controllers']}", "",
             f"Zaległość rekontroli (teraz): {backlog}",
             f"Otwarte zgłoszenia jakościowe (teraz): {open_issues}", ""]
    if rows:
        lines.append("Wyniki per kontroler:")
        for r in rows:
            pph = f"{r['pos_per_h']} poz./h" if r["pos_per_h"] is not None else "—"
            lines.append(f"  • {r['controller']}: {r['distinct_positions']} pozycji, "
                         f"{r['hus']} HU, {r['errors']} błędów, {pph}")
    else:
        lines.append("Brak aktywności kontrolnej w tym dniu.")

    if now.weekday() == 0:                      # poniedziałek → sekcja tygodniowa
        w_rows, w_totals = _kpi_stats(day_end - timedelta(days=7), day_end)
        lines += ["", f"Podsumowanie tygodnia ({day_end - timedelta(days=7):%d.%m}–{day_start:%d.%m}):",
                  f"  HU: {w_totals['hus']}   Pozycje: {w_totals['positions']}   "
                  f"Błędy: {w_totals['errors']}   Kontrolerzy: {w_totals['controllers']}"]

    link = (getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/")
    if link:
        lines += ["", f"Szczegóły i KPI: {link}/control/kpi/"]

    app = getattr(settings, "APP_NAME", "GROOVE")
    send_mail(f"[{app}] Raport kontroli HU — {day_start:%d.%m.%Y}", "\n".join(lines),
              getattr(settings, "DEFAULT_FROM_EMAIL", None), recipients, fail_silently=False)
    return {"ok": True, "recipients": len(recipients), "hus": totals["hus"]}


def _pg_dump_cmd(db):
    """(argv, env) dla pg_dump z DATABASES['default']. Hasło i sslmode idą w env procesu
    (PGPASSWORD/PGSSLMODE) — nigdy w argv, bo argv widać w `ps`."""
    import os
    env = dict(os.environ)
    if db.get("PASSWORD"):
        env["PGPASSWORD"] = str(db["PASSWORD"])
    sslmode = (db.get("OPTIONS") or {}).get("sslmode")
    if sslmode:
        env["PGSSLMODE"] = str(sslmode)
    cmd = ["pg_dump", "-h", db.get("HOST") or "localhost", "-p", str(db.get("PORT") or 5432),
           "-U", db.get("USER") or "", db.get("NAME") or ""]
    return cmd, env


# Limit czekania na zablokowaną bazę przy backupie SQLite. CPython przy SQLITE_BUSY/LOCKED
# ponawia krok .backup bez końca — bez limitu beat blokowałby worker Celery na zawsze.
_SQLITE_BACKUP_TIMEOUT_S = 300


def _sqlite_backup(src_path, dest):
    """Spójna kopia żywej bazy SQLite (.backup); zablokowana dłużej niż limit → TimeoutError,
    a niepełny plik docelowy jest usuwany."""
    import sqlite3
    import time

    deadline = time.monotonic() + _SQLITE_BACKUP_TIMEOUT_S

    def _progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise TimeoutError(f"baza SQLite zablokowana dłużej niż {_SQLITE_BACKUP_TIMEOUT_S} s "
                               "— backup przerwany")

    # Baza testowa Django to URI „file:memorydb_…” — bez uri=True powstawał plik „file” w cwd.
    src = sqlite3.connect(str(src_path), uri=str(src_path).startswith("file:"))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            with dst:
                src.backup(dst, progress=_progress)
        finally:
            dst.close()
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    finally:
        src.close()


@shared_task(name="ui.run_backup")
def run_backup():
    """Beat task (02:30): backup bazy + mediów do BACKUP_DIR z aplikacji (bez crona na
    hoście). SQLite przez natywne .backup (spójny snapshot na żywej bazie), Postgres
    przez pg_dump. Retencja BACKUP_RETAIN_DAYS. Awaria → powiadomienie adminów e-mailem.
    Kopię z BACKUP_DIR należy synchronizować off-host (rclone/rsync)."""
    import gzip
    import logging
    import subprocess
    import tarfile
    import time
    from pathlib import Path
    from django.utils import timezone as dj_tz
    log = logging.getLogger(__name__)

    backup_dir = Path(getattr(settings, "BACKUP_DIR", "/backups"))
    ts = dj_tz.now().strftime("%Y%m%dT%H%M%SZ")
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        db = settings.DATABASES["default"]
        if "sqlite" in db["ENGINE"]:
            dest = backup_dir / f"{ts}_db.sqlite3"
            _sqlite_backup(db["NAME"], dest)
        else:
            dest = backup_dir / f"{ts}_db.sql.gz"
            cmd, env = _pg_dump_cmd(db)
            with gzip.open(dest, "wb") as fh:
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, check=True, timeout=540, env=env)
                fh.write(proc.stdout)

        media_root = Path(getattr(settings, "MEDIA_ROOT", "") or "")
        if media_root.is_dir() and any(media_root.iterdir()):
            with tarfile.open(backup_dir / f"{ts}_media.tar.gz", "w:gz") as tar:
                tar.add(media_root, arcname=media_root.name)

        # Retencja: usuń lokalne kopie starsze niż BACKUP_RETAIN_DAYS.
        cutoff = time.time() - int(getattr(settings, "BACKUP_RETAIN_DAYS", 14)) * 86400
        pruned = 0
        for f in backup_dir.iterdir():
            if f.is_file() and ("_db." in f.name or f.name.endswith("_media.tar.gz")) \
                    and f.stat().st_mtime < cutoff:
                f.unlink()
                pruned += 1
        size_mb = round(dest.stat().st_size / 1e6, 1)
        log.info("Backup OK → %s (%.1f MB), usunięto %d starych kopii", dest, size_mb, pruned)
        return {"ok": True, "file": str(dest), "size_mb": size_mb, "pruned": pruned}
    except Exception as exc:
        log.exception("Backup nie powiódł się: %s", exc)
        from .notifications import notify, owner_users
        notify(owner_users(), "Backup GROOVE nie powiódł się",
               f"{type(exc).__name__}: {exc}"[:380], level="error", email=True)
        return {"ok": False, "reason": str(exc)[:200]}


@shared_task(name="ui.scheduled_powerbi_stock_pull")
def scheduled_powerbi_stock_pull():
    """Beat task: refresh warehouse stock straight from Power BI / SAP BW (no file upload)
    and re-run the stock-discrepancy engine. No-op unless Power BI is configured — enable
    the beat entry with POWERBI_AUTO_PULL=true. Mirrors `planner_stock_powerbi_import`."""
    import logging
    log = logging.getLogger(__name__)
    from . import powerbi
    if not powerbi.is_configured():
        log.warning("Power BI pull pominięty — brak konfiguracji (POWERBI_*).")
        return {"ok": False, "reason": "powerbi_not_configured"}
    try:
        header, rows = powerbi.fetch_table()
    except Exception as exc:                          # network/auth/TLS — log and bail
        # Bez tego cykliczne odświeżanie padało po cichu: beat połykał wynik zadania,
        # a stock po prostu się nie aktualizował.
        log.exception("Power BI pull nie powiódł się: %s", exc)
        powerbi.record_error(exc)
        return {"ok": False, "reason": f"fetch_error: {exc}"}
    if not rows:
        log.warning("Power BI zwrócił 0 wierszy (tabela %s).",
                    getattr(settings, "POWERBI_STOCK_TABLE", "?"))
        return {"ok": False, "reason": "no_rows"}
    powerbi.clear_error()
    from huctl.hu_import import import_hu_rows as _import_hu_rows
    from .notifications import run_stock_discrepancy_checks
    ok, info = _import_hu_rows(header, rows)
    if not ok:
        return {"ok": False, "reason": info}
    discrepancies = run_stock_discrepancy_checks()
    return {"ok": True, "hu": info.get("hu"), "items": info.get("items"),
            "skipped_locked": info.get("skipped_locked"),
            "discrepancies": discrepancies}


@shared_task(name="ui.zaria_purge_old_conversations")
def zaria_purge_old_conversations():
    """Retencja ZARIA (F3): usuwa NIEzapisane rozmowy starsze niż ZariaConfig.retention_days.
    Zapisane (is_saved) są wyłączone. 0 = retencja wyłączona. Ostrzeżenie w UI pojawia się
    7 dni wcześniej (patrz _retention_warning_days) — tu tylko właściwe usunięcie."""
    from datetime import timedelta
    from django.utils import timezone
    from .models import ZariaConfig, ZariaConversation
    days = ZariaConfig.load().retention_days or 0
    if not days:
        return {"ok": True, "deleted": 0, "reason": "retention_off"}
    cutoff = timezone.now() - timedelta(days=days)
    qs = ZariaConversation.objects.filter(is_saved=False, updated_at__lt=cutoff)
    n = qs.count()
    qs.delete()
    return {"ok": True, "deleted": n}


@shared_task(name="ui.purge_old_hu_control_photos")
def purge_old_hu_control_photos():
    """Retencja zdjęć kontroli HU: kasuje HUControlPhoto WRAZ Z PLIKIEM starsze niż
    HU_PHOTO_RETAIN_DAYS (domyślnie 30). Zdjęcie to dowód do spot-audytu lidera, nie
    wieczyste archiwum — bez tego dysk rósłby o GB/dobę. 0 = retencja wyłączona."""
    from datetime import timedelta
    from django.conf import settings
    from django.utils import timezone
    from .models import HUControlPhoto
    days = int(getattr(settings, "HU_PHOTO_RETAIN_DAYS", 30) or 0)
    if not days:
        return {"ok": True, "deleted": 0, "reason": "retention_off"}
    cutoff = timezone.now() - timedelta(days=days)
    n = 0
    for p in list(HUControlPhoto.objects.filter(created_at__lt=cutoff)):
        p.photo.delete(save=False)   # usuń plik z dysku (Django nie kasuje go automatycznie)
        p.delete()
        n += 1
    return {"ok": True, "deleted": n}


@shared_task(name="ui.gdpr_retention")
def gdpr_retention():
    """RODO: anonimizacja danych kierowców i czyszczenie logów logowania po okresie retencji
    (GDPR_DRIVER_RETAIN_DAYS / GDPR_ACCESSLOG_RETAIN_DAYS; 0 = wyłączone, domyślnie)."""
    from .gdpr import anonymize_drivers, purge_access_logs
    return {"drivers": anonymize_drivers(int(getattr(settings, "GDPR_DRIVER_RETAIN_DAYS", 0) or 0)),
            "access_logs": purge_access_logs(int(getattr(settings, "GDPR_ACCESSLOG_RETAIN_DAYS", 0) or 0))}


@shared_task(name="ui.refresh_transport_kpi")
def refresh_transport_kpi():
    """Odśwież snapshot KPI transportu (beat co godzinę) — dashboard renderuje z cache."""
    from transport.kpi import refresh_transport_kpi_snapshot
    snap = refresh_transport_kpi_snapshot()
    return {"updated_at": snap.updated_at.isoformat()}


@shared_task(bind=True, name="ui.complete_powerbi_device_flow", max_retries=0,
             time_limit=1200, soft_time_limit=1100)
def complete_powerbi_device_flow(self, flow: dict) -> dict:
    """Dokończ logowanie device-code do Power BI (blokuje do wpisania kodu / wygaśnięcia).
    Jako zadanie Celery zamiast wątku-demona w widoku: przeżywa restart workera z retry
    po stronie operatora (ponowny connect), błąd ląduje w powerbi.record_error zamiast
    ginąć z procesem, a stan widać w wynikach zadań (django-celery-results)."""
    from . import powerbi
    try:
        powerbi.complete_device_flow(flow)
        return {"ok": True}
    except Exception as exc:                       # noqa: BLE001 — powód idzie do panelu
        powerbi.record_error(exc)
        return {"ok": False, "error": str(exc)}
