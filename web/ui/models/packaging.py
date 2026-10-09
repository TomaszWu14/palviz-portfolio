import uuid
from django.core.validators import FileExtensionValidator
from django.db import models
from simple_history.models import HistoricalRecords
from .catalog import Product

class InnerPack(models.Model):
    name = models.CharField(max_length=250, verbose_name="Nazwa opakowania zbiorczego")
    ean = models.CharField(max_length=30, blank=True, db_index=True, verbose_name="EAN opak. zbiorczego")
    length_cm = models.FloatField(verbose_name="L [cm]")
    width_cm = models.FloatField(verbose_name="W [cm]")
    height_cm = models.FloatField(verbose_name="H [cm]")
    units_per_pack = models.IntegerField(null=True, blank=True, verbose_name="Szt / opakowanie (opcjonalnie)")
    tare_kg = models.FloatField(default=0.0, verbose_name="Tara opakowania [kg]")
    # Sales unit (opakowanie handlowe) — the retail package inside this delivery unit
    sales_unit_l_cm = models.FloatField(null=True, blank=True, verbose_name="L opak. handlowego [cm]")
    sales_unit_w_cm = models.FloatField(null=True, blank=True, verbose_name="W opak. handlowego [cm]")
    sales_unit_h_cm = models.FloatField(null=True, blank=True, verbose_name="H opak. handlowego [cm]")
    sales_units_per_pack = models.IntegerField(default=1, verbose_name="Opak. handlowych / zbiorcze")
    sales_unit_ean = models.CharField(max_length=30, blank=True, db_index=True, verbose_name="EAN opak. handlowego")
    notes = models.TextField(blank=True, verbose_name="Uwagi")
    is_active = models.BooleanField(default=True, verbose_name="Aktywne")
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        verbose_name = "Opakowanie zbiorcze"
        verbose_name_plural = "Opakowania zbiorcze"
        ordering = ["name"]

    def __str__(self):
        szt = f", {self.units_per_pack} szt" if self.units_per_pack else ""
        return f"{self.name} ({self.length_cm}×{self.width_cm}×{self.height_cm} cm{szt})"

    @property
    def volume_m3(self):
        return round(self.length_cm * self.width_cm * self.height_cm / 1_000_000, 5)


class Carton(models.Model):
    name = models.CharField(max_length=250, verbose_name="Nazwa opakowania")
    ean = models.CharField(max_length=30, blank=True, db_index=True, verbose_name="EAN opakowania")
    length_cm = models.IntegerField(verbose_name="L [cm]")
    width_cm = models.IntegerField(verbose_name="W [cm]")
    height_cm = models.IntegerField(verbose_name="H [cm]")
    unit_weight_kg = models.FloatField(verbose_name="Waga jedn. [kg]")
    pieces_per_carton = models.IntegerField(default=1, verbose_name="Szt / karton")
    tare_kg = models.FloatField(default=0.0, verbose_name="Tara kartonu [kg]")
    inner_pack = models.ForeignKey(
        InnerPack, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="cartons", verbose_name="Opakowanie zbiorcze")
    packs_per_carton = models.IntegerField(null=True, blank=True, verbose_name="Opakowań w kartonie")
    notes = models.TextField(blank=True, verbose_name="Uwagi")
    layout_design = models.JSONField(null=True, blank=True, verbose_name="Zaprojektowany układ na palecie")
    # Realny model 3D (glTF binarny) zastępujący generowaną bryłę tego kartonu w widoku
    # hierarchii (palviz-three.js) — opcjonalny; brak pliku = bryła generowana jak dotąd.
    glb_model = models.FileField(upload_to="carton_models/", blank=True, null=True,
                                 validators=[FileExtensionValidator(["glb"])],
                                 verbose_name="Model 3D (.glb)")
    is_active = models.BooleanField(default=True, verbose_name="Aktywny")
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        verbose_name = "Karton"
        verbose_name_plural = "Kartony"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.length_cm}×{self.width_cm}×{self.height_cm} cm)"

    @property
    def carton_weight_kg(self):
        return round(self.unit_weight_kg * self.pieces_per_carton + self.tare_kg, 3)


