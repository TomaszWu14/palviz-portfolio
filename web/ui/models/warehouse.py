from django.db import models

LOCATION_CLASS_CHOICES = [
    ("pallet_full",  "Paletowe pełne (≥80 cm)"),
    ("pallet_half",  "Półpaletowe (40–79 cm)"),
    ("floor",        "Podłogowe / BLC"),
    ("shelf",        "Półkowe"),
    ("other",        "Inne"),
]


class WarehouseLocationType(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nazwa lokalizacji")
    location_class = models.CharField(
        max_length=20, choices=LOCATION_CLASS_CHOICES, default="pallet_full",
        verbose_name="Klasa lokalizacji")
    is_pallet_location = models.BooleanField(
        default=True, verbose_name="Lokalizacja paletowa",
        help_text="Towar wstawiany jest na palecie (odlicza wys. palety od podstawy)")
    max_load_kg = models.FloatField(
        null=True, blank=True, verbose_name="Max obciążenie [kg]")
    width_cm = models.IntegerField(verbose_name="Szerokość [cm]", help_text="Światło w miejscu paletowym")
    depth_cm = models.IntegerField(verbose_name="Głębokość [cm]")
    total_height_cm = models.IntegerField(verbose_name="Wysokość całkowita [cm]")
    pallet_height_cm = models.IntegerField(default=15, verbose_name="Wys. palety [cm]")
    manipulation_margin_cm = models.IntegerField(default=20, verbose_name="Margines manipulacji [cm]",
        help_text="Wolna przestrzeń na wstawienie wózkiem")
    notes = models.TextField(blank=True, verbose_name="Uwagi")
    is_active = models.BooleanField(default=True, verbose_name="Aktywna")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        # Katalog klas lokalizacji do symulacji pakowania (cm) — ODRĘBNY od WarehouseRackType
        # (fizyczny szablon regału 3D, mm). Nazwy rozróżnione, by nie myliły się w adminie/UI.
        verbose_name = "Klasa lokalizacji (symulacja)"
        verbose_name_plural = "Klasy lokalizacji (symulacja)"

    def __str__(self):
        return f"{self.name} ({self.width_cm}×{self.depth_cm}×{self.total_height_cm} cm)"

    @property
    def usable_height_cm(self):
        return max(0, self.total_height_cm - self.pallet_height_cm - self.manipulation_margin_cm)

    @property
    def volume_m3(self):
        # usable_height_cm always deducts a pallet; for a shelf (non-pallet) location
        # there is no pallet, so add that height back — matches the fitting helpers
        # (_location_fit / _fig_location_3d), which compensate the same way.
        h = self.usable_height_cm + (self.pallet_height_cm if not self.is_pallet_location else 0)
        return round(self.width_cm * self.depth_cm * h / 1_000_000, 3)


# ─── Warehouse Map / Snapshot models ─────────────────────────────────────────

