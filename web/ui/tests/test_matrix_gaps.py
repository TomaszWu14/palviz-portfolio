"""Biblioteka grafik: licznik braków + filtr „tylko braki" (panel zarządzania grafikami)."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from ui.models import Product, PalletizationInstruction, Carton


class MatrixGapsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("mx_admin", "a@a.pl", "x")
        self.client.force_login(self.admin)
        # Produkt z kartonem, bez wgranej grafiki → poziom „karton" = brak (todo).
        p = Product.objects.create(code="GAP-1", name="Bez grafiki",
                                   unit_length_cm=10, unit_width_cm=8, unit_height_cm=5)
        c = Carton.objects.create(name="K", length_cm=40, width_cm=30, height_cm=25,
                                  unit_weight_kg=0.5, pieces_per_carton=10)
        PalletizationInstruction.objects.create(
            product=p, version=1, is_active=True, unit_weight=0.5, pcs_per_carton=10,
            carton=c, carton_l=40, carton_w=30, carton_h=25,
            pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15,
            max_height_total_cm=200)

    def test_badge_counts_missing(self):
        r = self.client.get(reverse("ui:packaging_matrix"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "braki:")                     # badge obecny
        self.assertContains(r, "Pokaż tylko braki")          # przycisk filtra
        self.assertGreaterEqual(r.context["summary"]["todo"], 1)
        self.assertGreaterEqual(r.context["summary"]["gap_rows"], 1)

    def test_row_marked_with_gap_flag(self):
        r = self.client.get(reverse("ui:packaging_matrix"))
        self.assertContains(r, 'data-gap="1"')               # wiersz oznaczony do filtra