def artwork_upload_to(instance, filename):
    """Ścieżka pliku grafiki opakowania — z losowym prefiksem, więc UNIKALNA.

    To nie kosmetyka: `media_serve` serwuje `*_artwork/` z `Cache-Control: immutable`,
    co jest poprawne tylko wtedy, gdy dany URL nigdy nie zmienia treści. Bez prefiksu
    podmiana nadruku (kasujemy stary plik, potem zapisujemy nowy) odzyskiwała dokładnie
    tę samą ścieżkę — przeglądarka trzymała starą grafikę nawet rok. Losowy prefiks
    daje ten inwariant z definicji, niezależnie od kolejności operacji i od tego, kto
    wgrywa plik (widok, admin, import)."""
    import os
    return f"{getattr(instance, '_art_dir', 'artwork')}/{uuid.uuid4().hex[:12]}_{os.path.basename(filename)}"


def _artwork_as_dict(art):
    """Kontrakt danych grafiki dla frontu (edytor 2D + renderer 3D) — wspólny dla
    kartonu, sztuki i OPZ, żeby nie rozjechał się między poziomami.

    Warianty pliku: `url` = display (mniejszy, do ekranu), `thumb` = miniatura (kafle
    PHV / małe canvasy), `full` = oryginał (druk / pobranie). Derywaty są opcjonalne —
    dla starych rekordów i małych plików wszystkie trzy wskazują na oryginał, więc
    `url` NIGDY nie jest puste, gdy grafika ma plik."""
    full = art.image.url if art.image else ""
    display = art.image_display.url if art.image_display else full
    thumb = art.image_thumb.url if art.image_thumb else display
    return {
        "id": art.id, "face": art.face, "kind": art.kind,
        "url": display, "thumb": thumb, "full": full,
        "name": art.name,
        "x": art.x_pct, "y": art.y_pct, "w": art.w_pct, "h": art.h_pct,
        "rot": art.rotation_deg, "z": art.z,
        "w_px": art.width_px, "h_px": art.height_px,
    }


class CartonArtwork(models.Model):
    """Graphic applied to a carton face: either a full-face print background or a
    freely-placed label/sticker. Placement is stored as percentages of the face so
    it stays correct regardless of render size."""
    FACE_CHOICES = [
        ("front",  "Front"),
        ("back",   "Tył"),
        ("left",   "Lewy bok"),
        ("right",  "Prawy bok"),
        ("top",    "Góra"),
        ("bottom", "Dół"),
    ]
    KIND_CHOICES = [
        ("print", "Nadruk ściany"),
        ("label", "Etykieta / naklejka"),
    ]
    _art_dir = "carton_artwork"
    carton = models.ForeignKey(Carton, on_delete=models.CASCADE, related_name="artworks")
    face = models.CharField(max_length=10, choices=FACE_CHOICES, default="front")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="label")
    image = models.ImageField(upload_to=artwork_upload_to, verbose_name="Grafika")
    # Derywaty (WEBP) generowane przy uploadzie — front pobiera je zamiast oryginału.
    # Puste = plik był już mały albo nie dał się zdekodować → fallback na `image`.
    image_display = models.ImageField(upload_to="carton_artwork/derived/", blank=True,
                                      verbose_name="Grafika (ekran)")
    image_thumb = models.ImageField(upload_to="carton_artwork/derived/", blank=True,
                                    verbose_name="Grafika (miniatura)")
    width_px = models.IntegerField(default=0, verbose_name="Szerokość oryginału [px]")
    height_px = models.IntegerField(default=0, verbose_name="Wysokość oryginału [px]")
    name = models.CharField(max_length=120, blank=True, verbose_name="Nazwa")
    # placement as % of the face (0..100); prints fill the whole face
    x_pct = models.FloatField(default=8)
    y_pct = models.FloatField(default=8)
    w_pct = models.FloatField(default=30)
    h_pct = models.FloatField(default=20)
    rotation_deg = models.FloatField(default=0)
    z = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["face", "z", "id"]
        verbose_name = "Grafika kartonu"
        verbose_name_plural = "Grafiki kartonu"

    def as_dict(self):
        return _artwork_as_dict(self)


