import uuid
from django.core.validators import FileExtensionValidator
from django.db import models
from simple_history.models import HistoricalRecords

def _quote_token():
    return uuid.uuid4().hex


# ─── Legacy models (kept for backward compatibility) ──────────────────────────

class Batch(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    name = models.CharField(max_length=250, blank=True)
    pallet_code = models.CharField(max_length=20, default="EU")
    pallet_length_cm = models.IntegerField(default=120)
    pallet_width_cm = models.IntegerField(default=80)
    max_height_total_cm = models.IntegerField(default=215)
    pallet_base_height_cm = models.IntegerField(default=15)
    max_weight_kg = models.IntegerField(default=1000)

    def __str__(self):
        return f"{self.name or 'Batch'} ({self.created_at:%Y-%m-%d %H:%M})"


class Palletization(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="items", null=True, blank=True)
    sku = models.CharField(max_length=80)
    variant = models.CharField(max_length=80, default="STD")
    carton_l = models.IntegerField()
    carton_w = models.IntegerField()
    carton_h = models.IntegerField()
    unit_weight = models.FloatField()
    pcs_per_carton = models.IntegerField()
    demand_pcs = models.IntegerField()
    carton_tare = models.FloatField(default=0.0)
    layouts = models.JSONField(default=list)
    selected_layout = models.CharField(max_length=120, blank=True)


# ─── New models ───────────────────────────────────────────────────────────────

CATEGORY_COLORS = [
    ("#3b82f6", "Niebieski"), ("#10b981", "Zielony"), ("#f59e0b", "Żółty"),
    ("#ef4444", "Czerwony"), ("#8b5cf6", "Fioletowy"), ("#f97316", "Pomarańczowy"),
    ("#06b6d4", "Cyjan"), ("#ec4899", "Różowy"), ("#6b7280", "Szary"), ("#14b8a6", "Turkusowy"),
]

class ProductCategory(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name="Nazwa kategorii")
    code = models.CharField(max_length=20, unique=True, verbose_name="Kod kategorii", help_text="Krótki identyfikator np. KUBKI")
    color = models.CharField(max_length=7, default="#3b82f6", verbose_name="Kolor", help_text="Hex np. #3b82f6")
    description = models.TextField(blank=True, verbose_name="Opis")
    # Które poziomy hierarchii opakowań istnieją dla produktów tej kategorii (CSV kluczy:
    # pallet,carton,inner_pack,sales_unit,unit,ju). Puste = wszystkie policzalne poziomy.
    # Poziom wymagany tutaj, ale niepoliczalny z master daty → czerwony alert
    # „Brak przelicznika" w hierarchii (desktop i skaner/PHV). Patrz ui/hierarchy.py.
    hierarchy_levels = models.CharField(
        max_length=80, blank=True, default="", verbose_name="Poziomy hierarchii (CSV)",
        help_text="np. „pallet,carton,unit”; puste = pełny szablon")

    class Meta:
        verbose_name = "Kategoria produktu"
        verbose_name_plural = "Kategorie produktów"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):
    code = models.CharField(max_length=100, unique=True, db_index=True, verbose_name="Kod indeksu")
    name = models.CharField(max_length=250, verbose_name="Nazwa")
    description = models.TextField(blank=True, verbose_name="Opis")
    ean = models.CharField(max_length=30, blank=True, db_index=True, verbose_name="EAN / barcode")
    supplier_short = models.CharField(max_length=50, blank=True, verbose_name="Dostawca (skrót)")
    category = models.ForeignKey(
        ProductCategory, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="products", verbose_name="Kategoria"
    )
    unit_length_cm = models.FloatField(null=True, blank=True, verbose_name="L sztuki [cm]")
    unit_width_cm = models.FloatField(null=True, blank=True, verbose_name="W sztuki [cm]")
    unit_height_cm = models.FloatField(null=True, blank=True, verbose_name="H sztuki [cm]")
    # Stackability constraints — feed the pallet builder so fragile/non-stackable goods
    # are not stacked beyond what's physically allowed (drives a realistic pallet count).
    stackable = models.BooleanField(default=True, verbose_name="Można piętrować")
    max_stack_layers = models.PositiveSmallIntegerField(
        default=0, verbose_name="Maks. warstw na palecie (0 = bez limitu)")
    # Realny model 3D (.glb) poziomu OP/sztuka — zastępuje generowaną bryłę w hierarchii
    # (jak Carton.glb_model dla kartonu); brak pliku = bryła generowana jak dotąd.
    glb_model = models.FileField(upload_to="product_models/", blank=True, null=True,
                                 validators=[FileExtensionValidator(["glb"])],
                                 verbose_name="Model 3D (.glb)")
    # Media poziomu SZTUKA (JU) — osobne od poziomu OP (glb_model/artworks):
    # realny model .glb i/lub zdjęcie (nadruk na bryłę) jednostki użytkowej.
    ju_glb_model = models.FileField(upload_to="product_models/", blank=True, null=True,
                                    validators=[FileExtensionValidator(["glb"])],
                                    verbose_name="Model 3D sztuki JU (.glb)")
    ju_image = models.FileField(upload_to="product_art/", blank=True, null=True,
                                validators=[FileExtensionValidator(["png", "jpg", "jpeg"])],
                                verbose_name="Zdjęcie sztuki JU (PNG/JPG)")
    is_active = models.BooleanField(default=True, verbose_name="Aktywny")
    # E2: obowiązujący wariant paletyzacji (A domyślnie; B = alternatywny zestaw
    # instrukcji). Przełączenie logowane przez HistoricalRecords (kto/kiedy).
    active_variant = models.CharField(max_length=1, choices=[("A", "A"), ("B", "B")],
                                      default="A", verbose_name="Obowiązujący wariant paletyzacji")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    class Meta:
        verbose_name = "Produkt"
        verbose_name_plural = "Produkty"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    def latest_instruction(self):
        """Obowiązująca instrukcja: najwyższa aktywna wersja W OBOWIĄZUJĄCYM wariancie
        (E2). Fallback na dowolny wariant, gdy aktywny nie ma instrukcji (bezpiecznik —
        przełączenie na pusty wariant nie może zgasić karty produktu)."""
        # Użyj cache prefetcha jeśli caller zrobił prefetch_related("instructions") — inaczej
        # .filter() omija cache i daje N+1 na listach (data_center woła to 2×N produktów).
        av = self.active_variant or "A"
        if "instructions" in getattr(self, "_prefetched_objects_cache", {}):
            actives = [i for i in self.instructions.all() if i.is_active]
            in_variant = [i for i in actives if i.variant == av]
            return max(in_variant or actives, key=lambda i: i.version, default=None)
        return (self.instructions.filter(is_active=True, variant=av).order_by("-version").first()
                or self.instructions.filter(is_active=True).order_by("-version").first())

    def active_instructions(self):
        return self.instructions.filter(is_active=True).order_by("version")

    def has_variant_b(self):
        """Czy istnieje alternatywny wariant paletyzacji (aktywna instrukcja w wariancie
        innym niż obowiązujący) — do plakietki „istnieje wariant alternatywny"."""
        av = self.active_variant or "A"
        return self.instructions.filter(is_active=True).exclude(variant=av).exists()


