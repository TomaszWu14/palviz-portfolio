"""Modele domeny Magazyn 3D (wh3d) — W2 wydzielenia.

Przeniesione z ui.models przez SeparateDatabaseAndState: TABELE zostają pod
starymi nazwami ui_* (jawne db_table), zmienia się tylko własność w stanie
migracji. ui.models re-eksportuje te nazwy dla kompatybilności importów.
Klucz integracji z resztą GROOVE = string location_code (bez FK do Product/HU).
"""
import re

from django.core.exceptions import ValidationError
from django.db import models

from .addressing import parse_bay_numbers


class WarehouseSnapshot(models.Model):
    name = models.CharField(max_length=200, verbose_name="Nazwa")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    row_count = models.IntegerField(default=0)
    occupied_count = models.IntegerField(default=0)
    blocked_count = models.IntegerField(default=0)

    class Meta:
        db_table = "ui_warehousesnapshot"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.name} ({self.uploaded_at:%Y-%m-%d})"



class WarehouseSnapshotRow(models.Model):
    snapshot = models.ForeignKey(WarehouseSnapshot, on_delete=models.CASCADE, related_name="rows")
    location_code = models.CharField(max_length=50, db_index=True)
    # SAP WMS fields
    warehouse_type = models.CharField(max_length=10, blank=True, default="")
    section = models.CharField(max_length=20, blank=True, default="")
    storage_group = models.CharField(max_length=50, blank=True, default="")
    is_empty = models.BooleanField(default=True)
    blocked_pick = models.BooleanField(default=False)
    blocked_put = models.BooleanField(default=False)
    capacity_mm = models.IntegerField(default=0)   # max material height in mm
    # Parsed from location code: B0-01-100A
    zone = models.CharField(max_length=10, blank=True, default="")   # B0
    aisle = models.CharField(max_length=10, blank=True, default="")  # 01
    stack = models.CharField(max_length=10, blank=True, default="")  # 100
    col_code = models.CharField(max_length=5, blank=True, default="")  # A/B/C/X/Y/Z
    level = models.IntegerField(default=1)   # 1-4 derived from col_code or SAP field
    col_idx = models.IntegerField(default=0) # 0/1/2 for A/B/C; 0 for X/Y/Z

    class Meta:
        db_table = "ui_warehousesnapshotrow"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["location_code", "id"]   # id tiebreaker — location_code is non-unique


# ─── Warehouse Layout models ──────────────────────────────────────────────────



class WarehouseLayout(models.Model):
    """Physical warehouse map — location codes with their X/Z grid positions."""
    name = models.CharField(max_length=200, default="Layout")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    location_count = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "ui_warehouselayout"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.name} ({self.location_count} lok.)"



class WarehouseLayoutCell(models.Model):
    """One location in the physical layout with grid position."""
    layout = models.ForeignKey(WarehouseLayout, on_delete=models.CASCADE, related_name="cells")
    location_code = models.CharField(max_length=50, db_index=True)
    grid_row = models.IntegerField()   # Excel row → physical depth (Z in 3D)
    grid_col = models.IntegerField()   # Excel col → physical width (X in 3D)
    level = models.IntegerField(default=1)  # derived from suffix: A/B/C=1, X=2, Y=3, Z=4

    class Meta:
        db_table = "ui_warehouselayoutcell"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["layout", "id"]   # deterministic for pagination/iteration
        unique_together = [("layout", "location_code")]
        indexes = [models.Index(fields=["layout", "grid_row", "grid_col"])]



class WarehouseAisleConfig(models.Model):
    """Konfiguracja pojedynczej alei aktywnego layoutu: szerokość korytarza (w metrach)
    i kąt obrotu (używany w Etapie 4). Brak wiersza = domyślny rozstaw jak dotychczas."""
    layout = models.ForeignKey(WarehouseLayout, on_delete=models.CASCADE, related_name="aisles")
    aisle = models.CharField(max_length=30, verbose_name="Aleja")
    width_m = models.FloatField(default=2.0, verbose_name="Szerokość korytarza [m]")
    angle_deg = models.FloatField(default=0, verbose_name="Kąt obrotu [°]")
    notes = models.CharField(max_length=200, blank=True, verbose_name="Uwagi")

    class Meta:
        db_table = "ui_warehouseaisleconfig"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["aisle"]
        unique_together = [("layout", "aisle")]
        verbose_name = "Konfiguracja alei"
        verbose_name_plural = "Konfiguracje alej"

    def __str__(self):
        return f"{self.aisle} ({self.width_m} m)"


