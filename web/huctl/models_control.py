"""Modele domeny Kontrola HU (huctl) — W2 wydzielenia.

Przeniesione z ui.models przez SeparateDatabaseAndState: TABELE zostają pod
starymi nazwami ui_* (jawne db_table), zmienia się tylko własność w stanie
migracji. ui.models re-eksportuje te nazwy dla kompatybilności importów.
FK do master/transport (transport.Shipment, ui.Product) jako stringi — bez cyklu importu.
"""
from django.db import models

from .models_hu import HandlingUnit, HandlingUnitItem  # noqa: F401

class HUQualityIssue(models.Model):
    """A quality discrepancy raised during HU control (damaged, wrong batch/expiry,
    foreign item…). Separate from the quantity track — it does NOT force a recount;
    a controller closes it with a resolution note."""
    TYPES = HandlingUnitItem.ERROR_FLAGS + [("foreign_item", "Obcy / nadmiarowy towar")]
    STATUS = [("open", "Otwarte"), ("closed", "Zamknięte")]
    hu = models.ForeignKey(HandlingUnit, on_delete=models.CASCADE, related_name="quality_issues")
    item = models.ForeignKey(HandlingUnitItem, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="quality_issues")
    issue_type = models.CharField(max_length=24, choices=TYPES, verbose_name="Typ")
    ref_code = models.CharField(max_length=50, blank=True, verbose_name="REF (obcy towar)")
    qty = models.FloatField(null=True, blank=True, verbose_name="Ilość")
    note = models.CharField(max_length=300, blank=True, verbose_name="Opis")
    photo = models.ImageField(upload_to="quality/%Y/%m/", null=True, blank=True, verbose_name="Zdjęcie")
    status = models.CharField(max_length=8, choices=STATUS, default="open")
    raised_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="raised_quality_issues")
    closed_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="closed_quality_issues")
    resolution_note = models.CharField(max_length=300, blank=True, verbose_name="Rozwiązanie")
    raised_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ui_huqualityissue"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["-raised_at"]
        constraints = [
            # Nie nawarstwiaj duplikatów OTWARTYCH issue na tę samą (hu, item, typ).
            # item=NULL (obcy towar) jest w SQL/Postgres distinct → obce pozycje nie kolidują.
            models.UniqueConstraint(
                fields=["hu", "item", "issue_type"], condition=models.Q(status="open"),
                name="huqi_open_dedup"),
        ]

    def __str__(self):
        return f"{self.hu.ref} · {self.get_issue_type_display()} ({self.status})"


class HUControlAttempt(models.Model):
    """Audit record of one position-count confirmation (full history, even after a
    re-control fixes it). Backs the controller KPI: positions, HUs, time per position
    (the gap to the same controller's previous confirmation), quality detection."""
    hu = models.ForeignKey(HandlingUnit, on_delete=models.CASCADE, related_name="control_attempts")
    item = models.ForeignKey(HandlingUnitItem, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="control_attempts")
    controller = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="hu_attempts")
    is_recheck = models.BooleanField(default=False)
    counted_qty = models.FloatField(null=True, blank=True)
    counted_unit = models.CharField(max_length=20, blank=True)
    exp_base_qty = models.FloatField(default=0)
    exp_base_unit = models.CharField(max_length=20, blank=True)
    exp_alt_qty = models.FloatField(default=0)
    exp_alt_unit = models.CharField(max_length=20, blank=True)
    result = models.CharField(max_length=8, blank=True)
    error_flags = models.JSONField(default=dict, blank=True)
    seconds_since_prev = models.FloatField(null=True, blank=True,
                                           verbose_name="Czas od poprzedniej pozycji [s]")
    # Stabilny identyfikator akcji ze skanera (per-pozycja, ten sam przy retry) — dedup
    # przy replayu offline-batcha, żeby nie podwajać KPI. Puste = brak (ścieżka online bez id).
    client_id = models.CharField(max_length=64, blank=True, db_index=True)
    # Warstwa C (detekcja anty-„przeklikanie z biurka"): źródło inputu (scan/keyboard) i
    # identyfikator urządzenia. BSSID/RSSI AP celowo pominięte — PWA/TWA nie ma do nich dostępu.
    input_source = models.CharField(max_length=12, blank=True, default="",
                                    verbose_name="Źródło inputu")   # "scan" | "keyboard" | ""
    device_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "ui_hucontrolattempt"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["client_id"], condition=~models.Q(client_id=""),
                name="huattempt_client_id_uniq"),
        ]

    def __str__(self):
        return f"{self.hu_id}/{self.item_id} {self.result} by {self.controller_id}"


