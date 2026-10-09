from django.core.validators import FileExtensionValidator
from django.db import models
from simple_history.models import HistoricalRecords
from .catalog import Product

class PackagingIssue(models.Model):
    """Zgłoszenie błędu master daty z modułu „Hierarchia opakowań" (PHV): pracownik
    magazynu widzi hierarchię szt→OPZ→KAR→PAL i zgłasza brak/błąd przelicznika, nazwy
    lub daty — z fotodokumentacją. Powiadomienie mailowe idzie do opiekuna master daty
    (PHV_ISSUE_EMAIL); statusy open→in_review→resolved śledzi zgłaszający („Moje
    zgłoszenia" / po #ID)."""
    TYPES = [
        ("missing_conversion", "Brak przelicznika"),
        ("wrong_conversion", "Błędny przelicznik"),
        ("wrong_name", "Błędna nazwa REF"),
        ("wrong_date", "Błędna data (MFG/ważność/seria)"),
        # Zastąpiony w pickerze przez „Zmień lokalizację"; wartość zostaje dla starych wierszy.
        ("carton_too_heavy", "Karton za ciężki"),
        ("change_location", "Zmień lokalizację"),
        ("carton_underfilled", "Karton niewypełniony (do optymalizacji)"),
        ("carton_fit_pallet", "Dopasować karton do wymiarów palety"),
        # Indeks nie ma przypisanego procesu magazynowego (fix / near to bin / antresola)
        # — zgłaszający sugeruje jeden z trzech w `correct_value`.
        ("missing_process", "Brak procesu magazynowego"),
        # Jednostka bez renderu/zdjęcia w bazie — operator dosyła fotodokumentację.
        ("missing_render", "Brak renderu 3D — zdjęcie jednostki"),
        # B6: proces zmiany paletyzacji (powód ze słownika w correct_value, E1)
        # i weryfikacja objętości — standardowy pipeline (mail + reports_admin).
        ("change_palletization", "Zmień sposób paletyzacji"),
        ("verify_volume", "Zweryfikuj objętość produktu"),
        # E3: rozbieżność deklarowanego packspec z master datą (MARM/instrukcja).
        ("packspec_mismatch", "Rozbieżność packspec / MARM"),
        ("other", "Inne / uwaga"),
    ]
    STATUS = [("open", "Otwarte"), ("in_review", "W przeglądzie"), ("resolved", "Rozwiązane")]
    ref_code = models.CharField(max_length=50, db_index=True, verbose_name="REF / indeks")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    issue_type = models.CharField(max_length=24, choices=TYPES, verbose_name="Typ zgłoszenia")
    description = models.TextField(max_length=500, blank=True, verbose_name="Opis")
    current_value = models.CharField(max_length=80, blank=True, verbose_name="Wartość aktualna")
    correct_value = models.CharField(max_length=80, blank=True, verbose_name="Wartość poprawna")
    photo = models.ImageField(upload_to="phv/%Y/%m/", null=True, blank=True, verbose_name="Zdjęcie")
    reporter = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True,
                                 related_name="packaging_issues")
    assigned_to = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="claimed_packaging_issues", verbose_name="Przyjęte przez")
    assigned_at = models.DateTimeField(null=True, blank=True, verbose_name="Przyjęto")
    status = models.CharField(max_length=12, choices=STATUS, default="open", db_index=True)
    resolver_notes = models.CharField(max_length=300, blank=True, verbose_name="Notatka rozwiązania")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Zgłoszenie hierarchii opakowań"
        verbose_name_plural = "Zgłoszenia hierarchii opakowań"

    def __str__(self):
        return f"#{self.pk} {self.ref_code} · {self.get_issue_type_display()} ({self.status})"