# ─── Warehouse Rack Types ─────────────────────────────────────────────────────



class WarehouseRackType(models.Model):
    """Named location type template — dimensions apply to all locations with matching wh_type code.

    level_heights stores clearance per level: {"1": 2494, "2": 2500, "3": 2500, "4": 2000}
    Assignment is implicit: location's warehouse_type field == this type's code.
    """
    KIND = [("rack", "Regał"), ("zone", "Strefa")]
    code         = models.CharField(max_length=20, unique=True, verbose_name="Kod typu")
    name         = models.CharField(max_length=100, verbose_name="Nazwa")
    # Regał = fizyczna konstrukcja z wymiarami do 3D; strefa = obszar podłogowy/techniczny
    # (wysyłka, bufor, laboratorium…) bez geometrii regału. Decyzja usera 2026-08-24:
    # wymiary definiujemy TYLKO dla 0010/0011/0050/0052/0070.
    kind         = models.CharField(max_length=8, choices=KIND, default="rack",
                                    verbose_name="Rodzaj")
    description  = models.CharField(max_length=200, blank=True, default="",
                                    verbose_name="Opis (znaczenie kodu)")
    width_mm     = models.IntegerField(default=800,  verbose_name="Szer. fiz. miejsca [mm]")
    manip_mm     = models.IntegerField(default=900,  verbose_name="Szer. z manipulacją [mm]")
    depth_mm     = models.IntegerField(default=1100, verbose_name="Głębokość [mm]")
    max_weight_kg = models.FloatField(default=1200,  verbose_name="Max waga [kg]")
    max_volume_m3 = models.FloatField(default=2.5,   verbose_name="Max objętość [m³]")
    level_heights = models.JSONField(default=dict,   verbose_name="Wysokości poziomów [mm]")
    level_cols    = models.JSONField(
        default=dict,
        verbose_name="Kolumny per poziom",
        help_text='np. {"1":1,"2":2,"3":1} — ile kolumn na każdym poziomie. Puste = 1 wszędzie.'
    )
    level_weights = models.JSONField(
        default=dict,
        verbose_name="Nośność poziomów [kg]",
        help_text='np. {"1":1000,"2":300} — max waga na poziom (etykiety kg w widoku 3D).'
    )
    color_hex    = models.CharField(max_length=7, default="#f59e0b", verbose_name="Kolor 3D")
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ui_warehouseracktype"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["code"]
        # Fizyczny szablon regału do widoku 3D (mm), wiązany z lokalizacją przez
        # code == warehouse_type. ODRĘBNY od WarehouseLocationType (symulacja pakowania, cm).
        verbose_name = "Typ regału (3D)"
        verbose_name_plural = "Typy regałów (3D)"

    def __str__(self):
        return f"{self.code} — {self.name}"


# ─── Warehouse Location Master Data ──────────────────────────────────────────



class WarehouseLocationMasterBatch(models.Model):
    """One import of location master data (height, volume, weight, type)."""
    name = models.CharField(max_length=200)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    location_count = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "ui_warehouselocationmasterbatch"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.name} ({self.location_count} lok.)"



class WarehouseLocationMaster(models.Model):
    """Master data for a single warehouse location."""
    batch = models.ForeignKey(WarehouseLocationMasterBatch, on_delete=models.CASCADE, related_name="locations")
    location_code = models.CharField(max_length=50, db_index=True)
    level = models.IntegerField(default=1)
    warehouse_type = models.CharField(max_length=50, blank=True, default="")
    height_mm = models.IntegerField(default=0)
    width_mm = models.IntegerField(default=0)   # physical slot width (e.g. 800mm)
    depth_mm = models.IntegerField(default=0)   # rack depth (e.g. 1100mm)
    max_volume_m3 = models.FloatField(default=0.0)
    max_weight_kg = models.FloatField(default=0.0)
    # Blokady SAP (wyd./um.) — dane były wczytywane z eksportu, ale wyrzucane; teraz
    # zapisywane, żeby skaner (PHV) mógł pokazać „lokalizacja zablokowana".
    blocked_pick = models.BooleanField(default=False, verbose_name="Blokada wydania")
    blocked_put = models.BooleanField(default=False, verbose_name="Blokada umieszczania")

    class Meta:
        db_table = "ui_warehouselocationmaster"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["location_code", "id"]   # deterministic for pagination/iteration
        unique_together = [("batch", "location_code")]


