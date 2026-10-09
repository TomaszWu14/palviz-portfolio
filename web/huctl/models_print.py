"""Modele domeny Kontrola HU (huctl) — W2 wydzielenia.

Przeniesione z ui.models przez SeparateDatabaseAndState: TABELE zostają pod
starymi nazwami ui_* (jawne db_table), zmienia się tylko własność w stanie
migracji. ui.models re-eksportuje te nazwy dla kompatybilności importów.
FK do master/transport (transport.Shipment, ui.Product) jako stringi — bez cyklu importu.
"""
from django.db import models


class HUPrintProject(models.Model):
    """Numbering stream for HU label printing.

    Each project (Projekt Alfa, Projekt Beta, Projekt Gamma, Delta…) owns its own **ascending**
    sequence living in a distinct number band (the leading digit: 1 = Projekt Alfa,
    2 = Projekt Beta, 3 = Projekt Gamma, 4 = Delta). Printing takes the next N consecutive numbers
    and advances the counter atomically. The label carries: barcode(number) + the number +
    the project name (blank name — e.g. Projekt Alfa — prints only the number)."""
    name = models.CharField(max_length=60, verbose_name="Projekt")
    # Big text printed on the label. Blank → only the number is printed (Projekt Alfa).
    label_text = models.CharField(max_length=40, blank=True, default="",
                                  verbose_name="Nazwa na etykiecie",
                                  help_text="Duży tekst na etykiecie; puste = tylko numer (np. Projekt Alfa).")
    # Next number to print. Editable start ("na pewno nie od zera"); each band starts high.
    next_number = models.BigIntegerField(default=1, verbose_name="Następny numer",
                                          help_text="Kolejny numer do wydruku (edytowalny start).")
    digits = models.PositiveSmallIntegerField(default=9, verbose_name="Liczba cyfr (zero-padding)")
    is_active = models.BooleanField(default=True, verbose_name="Aktywny")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ui_huprintproject"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["next_number", "name"]
        verbose_name = "Projekt wydruku HU"
        verbose_name_plural = "Projekty wydruku HU"

    def __str__(self):
        return self.name

    def format_number(self, n):
        """Zero-padded string for the given integer (e.g. 4001 → '200004001')."""
        return str(int(n)).zfill(self.digits)


class HUPrintRun(models.Model):
    """Audit record of one label-print batch — who printed which number range, when.
    One row per print run (never per label), so a 10 000-label batch is a single row."""
    project = models.ForeignKey(HUPrintProject, on_delete=models.CASCADE, related_name="runs")
    user = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="hu_print_runs")
    # Permanent username snapshot — the audit log must stay complete ("kto i ile wydrukował")
    # even if the user account is later deleted (which nulls `user`).
    username = models.CharField(max_length=150, blank=True, default="", verbose_name="Użytkownik")
    quantity = models.PositiveIntegerField(verbose_name="Ilość etykiet")
    from_number = models.BigIntegerField(verbose_name="Od numeru")
    to_number = models.BigIntegerField(verbose_name="Do numeru")
    symbology = models.CharField(max_length=10, default="code128", verbose_name="Symbolika")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "ui_huprintrun"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["-created_at"]
        verbose_name = "Przebieg wydruku HU"
        verbose_name_plural = "Przebiegi wydruku HU"

    def __str__(self):
        return f"{self.project_id}: {self.from_number}–{self.to_number} ({self.quantity} szt.)"

__all__ = [n for n in list(globals().keys()) if not n.startswith('__')]
