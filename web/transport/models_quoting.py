"""Modele domeny Wycena przesyłek (transport) — W2 wydzielenia.

Przeniesione z ui.models przez SeparateDatabaseAndState: TABELE zostają pod
starymi nazwami ui_* (jawne db_table), zmienia się tylko własność w stanie
migracji. ui.models re-eksportuje te nazwy dla kompatybilności importów.
FK do master data (ui.Customer, ui.Product) jako stringi — bez cyklu importu.
"""
import uuid

from django.core.validators import MinValueValidator
from django.db import models


def _quote_token():
    return uuid.uuid4().hex


class Carrier(models.Model):
    CARRIER_TYPE = [
        ("courier", "Kurier (paczki)"),
        ("ltl",     "Drobnica LTL"),
        ("ftl",     "Całopojazdowy FTL"),
    ]
    name = models.CharField(max_length=100, verbose_name="Nazwa przewoźnika")
    carrier_type = models.CharField(max_length=20, choices=CARRIER_TYPE, default="ltl", verbose_name="Typ")
    dim_weight_divisor = models.FloatField(default=5000, validators=[MinValueValidator(1)], verbose_name="Dzielnik wagi obj. [cm³/kg]")
    is_active = models.BooleanField(default=True, verbose_name="Aktywny")
    notes = models.TextField(blank=True, verbose_name="Uwagi")

    class Meta:
        db_table = "ui_carrier"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["carrier_type", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_carrier_type_display()})"


class QuoteRecipient(models.Model):
    """Predefined address list for transport quote requests and the warehouse mailbox.

    `is_warehouse` rows are NOT e-mailed for forwarder quotes — they are the
    warehouse readiness mailbox(es)."""
    name = models.CharField(max_length=120, verbose_name="Nazwa / firma")
    email = models.EmailField(verbose_name="E-mail")
    is_active = models.BooleanField(default=True, verbose_name="Aktywny")
    is_warehouse = models.BooleanField(default=False, verbose_name="Magazyn")
    notes = models.CharField(max_length=200, blank=True, verbose_name="Uwagi")

    class Meta:
        db_table = "ui_quoterecipient"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} <{self.email}>"


class ShipmentQuoteOffer(models.Model):
    """A forwarder's quote reply for a shipment, filled via a tokenised public form.

    One per (shipment, recipient); the link is e-mailed to the forwarder, who enters
    the price + truck/delivery dates — pulled back and shown on the shipment."""
    shipment = models.ForeignKey("Shipment", on_delete=models.CASCADE, related_name="quote_offers")
    recipient = models.ForeignKey(QuoteRecipient, on_delete=models.SET_NULL, null=True, blank=True)
    carrier_name = models.CharField(max_length=120, blank=True, verbose_name="Spedycja")
    token = models.CharField(max_length=32, unique=True, default=_quote_token, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Kwota")
    currency = models.CharField(max_length=3, default="PLN", verbose_name="Waluta")
    # Dopłaty (paliwowa % + stała: ADR, winda, itp.) doliczane do kwoty bazowej, by
    # porównanie ofert odzwierciedlało realny koszt.
    fuel_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                   verbose_name="Dopłata paliwowa [%]")
    surcharge_fixed = models.DecimalField(max_digits=10, decimal_places=2, default=0,
                                          verbose_name="Dopłata stała")
    surcharge_note = models.CharField(max_length=200, blank=True, verbose_name="Opis dopłat")
    truck_date = models.DateField(null=True, blank=True, verbose_name="Podstawienie auta")
    delivery_date = models.DateField(null=True, blank=True, verbose_name="Data dostawy")
    notes = models.CharField(max_length=300, blank=True, verbose_name="Uwagi spedycji")
    attachment = models.FileField(upload_to="quotes/%Y/%m/", null=True, blank=True,
                                  verbose_name="Załącznik (oferta/dokument)")
    submitted_at = models.DateTimeField(null=True, blank=True)
    selected = models.BooleanField(default=False, verbose_name="Wybrana")
    sender_name = models.CharField(max_length=120, blank=True)
    sender_email = models.CharField(max_length=190, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ui_shipmentquoteoffer"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["amount"]
        unique_together = [("shipment", "recipient")]

    def __str__(self):
        return f"{self.carrier_name}: {self.amount} {self.currency}"

    @property
    def display_name(self):
        """Forwarder label that is safe when the recipient row was deleted (SET_NULL)."""
        return self.carrier_name or (self.recipient.name if self.recipient else "—")

    @property
    def total_amount(self):
        """Kwota bazowa + dopłata paliwowa (%) + dopłata stała. None gdy brak kwoty."""
        if self.amount is None:
            return None
        from decimal import Decimal
        base = self.amount
        fuel = base * (self.fuel_pct or 0) / Decimal(100)
        return base + fuel + (self.surcharge_fixed or 0)

    @property
    def has_surcharge(self):
        return bool((self.fuel_pct or 0) or (self.surcharge_fixed or 0))


class DriverAssignment(models.Model):
    """Driver data for the chosen forwarder + the driver's pickup confirmation.

    The forwarder fills the driver data via `form_token`; the driver confirms the
    pickup via `confirm_token` (link sent by SMS)."""
    PICKUP = [("pending", "Oczekuje"), ("confirmed", "Potwierdzony"), ("declined", "Odmowa")]
    shipment = models.OneToOneField("Shipment", on_delete=models.CASCADE, related_name="driver")
    offer = models.ForeignKey(ShipmentQuoteOffer, on_delete=models.SET_NULL, null=True, blank=True)
    form_token = models.CharField(max_length=32, unique=True, default=_quote_token, db_index=True)
    confirm_token = models.CharField(max_length=32, unique=True, default=_quote_token, db_index=True)
    driver_name = models.CharField(max_length=120, blank=True, verbose_name="Kierowca")
    driver_plate = models.CharField(max_length=20, blank=True, verbose_name="Nr auta")
    driver_phone = models.CharField(max_length=32, blank=True, verbose_name="Telefon kierowcy")
    driver_language = models.CharField(max_length=20, default="pl", verbose_name="Język komunikacji")
    truck_eta = models.DateTimeField(null=True, blank=True, verbose_name="Szac. godz. podstawienia auta")
    filled_at = models.DateTimeField(null=True, blank=True)
    pickup_status = models.CharField(max_length=10, choices=PICKUP, default="pending")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    sms_count = models.PositiveIntegerField(default=0)
    sms_last_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Kierowca {self.driver_plate or '—'} ({self.get_pickup_status_display()})"
    class Meta:
        db_table = "ui_driverassignment"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)