# ─── Szablony gniazd i adresy (edytor układu, część 1) ───────────────────────


def validate_bay_numbers(value):
    """Numeracja gniazd rzędu: zakresy „10-47,50”."""
    try:
        parse_bay_numbers(value)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


class BayTemplate(models.Model):
    """Szablon gniazda (słupa regału): belka, palety na belce, poziomy od podłogi w górę.

    levels: [{"letter": "B", "height_mm": 400, "ewm_type": "0052", "split": false, "max_kg": 1000}, …]
    `split` = miejsce dzielone wszerz na dwie połówki (kody z końcówką -1/-2)."""
    name = models.CharField(max_length=100, verbose_name="Nazwa")
    beam_mm = models.IntegerField(default=2700, verbose_name="Szerokość belki [mm]")
    pallets_per_beam = models.PositiveSmallIntegerField(default=3, verbose_name="Palet na belce")
    depth_mm = models.IntegerField(default=1100, verbose_name="Głębokość [mm]")
    levels = models.JSONField(default=list, blank=True, verbose_name="Poziomy (od dołu)")
    notes = models.CharField(max_length=200, blank=True, default="", verbose_name="Uwagi")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Szablon gniazda"
        verbose_name_plural = "Szablony gniazd"

    def __str__(self):
        return self.name

    @property
    def level_label(self):
        return " ".join(lv["letter"] + ("½" if lv.get("split") else "") for lv in self.levels)

    @property
    def ewm_types_label(self):
        return "/".join(dict.fromkeys(lv["ewm_type"] for lv in self.levels if lv.get("ewm_type")))

    def clean(self):
        errors = []
        if not 1 <= (self.pallets_per_beam or 0) <= 10:
            errors.append("Liczba palet na belce musi być w zakresie 1–10 (pozycja palety w kodzie to jedna cyfra).")
        if not self.levels:
            errors.append("Szablon musi mieć co najmniej jeden poziom.")
        letters = [lv.get("letter") for lv in self.levels]
        if len(set(letters)) != len(letters):
            errors.append("Litery poziomów muszą być unikalne.")
        for lv in self.levels:
            if not re.fullmatch(r"[A-Z]", lv.get("letter") or ""):
                errors.append(f"Nieprawidłowa litera poziomu: „{lv.get('letter')}” (jedna wielka litera A–Z).")
            height = lv.get("height_mm")
            if not isinstance(height, int) or height <= 0:
                errors.append(f"Poziom {lv.get('letter')}: wysokość musi być > 0 mm.")
        if errors:
            raise ValidationError(errors)


class WarehouseModel(models.Model):
    name = models.CharField(max_length=200, verbose_name="Nazwa modelu")
    notes = models.TextField(blank=True, verbose_name="Uwagi")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    floor_width_m = models.FloatField(default=50, verbose_name="Szerokość hali [m]")
    floor_depth_m = models.FloatField(default=30, verbose_name="Głębokość hali [m]")

    class Meta:
        db_table = "ui_warehousemodel"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["-created_at"]
        verbose_name = "Model magazynu"
        verbose_name_plural = "Modele magazynów"

    def __str__(self):
        return self.name

    def rack_count(self):
        return self.racks.count()



class WarehouseModelRack(models.Model):
    model = models.ForeignKey(WarehouseModel, on_delete=models.CASCADE, related_name="racks")
    zone = models.CharField(max_length=20, verbose_name="Strefa", db_index=True)
    rack_id = models.CharField(max_length=20, verbose_name="Nr regału")
    n_bays = models.IntegerField(default=1, verbose_name="Liczba boków (bays)")
    n_levels = models.IntegerField(default=3, verbose_name="Liczba poziomów")
    bay_width_cm = models.IntegerField(default=100, verbose_name="Szerokość boku [cm]")
    depth_cm = models.IntegerField(default=80, verbose_name="Głębokość regału [cm]")
    level_height_cm = models.IntegerField(default=200, verbose_name="Wys. poziomów [cm]")
    x_m = models.FloatField(null=True, blank=True, verbose_name="Pozycja X [m]")
    y_m = models.FloatField(null=True, blank=True, verbose_name="Pozycja Y [m]")
    angle_deg = models.FloatField(default=0, verbose_name="Kąt obrotu [°]")
    # Edytor układu, część 1: szablon domyślny gniazd + reguła adresu (numeracja, kierunek).
    template = models.ForeignKey(BayTemplate, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="racks", verbose_name="Szablon gniazda")
    bay_numbers = models.CharField(max_length=200, blank=True, default="", validators=[validate_bay_numbers],
                                   verbose_name="Numeracja gniazd",
                                   help_text="Zakresy, np. 10-47,50. Puste = 1…liczba gniazd.")
    reverse = models.BooleanField(default=False, verbose_name="Numeracja od końca rzędu")

    class Meta:
        db_table = "ui_warehousemodelrack"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["zone", "rack_id"]
        unique_together = [("model", "zone", "rack_id")]
        verbose_name = "Regał modelu"
        verbose_name_plural = "Regały modelu"

    def __str__(self):
        return f"{self.zone}-{self.rack_id}"

    @property
    def width_m(self):
        return round(self.n_bays * self.bay_width_cm / 100, 2)