class OptimizationConfig(models.Model):
    """Globalna konfiguracja modułu „Optymalizacja kartonów" — jeden wiersz (load()).
    Próg wypełnienia palety, poniżej którego materiał trafia na dashboard pilności."""
    min_fill_pct = models.PositiveSmallIntegerField(
        default=70, verbose_name="Próg dobrego wypełnienia palety [%]",
        help_text="Materiały z wypełnieniem poniżej tej wartości są sygnalizowane jako do optymalizacji (1–100).")
    suggest_vol_down_pct = models.PositiveSmallIntegerField(
        default=20, verbose_name="Auto-sugestia: dopuszczalne zmniejszenie objętości [%]",
        help_text="O ile mniejszy karton wolno zaproponować niż obecny (0–90).")
    suggest_vol_up_pct = models.PositiveSmallIntegerField(
        default=20, verbose_name="Auto-sugestia: dopuszczalne zwiększenie objętości [%]",
        help_text="O ile większy karton wolno zaproponować niż obecny (0–100).")
    pallets_per_truck = models.PositiveSmallIntegerField(
        default=33, verbose_name="Palet na auto [szt]",
        help_text="Pojemność auta do KPI aut/rok (1–66). Standard FTL: 33 palety EU.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Konfiguracja optymalizacji"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"Optymalizacja: próg {self.min_fill_pct}%"

    @classmethod
    def load(cls):
        return cls.objects.first() or cls.objects.create()


class CartonAlternative(models.Model):
    """Kandydat na wymiary kartonu dla materiału — do porównania wypełnienia palety.
    Materiał może mieć kilka wariantów (zmiany raz lepsze, raz gorsze); historia zmian
    wymiarów jest śledzona (HistoricalRecords), żeby było widać, jak ewoluowało wypełnienie.
    To brudnopis optymalizacji — NIE nadpisuje instrukcji paletyzacji (master data)."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE,
                                related_name="carton_alternatives", verbose_name="Materiał")
    label = models.CharField(max_length=80, verbose_name="Nazwa wariantu")
    length_cm = models.IntegerField(verbose_name="L kartonu [cm]")
    width_cm = models.IntegerField(verbose_name="W kartonu [cm]")
    height_cm = models.IntegerField(verbose_name="H kartonu [cm]")
    is_active = models.BooleanField(default=True, verbose_name="Aktywny")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Wymiary niższych poziomów hierarchii dla kaskady B/C (null = bez zmian, z bazy).
    unit_l_cm = models.IntegerField(null=True, blank=True, verbose_name="L sztuki [cm]")
    unit_w_cm = models.IntegerField(null=True, blank=True, verbose_name="W sztuki [cm]")
    unit_h_cm = models.IntegerField(null=True, blank=True, verbose_name="H sztuki [cm]")
    pack_l_cm = models.IntegerField(null=True, blank=True, verbose_name="L OPZ [cm]")
    pack_w_cm = models.IntegerField(null=True, blank=True, verbose_name="W OPZ [cm]")
    pack_h_cm = models.IntegerField(null=True, blank=True, verbose_name="H OPZ [cm]")
    slot = models.CharField(max_length=1, blank=True, default="",
                            choices=[("", "—"), ("B", "B"), ("C", "C")],
                            verbose_name="Slot porównania")
    history = HistoricalRecords(excluded_fields=["updated_at"])

    class Meta:
        ordering = ["product", "label"]
        verbose_name = "Wariant kartonu (optymalizacja)"
        verbose_name_plural = "Warianty kartonów (optymalizacja)"

    def __str__(self):
        return f"{self.product.code} · {self.label} ({self.length_cm}×{self.width_cm}×{self.height_cm})"


class CartonPromotion(models.Model):
    """Audyt promocji: wariant/sugestia kartonu przeniesiony do instrukcji paletyzacji
    (nowa wersja). Kto, kiedy, jakie wymiary, wypełnienie przed→po, z jakiego zgłoszenia.
    simple_history na instrukcji loguje surową zmianę; tu jest czytelny ślad workflow
    optymalizacji z powiązaniem do zgłoszenia (PackagingIssue)."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE,
                                related_name="carton_promotions", verbose_name="Materiał")
    version = models.PositiveIntegerField(verbose_name="Wersja instrukcji (wynikowa)")
    dims = models.CharField(max_length=40, verbose_name="Wymiary kartonu")
    source_label = models.CharField(max_length=80, blank=True, verbose_name="Źródło (wariant)")
    fill_before = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Wypełnienie przed [%]")
    fill_after = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Wypełnienie po [%]")
    issue = models.ForeignKey("PackagingIssue", on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="promotions", verbose_name="Ze zgłoszenia")
    user = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="carton_promotions", verbose_name="Wykonał")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Promocja kartonu (historia)"
        verbose_name_plural = "Promocje kartonów (historia)"

    def __str__(self):
        return f"{self.product.code} v{self.version} · {self.dims}"

    @property
    def delta_pp(self):
        if self.fill_before is not None and self.fill_after is not None:
            return self.fill_after - self.fill_before
        return None


