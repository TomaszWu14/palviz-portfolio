"""Modele domeny Kontrola HU (huctl) — W2 wydzielenia.

Przeniesione z ui.models przez SeparateDatabaseAndState: TABELE zostają pod
starymi nazwami ui_* (jawne db_table), zmienia się tylko własność w stanie
migracji. ui.models re-eksportuje te nazwy dla kompatybilności importów.
FK do master/transport (transport.Shipment, ui.Product) jako stringi — bez cyklu importu.
"""
from django.db import models


class HandlingUnit(models.Model):
    """A physical handling unit (pallet) of a shipment, with its EXPECTED contents.

    PalViz fills the expected contents from the palletization; the in-app HU control
    transaction lets a controller count each position and flag discrepancies. A HU in
    an error state is BLOCKED (no warehouse-task suspension here — out of scope)."""
    STATUS = [
        ("planned",    "Zaplanowany"),
        ("in_control", "W kontroli"),
        ("ok",         "Zgodny"),
        ("to_recheck", "Do rekontroli"),
        ("escaped",    "Wyjechało bez kontroli"),   # terminal: booked out before control (F2)
    ]
    shipment = models.ForeignKey("transport.Shipment", on_delete=models.CASCADE, related_name="handling_units")
    seq = models.PositiveIntegerField(default=1, verbose_name="Nr palety")
    # pickHU/SSCC — etykieta FIZYCZNEJ palety, więc globalnie unikalna (nie per dostawa).
    # Skaner rozwiązuje kod bez kontekstu dostawy, więc dwa wiersze o tym samym kodzie
    # oznaczałyby, że skan trafia w losową paletę — pilnuje tego hu_code_uniq poniżej.
    code = models.CharField(max_length=64, blank=True, db_index=True, verbose_name="pickHU")
    status = models.CharField(max_length=12, choices=STATUS, default="planned", verbose_name="Status")
    location = models.CharField(max_length=40, blank=True, verbose_name="Lokalizacja")
    recipient_type = models.CharField(max_length=40, blank=True, verbose_name="Typ odbiorcy")
    warehouse_type = models.CharField(max_length=40, blank=True, verbose_name="Typ magazynu")
    picker = models.CharField(max_length=80, blank=True, db_index=True, verbose_name="Picker (kompletujący)")
    # Control bookkeeping
    controlled_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="controlled_hus")
    # Ręczny przydział lidera (roadmapa Q8) — "Następna HU" preferuje przydzielone.
    assigned_to = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="assigned_hus", verbose_name="Przydzielona do")
    # Rezerwacja kolejki wywołań (roadmapa HU) — kto/kiedy wywołał, i odłożenie na później.
    called_at = models.DateTimeField(null=True, blank=True, verbose_name="Wywołana o")
    snooze_until = models.DateTimeField(null=True, blank=True, verbose_name="Odłożona do")
    verified_at = models.DateTimeField(null=True, blank=True)
    is_priority = models.BooleanField(default=False, db_index=True, verbose_name="Priorytet (rekontrola)")
    # Controller's explicit acknowledgement of the customer's special delivery
    # requirements (fumigation, ADR, temperature, pallet limits…). Required before the
    # HU can be posted whenever the linked customer/shipment carries such requirements.
    client_reqs_confirmed_at = models.DateTimeField(null=True, blank=True,
                                                    verbose_name="Wymagania klienta potwierdzone")
    client_reqs_confirmed_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True,
                                                 blank=True, related_name="hu_reqs_confirmed")
    # Controller's explicit acknowledgement of short-dated positions (below the customer's /
    # global minimum shelf life) — required before posting when the HU carries such items (F7).
    short_dated_ack_at = models.DateTimeField(null=True, blank=True,
                                              verbose_name="Krótki termin potwierdzony")
    short_dated_ack_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True,
                                           blank=True, related_name="hu_short_dated_ack")
    # Status kompletacji z feedu SAP (nowa kolumna) — podział list na
    # skompletowane / nieskompletowane, niezależny od statusu KONTROLI.
    is_completed = models.BooleanField(default=False, db_index=True,
                                       verbose_name="Skompletowana (picking)")
    # Status zapasu z SAP (np. B6/F2/Q4) — informacyjny, prosto z feedu stocku. Puste,
    # dopóki feed nie poda kolumny. Niezależny od statusu KONTROLI (STATUS powyżej).
    stock_status = models.CharField(max_length=12, blank=True, default="",
                                    verbose_name="Status zapasu (SAP)")
    # Wymiary/waga palety z feedu (gdy SAP je poda); braki liczone z master daty.
    weight_kg = models.FloatField(null=True, blank=True, verbose_name="Waga [kg]")
    length_cm = models.FloatField(null=True, blank=True, verbose_name="Długość [cm]")
    width_cm = models.FloatField(null=True, blank=True, verbose_name="Szerokość [cm]")
    height_cm = models.FloatField(null=True, blank=True, verbose_name="Wysokość [cm]")
    # When the current control started (set on planned→in_control / take-over). Drives the
    # leader's live "who is controlling what, and for how long" view.
    control_started_at = models.DateTimeField(null=True, blank=True)
    # Last time this HU appeared in a SAP import (CSV / Power BI). SAP feeds are rolling
    # snapshots, so an uncontrolled HU that stops reappearing was likely booked out before
    # we controlled it — surfaced to the leader so nothing silently escapes control (Q95/96).
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ui_handlingunit"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["shipment", "seq"]
        unique_together = [("shipment", "seq")]
        # Kolejka/panel lidera filtrują status (=/IN), potem warehouse_type__in (DB-003).
        indexes = [models.Index(fields=["status", "warehouse_type"], name="hu_status_whtype_idx")]
        constraints = [
            # Inwariant: HU 'Zgodny' MUSI mieć znacznik weryfikacji (łapie bugowe przejścia).
            models.CheckConstraint(
                check=~models.Q(status="ok") | models.Q(verified_at__isnull=False),
                name="hu_ok_requires_verified_at"),
            # Jeden pickHU = jedna paleta. Warunkowo, bo HU wygenerowane z paletyzacji
            # (jeszcze bez etykiety SAP) mają code="" i takich pustych jest wiele.
            models.UniqueConstraint(
                fields=["code"], condition=~models.Q(code=""), name="hu_code_uniq"),
        ]

    def __str__(self):
        return self.ref

    @property
    def sla(self):
        """Termin wysyłki HU (#18) — SLA(deadline, minutes, level, label). Poziom
        'none'/'ok'/'soon'/'overdue' steruje kolorem karty i sortem kolejki."""
        from .sla import sla_for
        return sla_for(self)

    @property
    def ref(self):
        """Stable identifier — the pickHU code if assigned, else a PalViz code."""
        return self.code or f"{self.shipment_id}-P{self.seq}"

    @property
    def is_blocked(self):
        """Blocked while under control or awaiting re-control."""
        return self.status in ("in_control", "to_recheck")

    def client_requirement_lines(self):
        """Special customer/shipment delivery requirements the controller must confirm
        before posting (empty list when the customer has none)."""
        lines = []
        cust = self.shipment.customer if self.shipment_id else None
        if cust:
            lines.extend(cust.requirement_summary())
        extra = (self.shipment.client_requirements or "").strip() if self.shipment_id else ""
        if extra:
            lines.append(extra)
        return lines

    @property
    def has_client_requirements(self):
        return bool(self.client_requirement_lines())

    def _customer(self):
        return self.shipment.customer if self.shipment_id else None

    def min_shelf_life_months(self):
        """Efektywny próg minimalnej ważności (miesiące): wymóg klienta, else 6 mies.
        (huctl.rules — jedna reguła dla kolejki, księgowania i karty HU, BIZ-007)."""
        from .rules import min_shelf_months
        return min_shelf_months(self._customer())

    def short_dated_items(self, today=None):
        """Pozycje 'krótkodatowe': ważne, ale z resztą ważności poniżej progu (nie w pełni
        przeterminowane — te obsługuje osobna blokada). Reguła: huctl.rules.is_short_dated."""
        from django.utils import timezone
        from .rules import is_short_dated
        today = today or timezone.localdate()
        cust = self._customer()
        return [it for it in self.items.all()
                if it.expiry and today <= it.expiry and is_short_dated(it.expiry, cust, today)]

    @property
    def has_short_dated(self):
        return bool(self.short_dated_items())

    def progress(self):
        """(controlled, total) item counts for the 'spr./suma' header."""
        total = self.items.count()
        done = self.items.filter(controlled=True).count()
        return done, total