class LocationOverride(models.Model):
    """Wyjątek adresu: nadpisuje wynik szablonu dla jednego miejsca albo całego gniazda (letter = "")."""
    ACTIONS = [
        ("template", "Inny szablon gniazda"),
        ("skip", "Pomiń"),
        ("add", "Dodaj miejsce"),
        ("rename", "Własny adres"),
        ("ewm_type", "Inny typ EWM"),
        ("split", "Podziel na połówki"),
        ("unsplit", "Scal połówki"),
        ("block", "Blokada"),
    ]
    rack = models.ForeignKey(WarehouseModelRack, on_delete=models.CASCADE, related_name="overrides")
    bay = models.IntegerField(verbose_name="Gniazdo (numer z adresu)")
    position = models.PositiveSmallIntegerField(default=0, verbose_name="Pozycja palety")
    letter = models.CharField(max_length=1, blank=True, default="", verbose_name="Litera poziomu")
    half = models.PositiveSmallIntegerField(default=0, verbose_name="Połówka (0/1/2)")
    action = models.CharField(max_length=10, choices=ACTIONS, verbose_name="Wyjątek")
    value = models.CharField(max_length=50, blank=True, default="", verbose_name="Wartość (kod / typ EWM)")
    template = models.ForeignKey(BayTemplate, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="bay_overrides", verbose_name="Szablon (dla wyjątku gniazda)")

    class Meta:
        ordering = ["rack", "bay", "letter", "position", "half"]
        verbose_name = "Wyjątek adresu"
        verbose_name_plural = "Wyjątki adresów"
        constraints = [
            models.UniqueConstraint(fields=["rack", "bay", "position", "letter", "half", "action"],
                                    name="locationoverride_unique"),
        ]

    def __str__(self):
        return f"{self.rack} gn. {self.bay} {self.letter or '*'}: {self.get_action_display()}"


class WarehouseHallFeature(models.Model):
    """Element hali, którego siatka regałów nie odwzoruje: dok, brama, korytarz,
    strefa blokowa/nietypowa, strefa zwrotów, stanowisko lidera/kontroli."""
    KIND_CHOICES = [
        ("dock",       "Dok przeładunkowy"),
        ("gate",       "Brama"),
        ("corridor",   "Korytarz / ciąg komunikacyjny"),
        ("block_zone", "Strefa blokowa / nietypowa"),
        ("returns",    "Strefa zwrotów"),
        ("leader",     "Stanowisko lidera"),
        ("station",    "Stanowisko / punkt kontroli"),
        ("other",      "Inny obszar"),
    ]
    # Wspólny katalog dla obu modułów: ręczny model (module B) LUB layout mapy 3D
    # (module A). Dokładnie jeden FK ustawiony — pilnuje tego CRUD.
    model = models.ForeignKey(WarehouseModel, on_delete=models.CASCADE,
                              related_name="features", null=True, blank=True)
    layout = models.ForeignKey("WarehouseLayout", on_delete=models.CASCADE,
                               related_name="features", null=True, blank=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="other", verbose_name="Typ elementu")
    label = models.CharField(max_length=100, blank=True, verbose_name="Etykieta")
    zone_code = models.CharField(max_length=20, blank=True, verbose_name="Prefiks kodów lokalizacji",
                                 help_text="Dla stref blokowych/zwrotów — łapie kody po prefiksie")
    x_m = models.FloatField(default=0, verbose_name="Pozycja X [m]")
    y_m = models.FloatField(default=0, verbose_name="Pozycja Y [m]")
    width_m = models.FloatField(default=2, verbose_name="Szerokość [m]")
    depth_m = models.FloatField(default=2, verbose_name="Głębokość [m]")
    angle_deg = models.FloatField(default=0, verbose_name="Kąt obrotu [°]")
    color_hex = models.CharField(max_length=7, blank=True, verbose_name="Kolor")
    notes = models.CharField(max_length=200, blank=True, verbose_name="Uwagi")

    class Meta:
        db_table = "ui_warehousehallfeature"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["kind", "label"]
        verbose_name = "Element hali"
        verbose_name_plural = "Elementy hali"
        constraints = [
            # Dokładnie jeden właściciel: model (moduł B) XOR layout (moduł A).
            models.CheckConstraint(
                check=(models.Q(model__isnull=False, layout__isnull=True)
                       | models.Q(model__isnull=True, layout__isnull=False)),
                name="hallfeature_one_owner",
            ),
        ]

    def __str__(self):
        return self.label or self.get_kind_display()


