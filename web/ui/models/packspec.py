from django.db import models
from .catalog import Product

class PackSpecBatch(models.Model):
    """Partia importu packspec (E3) — dane ilościowe paletyzacji dla przyjęć,
    wgrywane plikiem Excel/CSV (decyzja: import pliku). Nowa partia dezaktywuje stare."""
    name = models.CharField(max_length=120, verbose_name="Nazwa partii")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Wgrano")
    uploaded_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name="+")
    row_count = models.PositiveIntegerField(default=0, verbose_name="Liczba wierszy")
    is_active = models.BooleanField(default=True, verbose_name="Aktywna")

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "Partia packspec"
        verbose_name_plural = "Partie packspec"

    def __str__(self):
        return f"{self.name} ({self.uploaded_at:%Y-%m-%d})"


class PackSpec(models.Model):
    """Packspec przyjęcia (E3): deklarowane ilości paletyzacji dla indeksu.
    Walidowane vs master data (PalletizationInstruction/MARM) — rozbieżność daje
    alert w widoku przyjęć + zgłoszenie packspec_mismatch jednym kliknięciem."""
    batch = models.ForeignKey(PackSpecBatch, on_delete=models.CASCADE, related_name="rows")
    ref_code = models.CharField(max_length=50, db_index=True, verbose_name="REF / indeks")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="packspecs")
    pcs_per_carton = models.PositiveIntegerField(null=True, blank=True,
                                                 verbose_name="Szt / karton (packspec)")
    cartons_per_pallet = models.PositiveIntegerField(null=True, blank=True,
                                                     verbose_name="Kartony / paleta (packspec)")
    pcs_per_pallet = models.PositiveIntegerField(null=True, blank=True,
                                                 verbose_name="Szt / paleta (packspec)")
    note = models.CharField(max_length=200, blank=True, verbose_name="Uwagi")

    class Meta:
        ordering = ["ref_code"]
        verbose_name = "Packspec"
        verbose_name_plural = "Packspec"

    def __str__(self):
        return f"{self.ref_code} ({self.batch_id})"


class ImportRun(models.Model):
    """Ślad KAŻDEGO importu danych (BLOK F) — cienka nakładka na istniejące, per-import
    batche (WarehouseLocationMasterBatch/WarehouseSnapshot/WarehouseLayout/
    PickerActivityBatch zostają). Jeden wiersz per uruchomienie; panel statusu
    pokazuje ostatni per `kind` + ostrzega, gdy starszy niż próg."""
    KINDS = [
        ("products", "Produkty (pełny szablon)"),
        ("marm", "SAP MARM"),
        ("cartons", "Kartony"),
        ("inner_packs", "Opakowania zbiorcze"),
        ("categories", "Kategorie"),
        ("ref_materials", "Materiały referencyjne"),
        ("fix_locations", "Fixy (stałe lokalizacje)"),
        ("customers", "Klienci"),
        ("users", "Użytkownicy"),
        ("locations_master", "Lokalizacje — master"),
        ("warehouse_snapshot", "Snapshot zajętości (SAP WMS)"),
        ("warehouse_layout", "Layout magazynu"),
        ("picker_activity", "Aktywność pickerów (heatmapa)"),
        ("hu_stock_file", "Wsad HU (plik)"),
        ("hu_stock_powerbi", "Stock z Power BI"),
        ("packspec", "Packspec przyjęć"),
        ("producer_dims", "Wymiary producenta"),
    ]
    STATUS = [("ok", "OK"), ("error", "Błąd"), ("running", "W toku")]
    kind = models.CharField(max_length=32, choices=KINDS, db_index=True,
                            verbose_name="Źródło danych")
    label = models.CharField(max_length=200, blank=True, verbose_name="Etykieta (np. plik)")
    status = models.CharField(max_length=8, choices=STATUS, default="ok")
    row_count = models.PositiveIntegerField(default=0, verbose_name="Liczba rekordów")
    error_message = models.CharField(max_length=400, blank=True, verbose_name="Ostatni błąd")
    user = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="+", verbose_name="Uruchomił")
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Przebieg importu"
        verbose_name_plural = "Przebiegi importów"

    def __str__(self):
        return f"{self.get_kind_display()} {self.started_at:%Y-%m-%d %H:%M} ({self.status})"

    @classmethod
    def record(cls, kind, *, rows=0, label="", user=None, error=""):
        """Jednolinijkowy zapis w ścieżkach importu: sukces albo błąd."""
        from django.utils import timezone as _tz
        return cls.objects.create(
            kind=kind, label=str(label)[:200], row_count=rows or 0,
            status="error" if error else "ok", error_message=str(error)[:400],
            user=user if (user is not None and getattr(user, "pk", None)) else None,
            finished_at=_tz.now())

