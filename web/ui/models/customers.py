from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from .catalog import Product

class Customer(models.Model):
    """Customer / consignee (klient, odbiorca) with per-customer delivery requirements.

    A Shipment may link to one Customer; its requirements (max pallet height, fumigated
    pallet, pallet type, weight cap) then drive the calculator defaults and are surfaced
    as a requirements banner on the shipment so nothing client-specific gets missed."""
    PALLET_ANY = ""
    PALLET_TYPES = [
        (PALLET_ANY,   "Dowolna"),
        ("euro",       "Euro 120×80"),
        ("industrial", "Przemysłowa 120×100"),
        ("disposable", "Jednorazowa"),
    ]
    name = models.CharField(max_length=200, verbose_name="Nazwa / firma")
    code = models.CharField(max_length=40, blank=True, db_index=True,
                            verbose_name="Kod klienta")
    kind = models.CharField(max_length=10, default="customer", verbose_name="Typ",
                            choices=[("customer", "Klient"), ("consignee", "Odbiorca")])
    # VIP (roadmapa HU): sortowanie/grupowanie list HU po typie klienta.
    is_vip = models.BooleanField(default=False, verbose_name="Klient VIP")
    # Kategoria handlowa (VIP / Delta / Beta / Export…) — szersza niż samo is_vip;
    # steruje wydrukami (projekt etykiet) i widokami list. VIP w kategorii ustawia też is_vip.
    CATEGORIES = [("", "Standard"), ("vip", "VIP"), ("delta", "Delta"),
                  ("beta", "Beta"), ("export", "Export")]
    category = models.CharField(max_length=20, blank=True, default="", choices=CATEGORIES,
                                verbose_name="Kategoria klienta")
    # Przewoźnik domyślny dla wysyłek tego odbiorcy (np. „GLS"). Puste = dowolny/inny.
    carrier = models.CharField(max_length=40, blank=True, default="",
                               verbose_name="Przewoźnik", help_text="np. „GLS”; puste = inny/dowolny")
    priority_rank = models.PositiveSmallIntegerField(
        default=0, verbose_name="Ranga priorytetu",
        help_text="Wyższa = wcześniej w kolejce kontroli (tie-break po VIP).")
    # Kod SAP (KUNNR) — twarde dopasowanie dostaw z feedu HU do klienta
    # (porównywane po zdjęciu zer wiodących z obu stron).
    kunnr = models.CharField(max_length=20, blank=True, db_index=True,
                             verbose_name="Kod SAP (KUNNR)")
    country = models.CharField(max_length=2, blank=True, verbose_name="Kraj (ISO)")
    city = models.CharField(max_length=100, blank=True, verbose_name="Miasto")
    postal = models.CharField(max_length=20, blank=True, verbose_name="Kod pocztowy")
    street = models.CharField(max_length=200, blank=True, verbose_name="Ulica")
    phone = models.CharField(max_length=40, blank=True, verbose_name="Telefon")
    contact_email = models.EmailField(blank=True, verbose_name="E-mail kontaktowy")
    # --- Per-customer delivery requirements ---
    max_pallet_height_cm = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(50), MaxValueValidator(280)],
        verbose_name="Maks. wysokość palety [cm]",
        help_text="Jeśli ustawione, kalkulator nie zaproponuje wyższej palety dla tego klienta.")
    max_pallet_weight_kg = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Maks. waga palety [kg]")
    requires_fumigated_pallet = models.BooleanField(
        default=False, verbose_name="Wymaga palety fumigowanej (IPPC/ISPM 15)")
    pallet_type = models.CharField(max_length=12, blank=True, choices=PALLET_TYPES,
                                   default=PALLET_ANY, verbose_name="Wymagany typ palety")
    delivery_hours = models.CharField(max_length=120, blank=True, verbose_name="Okna dostaw",
                                      help_text="np. „pn–pt 08:00–14:00, awizacja 24 h”.")
    requires_adr = models.BooleanField(default=False, verbose_name="Wymaga ADR")
    temp_control = models.CharField(max_length=40, blank=True, verbose_name="Wymagana temperatura",
                                    help_text="np. „+2…+8°C” — puste = brak wymogu chłodni.")
    wz_copies = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(9)],
        verbose_name="Liczba kopii WZ",
        help_text="Ile wydrukowanych kopii dokumentu WZ wymaga klient (np. 2, 3, 4).")
    # Minimalna reszta ważności przy dostawie (miesiące). Puste = domyślne 6 mies.
    # (huctl.rules). Pozycja krótkodatowa wymaga świadomego potwierdzenia (F6/F7).
    min_shelf_life_months = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(60)],
        verbose_name="Min. ważność przy dostawie [mies.]",
        help_text="Poniżej tej reszty ważności pozycja wymaga potwierdzenia przy kontroli.")
    requirements_notes = models.TextField(blank=True, verbose_name="Dodatkowe wymagania")
    # ── Etykieta logistyczna (roadmapa, mini-wywiad 2026-07-30) ──────────────────
    LABEL_LANGS = [("pl", "Polski"), ("en", "English"), ("de", "Deutsch")]
    requires_logistics_label = models.BooleanField(
        default=False, verbose_name="Wymaga etykiety logistycznej",
        help_text="Po kontroli HU kontroler może wydrukować etykietę z zawartością palety.")
    label_language = models.CharField(max_length=2, choices=LABEL_LANGS, default="pl",
                                      verbose_name="Język etykiety")
    label_extra_text = models.CharField(max_length=200, blank=True,
        verbose_name="Stały tekst na etykiecie", help_text="Np. numer umowy — drukowany na każdej etykiecie tego klienta.")
    label_show_lot_exp = models.BooleanField(default=True, verbose_name="Etykieta: LOT i termin ważności")
    label_show_requirements = models.BooleanField(default=True, verbose_name="Etykieta: notatki wymagań")
    is_active = models.BooleanField(default=True, verbose_name="Aktywny")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})" if self.code else self.name

    def requirement_summary(self):
        """Human-readable list of this customer's delivery requirements — surfaced on the
        shipment and carried into the warehouse request + forwarder quote so nothing
        client-specific is missed."""
        out = []
        if self.max_pallet_height_cm:
            out.append(f"maks. wysokość palety {self.max_pallet_height_cm} cm")
        if self.max_pallet_weight_kg:
            out.append(f"maks. waga palety {self.max_pallet_weight_kg} kg")
        if self.pallet_type:
            out.append(f"paleta: {self.get_pallet_type_display()}")
        if self.requires_fumigated_pallet:
            out.append("paleta fumigowana (IPPC/ISPM 15)")
        if self.requires_adr:
            out.append("ADR")
        if self.temp_control:
            out.append(f"temperatura {self.temp_control}")
        if self.wz_copies:
            out.append(f"WZ w {self.wz_copies} kopiach")
        if self.delivery_hours:
            out.append(f"okna dostaw: {self.delivery_hours}")
        if self.requirements_notes:
            out.append(self.requirements_notes.strip())
        return out

    def has_requirements(self):
        return bool(self.max_pallet_height_cm or self.max_pallet_weight_kg
                    or self.requires_fumigated_pallet or self.pallet_type
                    or self.requires_adr or self.temp_control or self.delivery_hours
                    or self.wz_copies or self.requirements_notes)