class ControlledWarehouseType(models.Model):
    """Global config (set by leaders on the control hub): which warehouse types are
    currently under HU control. An EMPTY table means *every* type is controlled (no
    filtering) — so newly imported types are controlled by default until a leader
    narrows the scope. One row per controlled type ``code`` (blank = the "no type" group).
    """
    code = models.CharField(max_length=40, unique=True, blank=True, verbose_name="Typ magazynu")

    class Meta:
        db_table = "ui_controlledwarehousetype"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["code"]
        verbose_name = "Kontrolowany typ magazynu"
        verbose_name_plural = "Kontrolowane typy magazynu"

    def __str__(self):
        return self.code or "—"

    @classmethod
    def controlled_codes(cls):
        """Set of controlled type codes, or None when unconfigured (= all controlled)."""
        codes = set(cls.objects.values_list("code", flat=True))
        return codes or None


class HUStatusEvent(models.Model):
    """Audit trail of HU status transitions (who / when / why). The leader can review the
    full history of a handling unit — required for internal + client audit (Q46)."""
    KIND = [("status", "zmiana statusu"), ("call", "wywołanie"), ("release", "zwolnienie")]
    hu = models.ForeignKey(HandlingUnit, on_delete=models.CASCADE, related_name="status_events")
    # Rodzaj zdarzenia (spec wywołań): call/release logowane przy niezmienionym statusie —
    # metryki (czas wywołanie→start, porzucone wywołania) liczą się z tego pola, nie z note.
    kind = models.CharField(max_length=8, choices=KIND, default="status", db_index=True)
    from_status = models.CharField(max_length=12, blank=True, choices=HandlingUnit.STATUS)
    to_status = models.CharField(max_length=12, choices=HandlingUnit.STATUS)
    by_user = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="hu_status_events")
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "ui_hustatusevent"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["-created_at"]
        verbose_name = "Zdarzenie statusu HU"
        verbose_name_plural = "Zdarzenia statusu HU"

    def __str__(self):
        return f"{self.hu_id}: {self.from_status}→{self.to_status}"


class GlsPackingEntry(models.Model):
    """Rozliczenie konsolidacji w strefie GLS (wymóg po zaksięgowaniu palety): ile
    kartonów kontroler miał w przygotowaniu i ile paczek z nich zrobił (np. 5→2, 7→3,
    1→1). Zasila raport wydajności lidera: współczynnik konsolidacji (kartony/paczkę,
    wyżej = lepiej) i objętości paczek. `volume_m3` = snapshot objętości palety
    (hu_metrics) w momencie zapisu — analityka „w jakich objętościach pakujemy"."""
    hu = models.OneToOneField(HandlingUnit, on_delete=models.CASCADE,
                              related_name="gls_packing")
    controller = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True,
                                   related_name="gls_packing_entries")
    cartons = models.PositiveSmallIntegerField(verbose_name="Kartony w przygotowaniu")
    parcels = models.PositiveSmallIntegerField(verbose_name="Zrobione paczki")
    volume_m3 = models.FloatField(null=True, blank=True, verbose_name="Objętość palety [m³]")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "ui_glspackingentry"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["-created_at"]
        verbose_name = "Rozliczenie pakowania GLS"
        verbose_name_plural = "Rozliczenia pakowania GLS"

    def __str__(self):
        return f"{self.hu_id}: {self.cartons}→{self.parcels}"