class _PackagingArtworkBase(models.Model):
    """Wspólna baza grafik opakowania dla poziomów innych niż karton (sztuka, OPZ):
    nadruk ściany / etykieta z rozmieszczeniem w % — renderer 3D nakłada je na bryłę
    tak samo jak CartonArtwork (ten sam kontrakt as_dict)."""
    FACE_CHOICES = CartonArtwork.FACE_CHOICES
    KIND_CHOICES = CartonArtwork.KIND_CHOICES
    face = models.CharField(max_length=10, choices=FACE_CHOICES, default="front")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="label")
    name = models.CharField(max_length=120, blank=True, verbose_name="Nazwa")
    x_pct = models.FloatField(default=8)
    y_pct = models.FloatField(default=8)
    w_pct = models.FloatField(default=30)
    h_pct = models.FloatField(default=20)
    rotation_deg = models.FloatField(default=0)
    z = models.IntegerField(default=0)
    # Wymiary oryginału (0 = nieznane, np. rekord sprzed derywatów albo plik nie do odczytu).
    # Same pliki derywatów siedzą w podklasach — różnią się `upload_to`, tak jak `image`.
    width_px = models.IntegerField(default=0, verbose_name="Szerokość oryginału [px]")
    height_px = models.IntegerField(default=0, verbose_name="Wysokość oryginału [px]")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["face", "z", "id"]

    def as_dict(self):
        return _artwork_as_dict(self)


class ProductArtwork(_PackagingArtworkBase):
    """Grafika opakowania SZTUKI (Product) — poziom „sztuka / OP" w hierarchii 3D."""
    _art_dir = "product_artwork"
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="artworks")
    image = models.ImageField(upload_to=artwork_upload_to, verbose_name="Grafika")
    image_display = models.ImageField(upload_to="product_artwork/derived/", blank=True,
                                      verbose_name="Grafika (ekran)")
    image_thumb = models.ImageField(upload_to="product_artwork/derived/", blank=True,
                                    verbose_name="Grafika (miniatura)")

    class Meta(_PackagingArtworkBase.Meta):
        abstract = False
        ordering = ["face", "z", "id"]
        verbose_name = "Grafika sztuki"
        verbose_name_plural = "Grafiki sztuki"


class InnerPackArtwork(_PackagingArtworkBase):
    """Grafika opakowania ZBIORCZEGO (InnerPack / OPZ) — poziom „OPZ" w hierarchii 3D."""
    _art_dir = "inner_pack_artwork"
    inner_pack = models.ForeignKey(InnerPack, on_delete=models.CASCADE, related_name="artworks")
    image = models.ImageField(upload_to=artwork_upload_to, verbose_name="Grafika")
    image_display = models.ImageField(upload_to="inner_pack_artwork/derived/", blank=True,
                                      verbose_name="Grafika (ekran)")
    image_thumb = models.ImageField(upload_to="inner_pack_artwork/derived/", blank=True,
                                    verbose_name="Grafika (miniatura)")

    class Meta(_PackagingArtworkBase.Meta):
        abstract = False
        ordering = ["face", "z", "id"]
        verbose_name = "Grafika OPZ"
        verbose_name_plural = "Grafiki OPZ"


