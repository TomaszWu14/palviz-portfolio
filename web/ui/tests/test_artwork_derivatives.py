"""Derywaty grafik opakowań: mniejszy wariant „display" + miniatura zamiast serwowania
oryginału (do 8 MB) pod kafelek 120 px, plus nagłówki cache na /media/.

Strażnik regresji dla trzech rzeczy, które łatwo zepsuć przy kolejnej zmianie:
  • kontrakt `as_dict()` — `url` NIGDY nie może być puste (renderer 3D i edytor na nim stoją),
  • odporność na plik nie do zdekodowania — upload ma przejść, tylko bez derywatów,
  • wspólna ścieżka uploadu kartonu i sztuki/OPZ (cartons.py deleguje do _art_upload).
"""
import io
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ui.models import Carton, CartonArtwork, Product, ProductArtwork
from ui.roles import GROUP_MASTER_DATA


class MediaTempMixin:
    """Świeży MEDIA_ROOT na KAŻDY test. Wspólny katalog na moduł sprawiał, że testy
    zależały od siebie: „podmiana usuwa stare pliki" przechodził tylko dlatego, że
    sąsiedni test zajął już `art.png` i storage dokleił sufiks — uruchomiony sam padał,
    maskując realny błąd (ten sam URL po podmianie + `Cache-Control: immutable`)."""

    def setUp(self):
        super().setUp()
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        self.media = media


def _png(w=2000, h=1500):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 120, 60)).save(buf, format="PNG")
    return buf.getvalue()


def _upload(w=2000, h=1500, name="art.png"):
    return SimpleUploadedFile(name, _png(w, h), content_type="image/png")


