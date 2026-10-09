"""Kiedy powstaje wariant „display" — bo sam limit pikseli to za mało.

Dwa przypadki, które wcześniej przechodziły bokiem i front dostawał oryginał:
  • plik o małych wymiarach, ale ciężki (PNG 900×900 potrafi mieć 5 MB),
  • zdjęcie z orientacją EXIF — transpozycję robimy tylko na derywacie, więc bez niego
    ta sama etykieta bywała pokazana na boku.
Plus idempotencja backfillu dla rekordów, którym derywat nie jest potrzebny.
"""
import io
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ui.models import Carton, CartonArtwork
from ui.roles import GROUP_MASTER_DATA
from ui.views.core.images import ART_RECODE_MIN_BYTES, make_artwork_derivatives


def _noisy_png(w=900, h=900):
    """PNG o małych wymiarach, ale ciężki — szum nie daje się skompresować, więc plik
    przekracza próg przekodowania mimo że mieści się w limicie pikseli."""
    import random
    from PIL import Image
    rnd = random.Random(7)
    img = Image.new("RGB", (w, h))
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                 for _ in range(w * h)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _rotated_jpeg(w=120, h=80, orientation=6):
    """Mały JPEG z EXIF Orientation=6 (obrót o 90°) — wymiarami nie kwalifikuje się
    do zmniejszenia, więc bez wymuszenia nie powstałby żaden derywat."""
    from PIL import Image
    img = Image.new("RGB", (w, h), (10, 120, 200))
    exif = img.getexif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


class MediaTempMixin:
    def setUp(self):
        super().setUp()
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)


class DerivativeRulesTests(MediaTempMixin, TestCase):
    def test_heavy_but_small_image_still_gets_display(self):
        raw = _noisy_png()
        self.assertGreater(len(raw), ART_RECODE_MIN_BYTES)     # założenie fixture'a
        f = SimpleUploadedFile("noisy.png", raw, content_type="image/png")
        display, thumb, w, h = make_artwork_derivatives(f)
        self.assertIsNotNone(display, "ciężki plik musi dostać wariant display")
        self.assertEqual((w, h), (900, 900))
        self.assertLess(display.size, len(raw))                # po to jest przekodowanie
        from PIL import Image
        with Image.open(display) as im:
            self.assertEqual(im.size, (900, 900))              # nigdy nie skalujemy w górę

    def test_light_and_small_image_gets_no_display(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (200, 120, 60)).save(buf, format="PNG")
        f = SimpleUploadedFile("small.png", buf.getvalue(), content_type="image/png")
        display, thumb, w, h = make_artwork_derivatives(f)
        self.assertIsNone(display)                             # nie ma czego poprawiać
        self.assertIsNone(thumb)
        self.assertEqual((w, h), (64, 48))

    def test_exif_rotated_image_gets_normalized_derivative(self):
        f = SimpleUploadedFile("rot.jpg", _rotated_jpeg(), content_type="image/jpeg")
        display, thumb, w, h = make_artwork_derivatives(f)
        self.assertIsNotNone(display, "obrócone EXIF-em musi dostać wyprostowany derywat")
        # 120×80 po obrocie o 90° → 80×120; wymiary raportujemy jak WIDAĆ obraz
        self.assertEqual((w, h), (80, 120))
        from PIL import Image
        with Image.open(display) as im:
            self.assertEqual(im.size, (80, 120))


class BackfillIdempotenceTests(MediaTempMixin, TestCase):
    def test_small_records_are_not_reprocessed(self):
        from django.core.management import call_command
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (10, 10, 10)).save(buf, format="PNG")
        c = Carton.objects.create(name="K", length_cm=40, width_cm=30, height_cm=25,
                                  unit_weight_kg=0.5, pieces_per_carton=10)
        art = CartonArtwork.objects.create(
            carton=c, face="front", kind="print",
            image=SimpleUploadedFile("s.png", buf.getvalue(), content_type="image/png"))

        call_command("backfill_artwork_derivatives", verbosity=0)
        art.refresh_from_db()
        self.assertFalse(art.image_display)                    # derywat niepotrzebny…
        self.assertEqual((art.width_px, art.height_px), (64, 48))   # …ale wymiary znamy

        out = io.StringIO()
        call_command("backfill_artwork_derivatives", stdout=out)
        # Zero POMINIĘTYCH też ma znaczenie: „pominięty" znaczyłoby, że plik znów został
        # otwarty i zdekodowany, tylko bez efektu — a komenda ma go już nie dotykać.
        self.assertIn("Razem: 0 derywatów, 0 pominiętych", out.getvalue())


class MdUserMixin:
    def _md_login(self):
        u = get_user_model().objects.create_user("md-var", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)


class UploadUsesRulesTests(MediaTempMixin, MdUserMixin, TestCase):
    def test_heavy_upload_through_the_view_gets_display(self):
        self._md_login()
        c = Carton.objects.create(name="K2", length_cm=40, width_cm=30, height_cm=25,
                                  unit_weight_kg=0.5, pieces_per_carton=10)
        resp = self.client.post(
            reverse("ui:carton_artwork_upload", args=[c.pk]),
            {"image": SimpleUploadedFile("noisy.png", _noisy_png(), content_type="image/png"),
             "face": "front", "kind": "print"})
        self.assertEqual(resp.status_code, 200)
        art = CartonArtwork.objects.get()
        self.assertTrue(art.image_display)
        self.assertLess(art.image_display.size, art.image.size)