# ─── Picker Activity Heatmap ──────────────────────────────────────────────────



class PickerActivityBatch(models.Model):
    name = models.CharField(max_length=200, verbose_name="Nazwa importu")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    row_count = models.IntegerField(default=0)
    date_from = models.DateField(null=True, blank=True)
    date_to   = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "ui_pickeractivitybatch"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["-uploaded_at"]
        verbose_name = "Partia aktywności pickerów"

    def __str__(self):
        return f"{self.name} ({self.row_count} wierszy)"



class PickerActivity(models.Model):
    batch         = models.ForeignKey(PickerActivityBatch, on_delete=models.CASCADE, related_name="activities")
    location_code = models.CharField(max_length=50, db_index=True)
    confirmed_at  = models.DateTimeField(db_index=True)
    picker_name   = models.CharField(max_length=100, blank=True, default="")
    task_type     = models.CharField(max_length=50,  blank=True, default="")
    # Detal pobrania z eksportu SAP — źródło podpowiedzi „w jakiej jednostce pobrano" na
    # ekranie kontroli HU (join po indeksie+partii do HandlingUnitItem). Puste dla starych
    # importów heatmapy, które niosły tylko lokalizację+czas.
    material_code = models.CharField(max_length=50, blank=True, default="", db_index=True,
                                     verbose_name="Materiał / indeks")
    qty           = models.FloatField(null=True, blank=True, verbose_name="Ilość")
    unit          = models.CharField(max_length=20, blank=True, default="", verbose_name="JM")
    lot           = models.CharField(max_length=32, blank=True, default="", verbose_name="Partia / seria")

    class Meta:
        db_table = "ui_pickeractivity"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        indexes = [
            models.Index(fields=["batch", "confirmed_at"]),
            models.Index(fields=["batch", "location_code"]),
            models.Index(fields=["material_code", "lot"]),
        ]


# ─── Projektowanie wariantów ──────────────────────────────────────────────────


class WarehouseDesignVariant(models.Model):
    """Wariant projektu magazynu: elementy z katalogu `wh3d.design_catalog` (regały,
    VNA, shuttle, AMR, przenośniki…) — z pliku Blendera (palviz.design-variant) albo
    z modelu obecnego magazynu. Wskaźniki GROOVE liczy sam z elementów (nie ufa
    podsumowaniu z pliku); `kpi` to ich zapis z chwili importu."""
    SOURCE_CHOICES = [("blender", "Blender (zestaw projektowy)"), ("model", "Model magazynu")]
    name = models.CharField(max_length=200, verbose_name="Nazwa wariantu")
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default="blender",
                              verbose_name="Źródło")
    base_model = models.ForeignKey(WarehouseModel, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="design_variants", verbose_name="Model bazowy")
    floor_width_m = models.FloatField(default=50, verbose_name="Szerokość hali [m]")
    floor_depth_m = models.FloatField(default=30, verbose_name="Głębokość hali [m]")
    elements = models.JSONField(default=list, verbose_name="Elementy")
    features = models.JSONField(default=list, verbose_name="Elementy hali (doki, strefy)")
    kpi = models.JSONField(default=dict, verbose_name="Wskaźniki")
    notes = models.TextField(blank=True, verbose_name="Uwagi")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Wariant projektu magazynu"
        verbose_name_plural = "Warianty projektu magazynu"

    def __str__(self):
        return self.name


from .models_tasks import *  # noqa: E402,F401,F403  (zadania magazynowe EWM — krok 2)