# Kody błędów wygaszone soft-delete (poziom modułu — komprehensja w ciele klasy nie widzi
# atrybutów klasy, a dwa literały by się rozjechały). Konsumowane przez HandlingUnitItem.
_INACTIVE_ERROR_FLAGS = {"wrong_label", "missing_document"}


class HandlingUnitItem(models.Model):
    RESULT = [("", "—"), ("ok", "Potwierdzony"), ("error", "Błąd")]
    hu = models.ForeignKey(HandlingUnit, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("ui.Product", on_delete=models.SET_NULL, null=True, blank=True)
    ref_code = models.CharField(max_length=50, verbose_name="Kod / REF")
    description = models.CharField(max_length=120, blank=True, verbose_name="Opis")
    lot = models.CharField(max_length=16, blank=True, verbose_name="LOT producenta")
    expiry = models.DateField(null=True, blank=True, verbose_name="Data ważności")
    # Partia producenta (SAP MCH1) — numer nadany przez wytwórcę, INNY niż nasz LOT;
    # kontroler porównuje go z etykietą na kartonie.
    vendor_batch = models.CharField(max_length=40, blank=True, verbose_name="Partia producenta")
    mfg_date = models.DateField(null=True, blank=True, verbose_name="Data produkcji")
    # Expected quantity in both units — base (JP, e.g. OP) and alternative (AJM, e.g. KAR).
    base_unit = models.CharField(max_length=20, blank=True, verbose_name="Jedn. podstawowa (JP)")
    base_qty = models.FloatField(default=0, verbose_name="Ilość JP")
    alt_unit = models.CharField(max_length=20, default="KAR", verbose_name="Jedn. alternatywna (AJM)")
    alt_qty = models.FloatField(default=0, verbose_name="Ilość AJM")
    # legacy generic fields (kept for the existing API)
    expected_qty = models.FloatField(default=0, verbose_name="Oczekiwana ilość")
    unit = models.CharField(max_length=20, default="kar", verbose_name="Jednostka")
    # Waga pozycji z feedu (SAP BRGEW); brak → liczona z master daty (MARM).
    weight_kg = models.FloatField(null=True, blank=True, verbose_name="Waga [kg]")
    # Wyjątek master daty: pozycja zgłoszona jako niezgodna z master datą — nie blokuje
    # zamknięcia HU; domyka ją Master Data / lider (Fala 4).
    md_exception = models.BooleanField(default=False, verbose_name="Wyjątek master daty")
    md_exception_note = models.CharField(max_length=200, blank=True,
                                         verbose_name="Opis niezgodności master daty")
    # Control result
    controlled = models.BooleanField(default=False, verbose_name="Skontrolowano")
    counted_qty = models.FloatField(null=True, blank=True, verbose_name="Zliczono")
    counted_unit = models.CharField(max_length=20, blank=True)
    result = models.CharField(max_length=8, choices=RESULT, default="", verbose_name="Wynik")
    error_flags = models.JSONField(default=dict, blank=True, verbose_name="Flagi błędów")
    # Obowiązkowe potwierdzenie przy kontroli: partia dostawcy i data ważności z etykiety
    # kartonu muszą być aktywnie potwierdzone, żeby pozycję domknąć „OK" (gdy feed je poda).
    vendor_batch_ok = models.BooleanField(default=False, verbose_name="Partia potwierdzona")
    expiry_ok = models.BooleanField(default=False, verbose_name="Data ważności potwierdzona")
    controlled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ui_handlingunititem"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["id"]

    # Error-flag keys — pełna lista (ŹRÓDŁO ETYKIET dla HISTORII; nie usuwać wpisów).
    # Wygaszanie kodu = dopisz klucz do INACTIVE_FLAGS (soft-delete), NIE kasuj krotki —
    # historyczne error_flags/HUQualityIssue muszą dalej renderować swój label.
    ERROR_FLAGS = [
        ("wrong_assortment", "Błędny asortyment / zły indeks"),
        ("damaged",          "Uszkodzony towar"),
        ("wrong_batch",      "Błędna seria"),
        ("ajm_conversion",   "Błąd przelicznika AJM"),
        ("wrong_expiry",     "Błędna data ważności"),
        ("bad_placement",    "Nieprawidłowe ułożenie"),
        ("wrong_label",      "Błąd na etykiecie"),
        ("missing_document", "Brak dokumentu / certyfikatu"),
    ]
    # Kody wygaszone (soft-delete): nie pokazywane w UI wejścia, ale label zostaje dla historii.
    INACTIVE_FLAGS = _INACTIVE_ERROR_FLAGS
    # Aktywne kody = wejście kontrolera (formularz, parsowanie POST). Historia czyta ERROR_FLAGS.
    ACTIVE_ERROR_FLAGS = [(k, l) for k, l in ERROR_FLAGS if k not in _INACTIVE_ERROR_FLAGS]
    # Quantity-track flags force a re-control (recount); the rest go to the quality
    # track as closable issues without forcing a recount.
    QUANTITY_FLAGS = {"ajm_conversion"}

__all__ = [n for n in list(globals().keys()) if not n.startswith('__')]