class EscalationRoute(models.Model):
    """Mapa eskalacji niekompletnych przesyłek (spec wywołań): per typ magazynu, kto dostaje
    Task przy `hu_escalate`. Pusty `warehouse_type` = reguła globalna (fallback). Wzorzec
    ControlledWarehouseType/ControllerZone — mała tabela konfigurowana przez lidera/admina."""
    warehouse_type = models.CharField(max_length=40, blank=True, default="", unique=True,
                                      verbose_name="Typ magazynu (pusty = globalna)")
    picking_leader = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="escalation_picking", verbose_name="Lider pickingu")
    area_leader = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="escalation_area", verbose_name="Lider obszaru")
    shift_manager = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="escalation_shift", verbose_name="Kierownik zmiany")
    escalate_after_minutes = models.PositiveSmallIntegerField(
        default=0, verbose_name="Eskaluj do kierownika po [min]",
        help_text="0 = kierownik zmiany od razu; >0 = dopiero gdy brak reakcji po tym czasie.")

    class Meta:
        db_table = "ui_escalationroute"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["warehouse_type"]
        verbose_name = "Ścieżka eskalacji"
        verbose_name_plural = "Ścieżki eskalacji"

    def __str__(self):
        return self.warehouse_type or "(globalna)"

    @classmethod
    def resolve(cls, warehouse_type):
        """Dokładny typ magazynu → fallback na regułę globalną → None."""
        return (cls.objects.filter(warehouse_type=warehouse_type or "").first()
                or cls.objects.filter(warehouse_type="").first())


class ControllerZone(models.Model):
    """Per-user HU-control permission — a simple *user × warehouse type (zone)* matrix.

    An admin defines which warehouse types (strefy) each controller may control HU in. A
    user with NO rows may control EVERY zone (backward-compatible — existing controllers
    keep working until an admin narrows them). Admins and leaders always bypass this and
    control any zone. One row per allowed ``code`` (blank = the "no type" group)."""
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="control_zones")
    code = models.CharField(max_length=40, blank=True, verbose_name="Typ magazynu")

    class Meta:
        db_table = "ui_controllerzone"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        unique_together = [("user", "code")]
        ordering = ["user_id", "code"]
        verbose_name = "Strefa kontroli użytkownika"
        verbose_name_plural = "Strefy kontroli (użytkownik × typ magazynu)"

    def __str__(self):
        return f"{self.user_id}: {self.code or '—'}"

    @classmethod
    def zones_for(cls, user):
        """Set of warehouse-type codes the user may control, or None = every zone
        (unconfigured user, or an admin/leader who bypasses zone limits)."""
        from ui.roles import GROUP_ADMIN, GROUP_LEADER, has_role
        if not user or not getattr(user, "is_authenticated", False):
            return set()
        if user.is_superuser or has_role(user, GROUP_ADMIN, GROUP_LEADER):
            return None
        codes = set(cls.objects.filter(user=user).values_list("code", flat=True))
        return codes or None

    @classmethod
    def can_control(cls, user, warehouse_type):
        """True if the user may control HU in this warehouse type (zone)."""
        zones = cls.zones_for(user)
        return zones is None or (warehouse_type or "") in zones