def _md_user():
    u = get_user_model().objects.create_user("md-art", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
    return u


def _carton():
    return Carton.objects.create(name="K", length_cm=40, width_cm=30, height_cm=25,
                                 unit_weight_kg=0.5, pieces_per_carton=10)


class ArtworkDerivativesTests(MediaTempMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(_md_user())

    def _post(self, carton, **extra):
        return self.client.post(
            reverse("ui:carton_artwork_upload", args=[carton.pk]),
            {"image": _upload(**extra), "face": "front", "kind": "print"})

    def test_big_upload_gets_display_and_thumb(self):
        c = _carton()
        resp = self._post(c)
        self.assertEqual(resp.status_code, 200)
        art = CartonArtwork.objects.get()
        self.assertTrue(art.image_display and art.image_thumb)
        from PIL import Image
        with Image.open(art.image_display) as im:
            self.assertLessEqual(max(im.size), 1024)
        with Image.open(art.image_thumb) as im:
            self.assertLessEqual(max(im.size), 256)
        self.assertEqual((art.width_px, art.height_px), (2000, 1500))
        # derywat musi być realnie lżejszy od oryginału — po to cała zmiana
        self.assertLess(art.image_thumb.size, art.image.size)

    def test_as_dict_contract(self):
        c = _carton()
        d = self._post(c).json()["artwork"]
        self.assertEqual(set(d) >= {"url", "thumb", "full", "w_px", "h_px"}, True)
        art = CartonArtwork.objects.get()
        self.assertEqual(d["url"], art.image_display.url)     # ekran = wariant display
        self.assertEqual(d["thumb"], art.image_thumb.url)     # kafelek = miniatura
        self.assertEqual(d["full"], art.image.url)            # oryginał wciąż dostępny

    def test_small_image_falls_back_to_original(self):
        c = _carton()
        d = self._post(c, w=64, h=48).json()["artwork"]
        art = CartonArtwork.objects.get()
        self.assertFalse(art.image_display)                   # nie ma czego zmniejszać
        self.assertFalse(art.image_thumb)
        self.assertEqual(d["url"], art.image.url)             # …a front i tak ma URL
        self.assertEqual(d["thumb"], art.image.url)
        self.assertEqual((d["w_px"], d["h_px"]), (64, 48))

    def test_undecodable_file_still_uploads(self):
        """Atrapa „PNG" (jak w test_carton_artwork_3d) nie może wywrócić uploadu."""
        c = _carton()
        resp = self.client.post(
            reverse("ui:carton_artwork_upload", args=[c.pk]),
            {"image": SimpleUploadedFile("f.png", b"\x89PNG\r\n", content_type="image/png"),
             "face": "front", "kind": "print"})
        self.assertEqual(resp.status_code, 200)
        d = resp.json()["artwork"]
        art = CartonArtwork.objects.get()
        self.assertTrue(d["url"])
        self.assertEqual(d["url"], art.image.url)
        self.assertEqual((art.width_px, art.height_px), (0, 0))

    def test_reupload_gives_a_different_url(self):
        """Warunek poprawności `Cache-Control: immutable` na /media/*_artwork/.
        Gdyby podmiana odzyskała tę samą ścieżkę, przeglądarka trzymałaby starą grafikę
        nawet rok — a użytkownik nie ma jak tego wymusić poza czyszczeniem cache."""
        c = _carton()
        first = self._post(c).json()["artwork"]
        second = self._post(c).json()["artwork"]               # ten sam plik, ta sama nazwa
        for key in ("url", "thumb", "full"):
            self.assertNotEqual(first[key], second[key], f"URL nie zmienił się dla `{key}`")

    def test_reupload_of_print_removes_old_files(self):
        c = _carton()
        self._post(c)
        old = CartonArtwork.objects.get()
        old_paths = [old.image.path, old.image_display.path, old.image_thumb.path]
        self._post(c)                                          # jedna ściana = jeden nadruk
        self.assertEqual(CartonArtwork.objects.count(), 1)
        import os
        for p in old_paths:
            self.assertFalse(os.path.exists(p), f"osierocony plik: {p}")
        new = CartonArtwork.objects.get()                      # …i nowy plik NIE zajął ich miejsca
        self.assertNotIn(new.image.path, old_paths)

    def test_delete_removes_all_three_files(self):
        import os
        c = _carton()
        self._post(c)
        art = CartonArtwork.objects.get()
        paths = [art.image.path, art.image_display.path, art.image_thumb.path]
        resp = self.client.post(reverse("ui:carton_artwork_delete", args=[art.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(CartonArtwork.objects.count(), 0)
        for p in paths:
            self.assertFalse(os.path.exists(p), f"osierocony plik: {p}")

    def test_product_upload_shares_the_same_path(self):
        p = Product.objects.create(code="ART-D", name="X",
                                   unit_length_cm=10, unit_width_cm=8, unit_height_cm=5)
        resp = self.client.post(reverse("ui:product_artwork_upload", args=[p.pk]),
                                {"image": _upload(), "face": "front", "kind": "label"})
        self.assertEqual(resp.status_code, 200)
        art = ProductArtwork.objects.get()
        self.assertTrue(art.image_display and art.image_thumb)
        self.assertEqual((art.width_px, art.height_px), (2000, 1500))

    def test_oversize_and_wrong_type_still_rejected(self):
        c = _carton()
        resp = self.client.post(
            reverse("ui:carton_artwork_upload", args=[c.pk]),
            {"image": SimpleUploadedFile("a.gif", b"GIF89a", content_type="image/gif"),
             "face": "front", "kind": "print"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(CartonArtwork.objects.count(), 0)


class BackfillCommandTests(MediaTempMixin, TestCase):
    """Rekordy sprzed tej zmiany mają sam oryginał — komenda dogenerowuje derywaty."""

    def test_backfill_fills_missing_derivatives(self):
        from django.core.management import call_command
        c = _carton()
        art = CartonArtwork.objects.create(carton=c, face="front", kind="print",
                                           image=_upload())     # zapis z pominięciem widoku
        self.assertFalse(art.image_display)

        call_command("backfill_artwork_derivatives", "--dry-run", verbosity=0)
        art.refresh_from_db()
        self.assertFalse(art.image_display)                     # dry-run niczego nie zapisuje

        call_command("backfill_artwork_derivatives", verbosity=0)
        art.refresh_from_db()
        self.assertTrue(art.image_display and art.image_thumb)
        self.assertEqual((art.width_px, art.height_px), (2000, 1500))

        call_command("backfill_artwork_derivatives", verbosity=0)   # idempotentne
        after = CartonArtwork.objects.get(pk=art.pk)
        self.assertEqual(after.image_display.name, art.image_display.name)


class MediaCacheHeaderTests(MediaTempMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(_md_user())

    def test_artwork_is_immutable_but_hu_photos_are_not(self):
        c = _carton()
        self.client.post(reverse("ui:carton_artwork_upload", args=[c.pk]),
                         {"image": _upload(), "face": "front", "kind": "print"})
        art = CartonArtwork.objects.get()
        resp = self.client.get(art.image_display.url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("immutable", resp["Cache-Control"])
        # zdjęcia HU bywają podmieniane pod tą samą ścieżką — tam cache byłby błędem
        import os
        os.makedirs(os.path.join(self.media, "quality"), exist_ok=True)
        with open(os.path.join(self.media, "quality", "p.png"), "wb") as fh:
            fh.write(_png(8, 8))
        resp = self.client.get("/media/quality/p.png")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("immutable", resp.get("Cache-Control", ""))
