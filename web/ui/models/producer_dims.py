from django.conf import settings
from django.db import models


class ProducerCartonBatch(models.Model):
    """Jedna partia importu = jeden plik dostawcy. Ponowny import dostawcy dezaktywuje
    poprzednią aktywną partię (wzorzec packspec)."""
    supplier = models.CharField(max_length=120, db_index=True, verbose_name="Dostawca")
    source_filename = models.CharField(max_length=255, blank=True, verbose_name="Plik źródłowy")
    uploaded_at = models.DateTimeField(auto_now_add=True, db_index=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL)
    is_active = models.BooleanField(default=True, db_index=True)
    row_count = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Partia wymiarów producenta"
        verbose_name_plural = "Partie wymiarów producenta"
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.supplier} ({self.uploaded_at:%Y-%m-%d %H:%M})"


class ProducerCartonDim(models.Model):
    """Deklarowane przez producenta wymiary kartonu dla jednego REF (jeden wiersz pliku)."""
    batch = models.ForeignKey(ProducerCartonBatch, on_delete=models.CASCADE,
                              related_name="rows")
    supplier = models.CharField(max_length=120, db_index=True)   # denorm: filtr/dedup
    ref_code = models.CharField(max_length=100, db_index=True, verbose_name="REF")
    product = models.ForeignKey("ui.Product", null=True, blank=True,
                                on_delete=models.SET_NULL)
    carton_l = models.FloatField(null=True, blank=True, verbose_name="L kartonu [cm]")
    carton_w = models.FloatField(null=True, blank=True, verbose_name="W kartonu [cm]")
    carton_h = models.FloatField(null=True, blank=True, verbose_name="H kartonu [cm]")
    qty_in_carton = models.IntegerField(null=True, blank=True)
    gross_kg = models.FloatField(null=True, blank=True)
    net_kg = models.FloatField(null=True, blank=True)
    box_size_raw = models.CharField(max_length=120, blank=True)      # podgląd, nieporównywane
    pouch_size_raw = models.CharField(max_length=120, blank=True)    # podgląd, nieporównywane
    description = models.CharField(max_length=250, blank=True)

    class Meta:
        verbose_name = "Wymiar kartonu producenta"
        verbose_name_plural = "Wymiary kartonu producenta"
        indexes = [models.Index(fields=["supplier", "ref_code"])]

    def __str__(self):
        return f"{self.supplier}/{self.ref_code}"


__all__ = ["ProducerCartonBatch", "ProducerCartonDim"]