class MaterialReference(models.Model):
    """Reference data imported from SAP MARM export — used to validate product codes."""
    code = models.CharField(max_length=100, unique=True, db_index=True, verbose_name="Kod materiału")
    name = models.CharField(max_length=250, blank=True, verbose_name="Nazwa")
    supplier_short = models.CharField(max_length=50, blank=True, verbose_name="Skrót dostawcy")
    supplier_full = models.CharField(max_length=500, blank=True, verbose_name="Pełna nazwa dostawcy")
    width_cm = models.FloatField(null=True, blank=True, verbose_name="Szerokość [cm]")
    height_cm = models.FloatField(null=True, blank=True, verbose_name="Wysokość [cm]")
    length_cm = models.FloatField(null=True, blank=True, verbose_name="Długość [cm]")
    gross_kg = models.FloatField(null=True, blank=True, verbose_name="Waga brutto [kg]")
    pieces_per_carton = models.IntegerField(null=True, blank=True, verbose_name="Szt/karton")
    ean = models.CharField(max_length=30, blank=True, verbose_name="EAN")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Materiał referencyjny"
        verbose_name_plural = "Materiały referencyjne"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class DictionaryEntry(models.Model):
    """Słownik pojęć Data Center: kod → opis, w kategoriach. Katalog referencyjny
    (tylko do odczytu w UI). Pierwsza kategoria: 'process_type' — rodzaje procesów
    magazynowych z SAP (działania w magazynie), seedowane migracją."""
    CATEGORY = [
        ("process_type", "Rodzaj procesu"),
    ]
    category = models.CharField(max_length=32, choices=CATEGORY, db_index=True,
                                verbose_name="Kategoria")
    code = models.CharField(max_length=20, verbose_name="Kod")
    label = models.CharField(max_length=200, verbose_name="Opis")
    group = models.CharField(max_length=60, blank=True, verbose_name="Grupa")

    class Meta:
        verbose_name = "Pojęcie słownika"
        verbose_name_plural = "Słownik pojęć"
        ordering = ["category", "group", "code"]
        constraints = [
            models.UniqueConstraint(fields=["category", "code"], name="dict_cat_code_uniq"),
        ]

    def __str__(self):
        return f"{self.code} — {self.label}"