class HUControlPhoto(models.Model):
    """Zdjęcie palety zrobione ze skanera przy WEJŚCIU do kontroli HU — dowód fizycznej
    obecności („nie liczę zza biurka"). Wymuszane pod flagą `HU_PHOTO_ENFORCE` TYLKO na
    urządzeniach z aparatem (`UserProfile.has_camera`); Zebra (bez aparatu) jedzie na samym
    skanie HU. Retencja 30 dni (Celery-beat kasuje starsze) — to dowód do spot-audytu lidera,
    nie wieczyste archiwum. Zdjęcie jest kompresowane po stronie skanera przed uploadem."""
    hu = models.ForeignKey("HandlingUnit", on_delete=models.CASCADE, related_name="control_photos")
    user = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                             verbose_name="Kontroler")
    photo = models.ImageField(upload_to="hu_control/%Y/%m/", verbose_name="Zdjęcie")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "ui_hucontrolphoto"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["-created_at"]
        verbose_name = "Zdjęcie kontroli HU"
        verbose_name_plural = "Zdjęcia kontroli HU"

    def __str__(self):
        return f"{self.hu_id} @ {self.created_at:%Y-%m-%d %H:%M}"

class HUErrorInvestigation(models.Model):
    """Wyjaśnianie błędu (spec UX 2026-09-03 §3): po 2. liczeniu potwierdzającym błąd
    kontroler pauzuje licznik kontroli i idzie odkręcić błąd na magazynie. Pauza jest
    WAŻNA dla KPI dopiero po potwierdzeniu przez pickera pozycji albo lidera
    (anty-nadużycie); brak potwierdzenia w HU_INVESTIGATION_CONFIRM_MIN → czas wraca
    do kontroli + flaga w panelu lidera."""
    TYPES = [("missing", "Brak sztuk"), ("excess", "Nadmiar"),
             ("wrong_batch", "Zła partia"), ("wrong_expiry", "Błąd daty ważności"),
             ("bad_placement", "Błędne ułożenie")]
    hu = models.ForeignKey(HandlingUnit, on_delete=models.CASCADE,
                           related_name="investigations")
    item = models.ForeignKey(HandlingUnitItem, on_delete=models.SET_NULL, null=True,
                             blank=True, related_name="investigations")
    controller = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True,
                                   related_name="hu_investigations")
    error_type = models.CharField(max_length=20, choices=TYPES, verbose_name="Typ błędu")
    note = models.CharField(max_length=300, blank=True, verbose_name="Notatka")
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name="confirmed_investigations")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    rejected = models.BooleanField(default=False)   # lider odrzucił → czas wraca do KPI

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Wyjaśnianie błędu HU"
        verbose_name_plural = "Wyjaśniania błędów HU"
        constraints = [
            # Jedno OTWARTE wyjaśnianie per HU — kontroler kończy jedno, zanim zacznie drugie.
            models.UniqueConstraint(fields=["hu"], condition=models.Q(ended_at__isnull=True),
                                    name="one_open_investigation_per_hu"),
        ]

    @property
    def is_open(self):
        return self.ended_at is None

    def duration_s(self):
        from django.utils import timezone
        end = self.ended_at or timezone.now()
        return (end - self.started_at).total_seconds()

    @staticmethod
    def confirm_window_min():
        from django.conf import settings
        return int(getattr(settings, "HU_INVESTIGATION_CONFIRM_MIN", 15))

    def is_overdue(self):
        """Potwierdzenie nie przyszło w oknie od startu (liczone do teraz/confirmed_at)."""
        from datetime import timedelta
        from django.utils import timezone
        ref = self.confirmed_at or timezone.now()
        return (ref - self.started_at) > timedelta(minutes=self.confirm_window_min())

    def kpi_excluded(self):
        """Czy czas wyjaśniania NIE liczy się do czasu kontroli (KPI): potwierdzone,
        nieodrzucone i potwierdzone W OKNIE (spóźniony ack nie ratuje pauzy —
        egzekwuje docstring klasy, nie tylko badge w panelu lidera)."""
        return bool(self.confirmed_at) and not self.rejected and not self.is_overdue()

    def __str__(self):
        return f"{self.hu_id} {self.error_type} @ {self.started_at:%Y-%m-%d %H:%M}"


__all__ = [n for n in list(globals().keys()) if not n.startswith('__')]
