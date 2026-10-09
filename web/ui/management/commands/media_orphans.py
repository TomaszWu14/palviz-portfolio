"""Osierocone pliki mediów — pliki w MEDIA_ROOT, na które nie wskazuje żaden rekord.

Django nie kasuje pliku przy delete() rekordu (także kaskadowym), więc zdjęcia,
artworki i GLB zostają na dysku bez właściciela. Komenda porównuje MEDIA_ROOT
z wartościami wszystkich FileField/ImageField w bazie.

    python manage.py media_orphans            # tylko raport (dry-run)
    python manage.py media_orphans --delete   # usuń osierocone pliki

Pomija pliki młodsze niż 24 h (upload w toku / rekord jeszcze niezapisany)
oraz katalogi z własną retencją (EXCLUDE_DIRS).
"""
import time
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import models

MIN_AGE_HOURS = 24
# Pliki tymczasowe z własnym sprzątaniem (wh3d.ewm_tasks_import.purge_stale).
EXCLUDE_DIRS = ("ewm_tasks",)


def referenced_paths():
    """Zbiór ścieżek (względnych, z '/') ze wszystkich pól plikowych wszystkich modeli."""
    refs = set()
    for model in apps.get_models():
        names = [f.name for f in model._meta.concrete_fields if isinstance(f, models.FileField)]
        for name in names:
            refs.update(model._default_manager.exclude(**{name: ""})
                        .exclude(**{f"{name}__isnull": True})
                        .values_list(name, flat=True))
    return {r.replace("\\", "/") for r in refs}


def find_orphans(media_root, now=None):
    """Lista (Path, rozmiar) osieroconych plików starszych niż MIN_AGE_HOURS."""
    root = Path(media_root)
    if not root.is_dir():
        return []
    refs = referenced_paths()
    cutoff = (now or time.time()) - MIN_AGE_HOURS * 3600
    out = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.split("/", 1)[0] in EXCLUDE_DIRS or rel in refs:
            continue
        st = p.stat()
        if st.st_mtime < cutoff:
            out.append((p, st.st_size))
    return out


class Command(BaseCommand):
    help = "Raport (i opcjonalne usunięcie) plików w MEDIA_ROOT bez rekordu w bazie."

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="store_true",
                            help="Usuń osierocone pliki (domyślnie tylko raport).")

    def handle(self, *args, **options):
        root = Path(settings.MEDIA_ROOT)
        orphans = find_orphans(root)
        total = sum(size for _, size in orphans)
        for p, size in orphans:
            self.stdout.write(f"  {p.relative_to(root).as_posix()}  ({size} B)")
        self.stdout.write(f"Osierocone pliki: {len(orphans)}, łącznie {total / 1e6:.1f} MB")
        if not options["delete"]:
            if orphans:
                self.stdout.write("Tryb raportu — uruchom z --delete, aby usunąć.")
            return
        removed = 0
        for p, _ in orphans:
            try:
                p.unlink()
                removed += 1
            except OSError as exc:
                self.stderr.write(f"Nie usunięto {p}: {exc}")
        self.stdout.write(self.style.SUCCESS(f"Usunięto {removed} plików."))
