"""Derywaty grafik opakowań (artwork): mniejszy wariant „display" + miniatura.

Po co: oryginał wgrywany przez Master Data ma do 8 MB (`_ART_MAX_BYTES`), a konsumenci
renderują go dużo mniejszy — edytor 2D max 400 px, tekstura ścianki 3D max 512 px, karta
poziomu na skanerze PHV 120 px. Serwowanie oryginału pod kafelek 120 px to kilkadziesiąt
MB na kartę hierarchii; derywaty ścinają to o rząd(y) wielkości. Oryginał ZOSTAJE w
`image` (źródło do druku / pobrania), derywaty to dodatkowe pliki.

Zasady:
  • zapis do WEBP (mały, z kanałem alpha — etykiety bywają przezroczyste; `image/webp`
    jest już na liście typów serwowanych inline w `views/misc.py`),
  • wariant powstaje TYLKO gdy realnie zmniejsza obraz — mały plik nie dostaje derywatu
    i front spada na oryginał (`as_dict()` gwarantuje niepusty `url`),
  • plik niedekodowalny/uszkodzony NIE wywraca uploadu: zwracamy same None-y i grafika
    działa jak dotąd (na oryginale).

Pillow importowany leniwie (wewnątrz funkcji) — zgodnie z konwencją repo dla zależności
opcjonalnych w ścieżce importu widoków.
"""
import io
import logging

from django.core.files.base import ContentFile

log = logging.getLogger(__name__)

# Dłuższy bok wariantów. DISPLAY obsługuje edytor 2D (≤400 px) i teksturę 3D w pełnej
# jakości (≤512 px) z zapasem na ekrany HiDPI; THUMB to kafle PHV / małe canvasy.
ART_DISPLAY_MAX_PX = 1024
ART_THUMB_MAX_PX = 256
_WEBP_QUALITY = 82
# Powyżej tej wagi wariant display powstaje NAWET przy małych wymiarach: PNG 900×900
# potrafi ważyć 5 MB, a samo przekodowanie do WEBP ścina to o rząd wielkości. Bez tego
# progu „mały obrazek" wg pikseli nadal jedzie do przeglądarki jako kilka megabajtów.
ART_RECODE_MIN_BYTES = 300_000
# Zapora na „decompression bomb" — wymiary sprawdzamy przed jakimkolwiek resize.
_MAX_DECODED_PIXELS = 60_000_000


def _open_normalized(django_file):
    """→ (PIL.Image gotowy do skalowania: obrócony wg EXIF, w trybie RGB/RGBA,
    czy EXIF faktycznie coś obrócił)."""
    from PIL import Image, ImageOps

    django_file.seek(0)
    img = Image.open(io.BytesIO(django_file.read()))
    if img.width * img.height > _MAX_DECODED_PIXELS:
        raise ValueError(f"obraz za duży: {img.width}×{img.height} px")
    # Orientację czytamy PRZED transpozycją — potem PIL usuwa ten tag. Ma znaczenie, bo
    # EXIF stosujemy tylko do derywatów: gdy zdjęcie było obrócone, a derywat by nie
    # powstał, front dostałby oryginał leżący na boku (fallback `as_dict()` → `image`).
    rotated = (img.getexif().get(0x0112) or 1) != 1
    img = ImageOps.exif_transpose(img) or img
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if img.mode in ("P", "LA") else "RGB")
    return img, rotated


def _encode_webp(img, max_px, force=False):
    """Kopia `img` zmniejszona do `max_px` (dłuższy bok) jako ContentFile(WEBP).
    None, gdy obraz mieści się w limicie i nic go nie wymusza — wtedy derywat nic
    by nie dał. `force` przekodowuje bez zmiany wymiarów (nigdy nie powiększamy)."""
    from PIL import Image

    w, h = img.size
    if max(w, h) <= max_px and not force:
        return None
    scale = min(1.0, max_px / max(w, h))
    if scale < 1.0:
        resample = getattr(Image, "Resampling", Image).LANCZOS
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), resample)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=_WEBP_QUALITY, method=4)
    return ContentFile(buf.getvalue())


def make_artwork_derivatives(django_file):
    """→ (display|None, thumb|None, width_px, height_px) dla wgranego pliku grafiki.

    `display`/`thumb` to ContentFile z WEBP (albo None, gdy oryginał jest już mały).
    Wymiary to rozmiar ORYGINAŁU (0/0, gdy nie dało się go zdekodować)."""
    try:
        heavy = (getattr(django_file, "size", 0) or 0) > ART_RECODE_MIN_BYTES
        img, rotated = _open_normalized(django_file)
        with img:
            width, height = img.size
            display = _encode_webp(img, ART_DISPLAY_MAX_PX, force=heavy or rotated)
            thumb = _encode_webp(img, ART_THUMB_MAX_PX)
        return display, thumb, width, height
    except Exception as exc:                  # noqa: BLE001 — upload ma przejść mimo to
        log.warning("artwork: brak derywatów (%s) — front użyje oryginału", exc)
        return None, None, 0, 0
    finally:
        try:
            django_file.seek(0)               # storage zapisuje oryginał po nas
        except Exception:                     # noqa: BLE001
            pass


__all__ = ["make_artwork_derivatives", "ART_DISPLAY_MAX_PX", "ART_THUMB_MAX_PX",
           "ART_RECODE_MIN_BYTES"]
