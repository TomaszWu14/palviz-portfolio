"""Dogeneruj derywaty (display + miniatura) dla grafik opakowań wgranych PRZED tą zmianą.

Bez tego stare rekordy działają — `as_dict()` spada wtedy na oryginał — ale front pobiera
pełny plik (do 8 MB) pod kafelek 120 px. Komenda jest idempotentna: pomija rekordy, które
mają już oba warianty, więc można ją puścić ponownie po nieudanym przebiegu.

    python manage.py backfill_artwork_derivatives [--dry-run] [--limit N]
"""
from django.core.management.base import BaseCommand

from ui.models import CartonArtwork, ProductArtwork, InnerPackArtwork
from ui.views.core.images import make_artwork_derivatives

MODELS = (CartonArtwork, ProductArtwork, InnerPackArtwork)


class Command(BaseCommand):
    help = "Generuje brakujące derywaty grafik opakowań (karton / sztuka / OPZ)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Tylko policz, nic nie zapisuj.")
        parser.add_argument("--limit", type=int, default=0,
                            help="Przetwórz najwyżej N rekordów na model (0 = bez limitu).")

    def handle(self, *args, **opts):
        dry, limit = opts["dry_run"], opts["limit"]
        total_done = total_skipped = total_failed = 0

        for model in MODELS:
            # `width_px=0` odsiewa rekordy już raz przetworzone, którym derywat po prostu
            # nie był potrzebny (mały, lekki plik) — bez tego każde uruchomienie dekodowało
            # je od nowa, mimo że komenda ma być idempotentna.
            qs = model.objects.exclude(image="").filter(
                image_display="", image_thumb="", width_px=0)
            if limit:
                qs = qs[:limit]
            done = skipped = failed = 0
            for art in qs:
                try:
                    with art.image.open("rb") as fh:
                        display, thumb, width, height = make_artwork_derivatives(fh)
                except Exception as exc:                     # noqa: BLE001 — plik zniknął?
                    self.stderr.write(f"  ✗ {model.__name__} #{art.pk}: {exc}")
                    failed += 1
                    continue
                if dry:
                    done += 1 if (display or thumb) else 0
                    skipped += 0 if (display or thumb) else 1
                    continue
                if not (display or thumb):
                    # Mały, lekki plik: derywat nic by nie dał, ale wymiary zapisujemy —
                    # inaczej rekord wraca w każdym kolejnym przebiegu.
                    if width:
                        art.width_px, art.height_px = width, height
                        art.save(update_fields=["width_px", "height_px"])
                    skipped += 1
                    continue
                stem = (art.image.name.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "art")[:80]
                if display:
                    art.image_display.save(f"{stem}_d.webp", display, save=False)
                if thumb:
                    art.image_thumb.save(f"{stem}_t.webp", thumb, save=False)
                art.width_px, art.height_px = width, height
                art.save(update_fields=["image_display", "image_thumb",
                                        "width_px", "height_px"])
                done += 1
            self.stdout.write(f"{model.__name__}: {done} przetworzonych, "
                              f"{skipped} pominiętych, {failed} błędów")
            total_done += done
            total_skipped += skipped
            total_failed += failed

        prefix = "[dry-run] " if dry else ""
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}Razem: {total_done} derywatów, {total_skipped} pominiętych, "
            f"{total_failed} błędów."))