class CustomerPackagingRule(models.Model):
    """Wymaganie pakowania per klient × indeks — np. CTND-200_V1 dla klienta X ma iść
    w opakowaniach po 25 szt zamiast luzem w kartonie po 250. Reguła informacyjno-
    kontrolna: karta klienta (edycja Master Data) + plakietka przy pozycji w kontroli HU.
    # ponytail: bez automatycznego przeliczania paletyzacji — dołożyć, gdy reguł
    # będzie więcej i pojawi się realny wzorzec liczenia."""
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE,
                                 related_name="packaging_rules", verbose_name="Klient")
    product = models.ForeignKey(Product, on_delete=models.CASCADE,
                                related_name="customer_packaging_rules",
                                verbose_name="Indeks")
    units_per_pack = models.PositiveIntegerField(verbose_name="Szt / opakowanie")
    note = models.CharField(max_length=200, blank=True, verbose_name="Uwagi")
    # Alert mailowy: gdy pojawi się dostawa/zamówienie tego klienta z tym indeksem
    # w ilości > units_per_pack — mail „przygotuj specjalne opakowania" (numer
    # dostawy + dane). Puste = bez maila (sama plakietka w kontroli HU).
    alert_email = models.EmailField(blank=True, verbose_name="Alert e-mail")
    is_active = models.BooleanField(default=True, verbose_name="Aktywna")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("customer", "product")]
        verbose_name = "Reguła pakowania klienta"
        verbose_name_plural = "Reguły pakowania klienta"

    def __str__(self):
        return f"{self.customer} · {self.product.code}: {self.units_per_pack} szt/opak."


class UkraineOrderLine(models.Model):
    """Jedna wskazana partia do wysyłki dla klienta z Ukrainy (moduł „Wysyłka UKRAINA").

    Klient odgórnie wskazuje konkretny indeks + partię (LOT) + żądaną ilość; ilości rosną/
    zmieniają się w czasie. Stan „ile gotowe" NIE jest tu przechowywany — liczymy go w locie z
    istniejącego stocku (HandlingUnitItem po ref_code+lot, kubełkowany po warehouse_type na
    ACME/DLT). `index_code` to trwały klucz łączenia ze stockiem (= HandlingUnitItem.ref_code),
    niezależny od tego, czy produkt jest w bazie."""
    STATUS = [("open", "Otwarte"), ("closed", "Zamknięte")]
    customer    = models.CharField(max_length=120, verbose_name="Klient (Ukraina)")
    order_ref   = models.CharField(max_length=60, blank=True, verbose_name="Nr zlecenia")
    product     = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    index_code  = models.CharField(max_length=50, db_index=True, verbose_name="Indeks")
    lot         = models.CharField(max_length=32, verbose_name="Partia (LOT)")
    requested_qty = models.FloatField(default=0, verbose_name="Żądana ilość")
    unit        = models.CharField(max_length=20, default="szt", verbose_name="Jednostka")
    status      = models.CharField(max_length=8, choices=STATUS, default="open", verbose_name="Status")
    note        = models.CharField(max_length=300, blank=True, verbose_name="Uwagi")
    created_by  = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="ukraine_lines")
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["customer", "order_ref", "index_code", "lot"]
        verbose_name = "Linia wysyłki UKRAINA"
        verbose_name_plural = "Linie wysyłki UKRAINA"
        indexes = [models.Index(fields=["index_code", "lot"])]
        # Klucz upsertu przy imporcie: ta sama (klient, zlecenie, indeks, partia) → aktualizacja.
        constraints = [
            models.UniqueConstraint(fields=["customer", "order_ref", "index_code", "lot"],
                                    name="ukr_line_uniq"),
        ]

    def __str__(self):
        return f"{self.customer} · {self.index_code}/{self.lot} × {self.requested_qty:g}"