class PalletizationInstruction(models.Model):
    PALLET_CHOICES = [
        ("EU", "Euro (120×80 cm)"),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="instructions", verbose_name="Produkt")
    version = models.PositiveIntegerField(default=1, verbose_name="Wersja")
    # E2: wariant paletyzacji — A (domyślny) lub B (alternatywny). Obowiązujący
    # wybiera Product.active_variant; latest_instruction filtruje po nim.
    variant = models.CharField(max_length=1, choices=[("A", "A"), ("B", "B")],
                               default="A", db_index=True, verbose_name="Wariant paletyzacji")
    name = models.CharField(max_length=250, blank=True, verbose_name="Nazwa wersji")
    carton = models.ForeignKey(Carton, on_delete=models.SET_NULL, null=True, blank=True, related_name="instructions", verbose_name="Karton z bazy")

    # Pallet config
    pallet_code = models.CharField(max_length=20, choices=PALLET_CHOICES, default="EU", verbose_name="Typ palety")
    pallet_length_cm = models.IntegerField(default=120)
    pallet_width_cm = models.IntegerField(default=80)
    max_height_total_cm = models.IntegerField(default=215, verbose_name="Wys. max total [cm]")
    pallet_base_height_cm = models.IntegerField(default=15)
    max_weight_kg = models.IntegerField(default=1000, verbose_name="Max waga [kg]")

    # Inner pack (opakowanie zbiorcze) — assigned at instruction level, product-specific
    inner_pack = models.ForeignKey(
        "InnerPack", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="instructions", verbose_name="Opakowanie zbiorcze")
    pcs_per_inner_pack = models.IntegerField(null=True, blank=True, verbose_name="Szt / opakowanie zbiorcze")
    packs_per_carton = models.IntegerField(null=True, blank=True, verbose_name="Opakowań zbiorczych / karton")

    # Carton dimensions (may mirror carton FK or be standalone)
    carton_l = models.IntegerField(verbose_name="L kartonu [cm]")
    carton_w = models.IntegerField(verbose_name="W kartonu [cm]")
    carton_h = models.IntegerField(verbose_name="H kartonu [cm]")
    unit_weight = models.FloatField(verbose_name="Waga jedn. [kg]")
    pcs_per_carton = models.IntegerField(default=1, verbose_name="Szt / karton")
    carton_tare = models.FloatField(default=0.0, verbose_name="Tara kartonu [kg]")
    # Objętość jednej sztuki/OP wprost z SAP MARM (kolumna Objętość). Gdy podana, wyliczenia
    # objętości używają jej zamiast przeliczać z L×W×H kartonu (karton bywa większy niż
    # OP×liczba, więc przeliczanie zawyżało). None = brak danych → fallback na wymiary.
    unit_volume_m3 = models.FloatField(null=True, blank=True,
                                       verbose_name="Objętość 1 szt/OP [m³] (z MARM)")
    demand_pcs = models.IntegerField(default=1000, verbose_name="Popyt [szt]")
    # Ile sztuk użytkowych (JU) mieści jednostka konsumpcyjna liczona w kartonie/palecie.
    # Z SAP MARM: Mianownik/Licznik wiersza JU (np. 100 JU = 1 jedn. bazowa). 1 = brak podziału.
    units_per_piece = models.PositiveIntegerField(
        default=1, verbose_name="Szt użytkowych (JU) / jednostkę")

    # Computed
    layouts = models.JSONField(default=list)
    selected_layout = models.CharField(max_length=120, blank=True, verbose_name="Wybrany layout")
    custom_layers = models.JSONField(
        default=list,
        verbose_name="Warstwy niestandardowe",
        help_text="Lista warstw z ręcznie ułożonymi kartonami"
    )

    # Gdy ktoś ręcznie ułożył `custom_layers`, a potem zmieniono wymiary/wejścia i układ
    # przeliczono — ręczny układ ZOSTAJE (nie kasujemy po cichu), ale oznaczamy go jako
    # potencjalnie nieaktualny (plakietka w UI). Czyszczone przy ponownym zapisie edytora.
    custom_layout_stale = models.BooleanField(
        default=False, verbose_name="Ręczny układ może być nieaktualny")

    is_active = models.BooleanField(default=True, verbose_name="Aktywna")
    notes = models.TextField(blank=True, verbose_name="Uwagi dla magazyniera")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords(excluded_fields=["layouts", "updated_at"])

    class Meta:
        verbose_name = "Instrukcja paletyzacji"
        verbose_name_plural = "Instrukcje paletyzacji"
        unique_together = [("product", "version")]
        ordering = ["product", "version"]

    def __str__(self):
        label = self.name or f"v{self.version}"
        return f"{self.product.code} — {label}"

    def has_custom_layout(self):
        """Czy istnieje ręcznie ułożony układ (edytor warstw)."""
        return bool(self.custom_layers)

    def _custom_as_layout(self):
        """Adapter: `custom_layers` (warstwy z {x,y,w,d,h,orient}) → dict układu w schemacie,
        który czytają WSZYSCY konsumenci get_selected_layout (hierarchia, 3D detalu, wycena).
        Placement niesie OBA schematy naraz: x/y + dx/dy (palviz-three.js) i w/d/h/orient
        (edytor/plotly) + 1-indeksowany `layer` (renderer per-warstwa)."""
        layers = self.custom_layers or []
        placements = []
        per_layer_counts = []
        for li, layer in enumerate(layers):
            lp = layer.get("placements") or []
            per_layer_counts.append(len(lp))
            for p in lp:
                w = p.get("w") if p.get("w") is not None else p.get("dx", 0)
                d = p.get("d") if p.get("d") is not None else p.get("dy", 0)
                placements.append({
                    "x": p.get("x", 0), "y": p.get("y", 0),
                    "dx": w, "dy": d,
                    "w": w, "d": d, "h": p.get("h", 0),
                    "orient": p.get("orient"),
                    "rotated": bool(p.get("rotated", False)),
                    "layer": li + 1,
                })
        if not placements:
            return None
        return {
            "name": "custom",
            "source": "custom",
            "cartons_per_layer": max(per_layer_counts) if per_layer_counts else 0,
            "layers_used": len(layers),
            "cartons_per_pallet": len(placements),
            "placements": placements,
            "utilization_percent": None,
            "cog": None,
            "stale": bool(self.custom_layout_stale),
        }

    def get_selected_layout(self):
        # Ręczny układ (edytor warstw) MA PIERWSZEŃSTWO — jest tym, co użytkownik naprawił;
        # dzięki temu edycja układu jest widoczna wszędzie (hierarchia/3D/wycena), a ilości
        # (cartons_per_pallet) liczą się z realnie ułożonych kartonów.
        if self.custom_layers:
            custom = self._custom_as_layout()
            if custom:
                return custom
        if not self.layouts:
            return None
        if self.selected_layout:
            hit = next((l for l in self.layouts if l.get("name") == self.selected_layout), None)
            if hit:
                return hit
        return self.layouts[0]

    def get_engine_layout(self):
        """Wybrany układ SILNIKA, ignorując ręczny (do porównania / przywrócenia)."""
        if not self.layouts:
            return None
        if self.selected_layout:
            hit = next((l for l in self.layouts if l.get("name") == self.selected_layout), None)
            if hit:
                return hit
        return self.layouts[0]

    def get_meta(self):
        return {
            "pallet_code": self.pallet_code,
            "length_cm": self.pallet_length_cm,
            "width_cm": self.pallet_width_cm,
            "cargo_max_height_cm": max(0, self.max_height_total_cm - self.pallet_base_height_cm),
            "max_height_total_cm": self.max_height_total_cm,
            "base_height_cm": self.pallet_base_height_cm,
            "max_weight_kg": self.max_weight_kg,
        }


class ErrorReport(models.Model):
    STATUS_NEW = "new"
    STATUS_REVIEW = "in_review"
    STATUS_RESOLVED = "resolved"
    STATUS_CHOICES = [
        ("new",       "Nowe"),
        ("in_review", "W trakcie"),
        ("resolved",  "Rozwiązane"),
    ]

    instruction = models.ForeignKey(
        PalletizationInstruction, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="reports",
        verbose_name="Instrukcja"
    )
    product_code = models.CharField(max_length=100, blank=True, verbose_name="Kod produktu")
    reporter_name = models.CharField(max_length=100, blank=True, verbose_name="Imię/nick")
    reporter_location = models.CharField(max_length=100, blank=True, verbose_name="Lokalizacja (magazyn/stanowisko)")
    description = models.TextField(verbose_name="Opis błędu")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="new", verbose_name="Status")
    admin_notes = models.TextField(blank=True, verbose_name="Notatki admina")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Zgłoszenie błędu"
        verbose_name_plural = "Zgłoszenia błędów"
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.id} {self.product_code} — {self.get_status_display()}"