class ProductAlias(models.Model):
    """Stary / alternatywny kod produktu → kanoniczny Product.

    Dla REALNYCH zmian nazwy, których nie da się wyliczyć normalizacją (np.
    „DMO-M-100" → „DMOM10001" — różnią się nawet cyframi). Warianty mechaniczne
    (wielkość liter, separatory, sufiksy _v1/_b1, prefiksy dostawcy jak „nieat-")
    obsługuje `product_codes.normalize_code`, więc NIE trzymamy ich tutaj —
    tabela jest tylko na mapowania, których reguła nie odgadnie."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="aliases",
                                verbose_name="Produkt kanoniczny")
    alias_code = models.CharField(max_length=100, unique=True, db_index=True,
                                  verbose_name="Stary / alternatywny kod")
    note = models.CharField(max_length=200, blank=True, verbose_name="Uwaga")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Alias produktu"
        verbose_name_plural = "Aliasy produktów"
        ordering = ["alias_code"]

    def __str__(self):
        return f"{self.alias_code} → {self.product.code}"


class FixLocation(models.Model):
    """Stała lokalizacja pickingowa (fix) z SAP: przypisanie REF → lokalizacja z ilością
    minimalną i maksymalną. Źródło „Zapas / min" na karcie produktu (min = poziom, poniżej
    którego trzeba uzupełnić fix). Import z eksportu SAP (Miejsce składowania / Produkt /
    Ilość minimalna / Maksym. ilość)."""
    location_code = models.CharField(max_length=40, db_index=True, verbose_name="Miejsce składowania")
    ref_code = models.CharField(max_length=50, db_index=True, verbose_name="REF / produkt")
    warehouse_type = models.CharField(max_length=10, blank=True, default="", verbose_name="Typ magazynu")
    max_qty = models.FloatField(default=0, verbose_name="Ilość maksymalna")
    min_qty = models.FloatField(default=0, verbose_name="Ilość minimalna")
    uom = models.CharField(max_length=10, blank=True, default="", verbose_name="JM")
    changed_at = models.DateField(null=True, blank=True, verbose_name="Data zmiany")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("location_code", "ref_code")]
        ordering = ["ref_code", "location_code"]
        verbose_name = "Lokalizacja fix"
        verbose_name_plural = "Lokalizacje fix"

    def __str__(self):
        return f"{self.location_code} — {self.ref_code} (min {self.min_qty})"