class WarehouseReadiness(models.Model):
    """Warehouse readiness confirmation for a shipment, answered via a token link.

    Two per shipment: 'pre' = fast (15 min) "can we prepare this at all?" asked around
    quote time; 'date' = "can we prepare it for the agreed pickup date?" after a
    forwarder is chosen."""
    KIND = [("pre", "Gotowość wstępna (15 min)"), ("date", "Gotowość na datę odbioru")]
    STATUS = [("pending", "Oczekuje"), ("yes", "Potwierdzone"), ("no", "Odmowa")]
    shipment = models.ForeignKey("Shipment", on_delete=models.CASCADE, related_name="wh_confirmations")
    kind = models.CharField(max_length=8, choices=KIND)
    token = models.CharField(max_length=32, unique=True, default=_quote_token, db_index=True)
    status = models.CharField(max_length=8, choices=STATUS, default="pending")
    pickup_date = models.DateField(null=True, blank=True, verbose_name="Data odbioru")
    requested_at = models.DateTimeField(null=True, blank=True)
    deadline = models.DateTimeField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    # Pallet-count negotiation: how many pallets we asked the warehouse to prepare,
    # how many it confirms it can, and (if it can't match) the count it proposes
    # instead — which we then re-visualise and send to the forwarder for quoting.
    asked_pallets = models.PositiveIntegerField(null=True, blank=True,
                                                verbose_name="Palet w zapytaniu")
    confirmed_pallets = models.PositiveIntegerField(null=True, blank=True,
                                                    verbose_name="Palet potwierdzonych")
    suggested_pallets = models.PositiveIntegerField(null=True, blank=True,
                                                    verbose_name="Palet proponowanych przez magazyn")
    # Fastest moment the warehouse can have the goods ready (drives the pickup schedule).
    ready_at = models.DateTimeField(null=True, blank=True,
                                    verbose_name="Najszybszy termin gotowości")

    class Meta:
        db_table = "ui_warehousereadiness"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["kind"]
        unique_together = [("shipment", "kind")]

    def __str__(self):
        return f"{self.shipment_id} {self.kind}: {self.status}"

    @property
    def overdue(self):
        from django.utils import timezone
        return bool(self.status == "pending" and self.deadline and timezone.now() > self.deadline)


class CarrierZone(models.Model):
    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="zones")
    name = models.CharField(max_length=100, verbose_name="Strefa / kraj")
    countries = models.CharField(max_length=500, blank=True, verbose_name="Kody krajów ISO (przecinek)")

    class Meta:
        db_table = "ui_carrierzone"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["name"]

    def __str__(self):
        return f"{self.carrier.name} — {self.name}"


class CarrierRate(models.Model):
    zone = models.ForeignKey(CarrierZone, on_delete=models.CASCADE, related_name="rates")
    weight_from_kg = models.DecimalField(max_digits=10, decimal_places=3, default=0, verbose_name="Waga od [kg]")
    weight_to_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True, verbose_name="Waga do [kg] (puste = bez limitu)")
    price_eur = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Cena [EUR]")
    per_pallet_eur = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, verbose_name="Dopłata/paleta [EUR]")
    fuel_surcharge_pct = models.DecimalField(max_digits=6, decimal_places=3, default=0, verbose_name="Dopłata paliwowa [%]")
    notes = models.CharField(max_length=200, blank=True, verbose_name="Uwagi")

    class Meta:
        db_table = "ui_carrierrate"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["weight_from_kg"]

__all__ = [n for n in list(globals().keys()) if not n.startswith('__')]
