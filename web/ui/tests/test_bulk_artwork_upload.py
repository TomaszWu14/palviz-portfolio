"""Masowy upload grafik wg nazwy pliku REF_poziom_rewizja (karton/opz/sztuka)."""
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import (Product, PalletizationInstruction, Carton, CartonArtwork,
                       ProductAlias)

PNG = b"\x89PNG\r\n\x1a\n"


def _png(name):
    return SimpleUploadedFile(name, PNG, content_type="image/png")


class BulkArtworkUploadTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("bulk_admin", "a@a.pl", "x")
        self.client.force_login(self.admin)
        self.p = Product.objects.create(code="DMOM10001", name="X",
                                        unit_length_cm=10, unit_width_cm=8, unit_height_cm=5)
        self.carton = Carton.objects.create(name="K", length_cm=40, width_cm=30, height_cm=25,
                                            unit_weight_kg=0.5, pieces_per_carton=10)
        PalletizationInstruction.objects.create(
            product=self.p, version=1, is_active=True, unit_weight=0.5, pcs_per_carton=10,
            carton=self.carton, carton_l=40, carton_w=30, carton_h=25,
            pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15,
            max_height_total_cm=200)
        self.url = reverse("ui:packaging_matrix_bulk_upload")

    def test_uploads_carton_and_keeps_revision(self):
        self.client.post(self.url, {"files": [_png("DMOM10001_karton_R2.png")]})
        art = CartonArtwork.objects.get(carton=self.carton, face="front", kind="print")
        self.assertEqual(art.name, "R2")                     # rewizja z nazwy pliku

    def test_no_revision_ok(self):
        self.client.post(self.url, {"files": [_png("DMOM10001_karton.png")]})
        art = CartonArtwork.objects.get(carton=self.carton, kind="print")
        self.assertEqual(art.name, "")                       # brak rewizji → pusto

    def test_ref_alias_resolves(self):
        ProductAlias.objects.create(product=self.p, alias_code="DMO-M-100")
        self.client.post(self.url, {"files": [_png("DMO-M-100_karton.png")]})
        self.assertTrue(CartonArtwork.objects.filter(carton=self.carton).exists())

    def test_bad_names_and_unknown_ref_skipped(self):
        r = self.client.post(self.url, {"files": [
            _png("zlanazwa.png"),                 # brak '_'
            _png("DMOM10001_kosmos.png"),         # nieznany poziom
            _png("NIEMA_karton.png"),             # nieznany indeks
        ]}, follow=True)
        self.assertEqual(CartonArtwork.objects.count(), 0)
        self.assertEqual(r.status_code, 200)
