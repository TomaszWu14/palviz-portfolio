"""Zadania magazynowe EWM (WT) — import z monitora magazynu (/SCWM/MON) do animacji
przepływów, a dalej do profilu, dnia projektowego i symulacji (spec 2026-09-26, krok 2).

Skala: ~3 lata zadań (roboczo 6 miesięcy) — wiersze tylko przez bulk_create partiami,
zapytania zawsze w obrębie partii importu po czasie potwierdzenia albo lokalizacji.
"""
from django.conf import settings
from django.db import models

__all__ = ["TASK_KINDS", "WarehouseTaskBatch", "WarehouseTask"]

TASK_KINDS = [
    ("putaway", "Przyjęcie / odłożenie"),
    ("replenishment", "Uzupełnienie"),
    ("picking", "Kompletacja"),
    ("outbound", "Wydanie / załadunek"),
    ("move", "Przesunięcie"),
]


class WarehouseTaskBatch(models.Model):
    STATUS_CHOICES = [("queued", "W kolejce"), ("running", "Import trwa"),
                      ("done", "Zaimportowano"), ("error", "Błąd")]
    TZ_CHOICES = [("Europe/Warsaw", "Czas lokalny (Europe/Warsaw)"), ("UTC", "UTC")]

    name = models.CharField(max_length=200, verbose_name="Nazwa importu")
    file_name = models.CharField(max_length=255, blank=True, default="", verbose_name="Plik")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="+")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="queued")
    message = models.TextField(blank=True, default="", verbose_name="Komunikat")
    tz = models.CharField(max_length=40, choices=TZ_CHOICES, default="Europe/Warsaw",
                          verbose_name="Strefa czasowa pliku")
    kind_map = models.JSONField(default=dict, blank=True,
                                verbose_name="Nadpisania mapowania (proces → rodzaj)")
    row_count = models.IntegerField(default=0, verbose_name="Zadań")
    error_count = models.IntegerField(default=0, verbose_name="Błędnych wierszy")
    first_confirmed = models.DateTimeField(null=True, blank=True)
    last_confirmed = models.DateTimeField(null=True, blank=True)
    stats = models.JSONField(default=dict, blank=True, verbose_name="Statystyki")

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "Import zadań magazynowych EWM"
        verbose_name_plural = "Importy zadań magazynowych EWM"

    def __str__(self):
        return f"{self.name} ({self.row_count} zadań)"


class WarehouseTask(models.Model):
    batch = models.ForeignKey(WarehouseTaskBatch, on_delete=models.CASCADE, related_name="tasks")
    task_no = models.CharField(max_length=20, blank=True, default="", verbose_name="Nr WT")
    process_type = models.CharField(max_length=10, blank=True, default="",
                                    verbose_name="Rodzaj procesu mag.")
    kind = models.CharField(max_length=14, choices=TASK_KINDS, default="move", verbose_name="Rodzaj ruchu")
    src_location = models.CharField(max_length=50, blank=True, default="", verbose_name="Lokalizacja źródłowa")
    dst_location = models.CharField(max_length=50, blank=True, default="", verbose_name="Lokalizacja docelowa")
    material = models.CharField(max_length=50, blank=True, default="", verbose_name="Materiał")
    lot = models.CharField(max_length=32, blank=True, default="", verbose_name="Partia")
    qty = models.FloatField(null=True, blank=True, verbose_name="Ilość")
    unit = models.CharField(max_length=10, blank=True, default="", verbose_name="JM")
    src_hu = models.CharField(max_length=40, blank=True, default="", verbose_name="HU źródłowa")
    dst_hu = models.CharField(max_length=40, blank=True, default="", verbose_name="HU docelowa")
    document = models.CharField(max_length=35, blank=True, default="", verbose_name="Dokument")
    created_at = models.DateTimeField(null=True, blank=True, verbose_name="Utworzono")
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name="Potwierdzono")
    user = models.CharField(max_length=40, blank=True, default="", verbose_name="Użytkownik")
    resource = models.CharField(max_length=40, blank=True, default="", verbose_name="Zasób")
    queue = models.CharField(max_length=40, blank=True, default="", verbose_name="Kolejka")

    class Meta:
        verbose_name = "Zadanie magazynowe EWM"
        verbose_name_plural = "Zadania magazynowe EWM"
        indexes = [
            models.Index(fields=["batch", "confirmed_at"]),
            models.Index(fields=["batch", "src_location"]),
            models.Index(fields=["batch", "dst_location"]),
        ]

    def __str__(self):
        return f"WT {self.task_no} {self.src_location}→{self.dst_location}"