class PackagingRedesign(models.Model):
    """Projekt A/B przeprojektowania opakowania (moduł carton_opt): A = zamrożony
    snapshot stanu obecnego (wymiary + render wgrany przez użytkownika), B = docelowe
    wymiary projektowane ręcznie. Wynik = dokumentacja/porównanie — master data
    nie jest zmieniana automatycznie (wdrożenie fizyczne to osobny, ręczny krok)."""
    SCOPE = [("op", "Opakowanie (OP)"), ("karton", "Karton"), ("oba", "OP + karton")]
    STATUS = [("draft", "Szkic"), ("accepted", "Zaakceptowany"), ("rejected", "Odrzucony")]
    product = models.ForeignKey(Product, on_delete=models.CASCADE,
                                related_name="redesigns", verbose_name="Indeks")
    scope = models.CharField(max_length=8, choices=SCOPE, verbose_name="Zakres")
    # Wersja A — snapshot master daty z chwili utworzenia; NIGDY nie czytana na żywo.
    a_snapshot = models.JSONField(verbose_name="Wersja A (snapshot)")
    a_render = models.FileField(upload_to="redesigns/", blank=True, null=True,
                                validators=[FileExtensionValidator(["glb", "png", "jpg", "jpeg"])],
                                verbose_name="Render wersji A")
    # Wersja B — edytowalna do akceptacji; tylko pola objęte zakresem.
    b_op_l = models.FloatField(null=True, blank=True, verbose_name="B: L opakowania [cm]")
    b_op_w = models.FloatField(null=True, blank=True, verbose_name="B: W opakowania [cm]")
    b_op_h = models.FloatField(null=True, blank=True, verbose_name="B: H opakowania [cm]")
    b_carton_l = models.FloatField(null=True, blank=True, verbose_name="B: L kartonu [cm]")
    b_carton_w = models.FloatField(null=True, blank=True, verbose_name="B: W kartonu [cm]")
    b_carton_h = models.FloatField(null=True, blank=True, verbose_name="B: H kartonu [cm]")
    b_pcs_per_carton = models.PositiveIntegerField(null=True, blank=True,
                                                   verbose_name="B: szt/karton")
    b_units_per_pack = models.PositiveIntegerField(null=True, blank=True,
                                                   verbose_name="B: szt/OP")
    annual_volume_pcs = models.PositiveIntegerField(null=True, blank=True,
                                                    verbose_name="Wolumen roczny [szt]")
    status = models.CharField(max_length=10, choices=STATUS, default="draft",
                              db_index=True, verbose_name="Status")
    notes = models.TextField(max_length=1000, blank=True, verbose_name="Notatki")
    created_by = models.ForeignKey("auth.User", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="redesigns")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Projekt A/B opakowania"
        verbose_name_plural = "Projekty A/B opakowań"
        # Jeden indeks = jeden projekt. Nie wolno mieć kilku wątków do tego samego REF
        # (warianty docelowe B/C/… żyją WEWNĄTRZ projektu, nie jako osobne projekty).
        constraints = [
            models.UniqueConstraint(fields=["product"], name="uniq_redesign_per_product"),
        ]

    def __str__(self):
        return f"A/B {self.product.code} ({self.get_scope_display()}, {self.status})"

    @property
    def a(self):
        return self.a_snapshot or {}

    @classmethod
    def create_for(cls, product, scope, user):
        """Załóż projekt: snapshot A z bieżącej master daty (jedyny moment odczytu)."""
        instr = product.latest_instruction()
        snap = {
            "unit_l": product.unit_length_cm, "unit_w": product.unit_width_cm,
            "unit_h": product.unit_height_cm,
            "carton_l": getattr(instr, "carton_l", None),
            "carton_w": getattr(instr, "carton_w", None),
            "carton_h": getattr(instr, "carton_h", None),
            "pcs_per_carton": getattr(instr, "pcs_per_carton", None),
            "units_per_piece": getattr(instr, "units_per_piece", None),
        }
        return cls.objects.create(product=product, scope=scope,
                                  a_snapshot=snap, created_by=user)


