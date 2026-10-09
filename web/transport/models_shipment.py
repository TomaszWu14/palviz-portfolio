"""Modele domeny Wycena przesyłek (transport) — W2 wydzielenia.

Przeniesione z ui.models przez SeparateDatabaseAndState: TABELE zostają pod
starymi nazwami ui_* (jawne db_table), zmienia się tylko własność w stanie
migracji. ui.models re-eksportuje te nazwy dla kompatybilności importów.
FK do master data (ui.Customer, ui.Product) jako stringi — bez cyklu importu.
"""

from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class Shipment(models.Model):
    STATUS = [
        ("draft",     "Robocze"),
        ("confirmed", "Zatwierdzone"),
        ("sent",      "Wysłane"),
        ("cancelled", "Anulowane"),
    ]
    name = models.CharField(max_length=200, verbose_name="Nazwa przesyłki")
    destination_country = models.CharField(max_length=2, blank=True, verbose_name="Kraj docelowy (ISO)")
    destination_city = models.CharField(max_length=100, blank=True, verbose_name="Miasto")
    destination_postal = models.CharField(max_length=20, blank=True, verbose_name="Kod pocztowy")
    recipient_name = models.CharField(max_length=200, blank=True, verbose_name="Odbiorca")
    customer = models.ForeignKey("ui.Customer", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="shipments", verbose_name="Klient / odbiorca")
    ramp = models.CharField(max_length=20, blank=True, verbose_name="Numer rampy")
    # ── Pola z nowego feedu HU (SAP): dokument WZ, kod klienta, daty dostawy ──────
    wz_number = models.CharField(max_length=40, blank=True, verbose_name="Numer WZ")
    kunnr = models.CharField(max_length=20, blank=True, db_index=True,
                             verbose_name="Kod klienta SAP (KUNNR)")
    outbound_delivery_date = models.DateField(null=True, blank=True,
                                              verbose_name="Data dostawy wychodzącej")
    # Data UTWORZENIA dostawy wychodzącej (SAP ERDAT) — główne sortowanie list HU.
    outbound_created_date = models.DateField(null=True, blank=True, db_index=True,
                                             verbose_name="Data utworzenia dostawy wychodzącej")
    is_stock = models.BooleanField(default=False, db_index=True,
                                   verbose_name="Kontener stanu (import HU)")
    # Fala 5: niekompletność przesyłki z feedu SAP — picking jeszcze trwa (kolejne HU
    # mogą dojść), więc kontrola nie powinna traktować obecnych palet jako komplet.
    picking_complete = models.BooleanField(default=False, db_index=True,
                                            verbose_name="Picking zakończony")
    picking_complete_at = models.DateTimeField(null=True, blank=True)
    picking_eta_note = models.CharField(max_length=200, blank=True,
                                        verbose_name="Status pickingu (ETA)")
    picking_eta_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True,
                                       blank=True, related_name="picking_eta_updates")
    picking_eta_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="draft", verbose_name="Status")
    stowage_efficiency_pct = models.IntegerField(
        default=85, validators=[MinValueValidator(30), MaxValueValidator(100)],
        verbose_name="Efektywność układania [%]",
        help_text="Realny stopień wykorzystania kubatury palety przy ręcznym układaniu "
                  "(mieszane SKU nigdy nie pakują się w 100%). Niższa wartość → więcej palet.")
    notes = models.TextField(blank=True, verbose_name="Uwagi")
    client_requirements = models.CharField(max_length=300, blank=True,
                                           verbose_name="Wymagania klienta")
    # Rzeczywista liczba jednostek obsługi (HU/palet) wg SAP — pole nagłówkowe
    # "Dost.Liczba jednostek obsługi" z eksportu dostawy. Gdy znane, jest to
    # prawda fizyczna (ile realnie wyszło palet) i kotwiczy wycenę, zamiast
    # polegać wyłącznie na szacunku objętościowym. 0/null = nieznane.
    actual_hu_count = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Rzeczywista liczba HU (SAP)",
        help_text="Liczba jednostek obsługi z dokumentu dostawy SAP — faktyczna "
                  "liczba palet, jaka wyszła. Gdy podana, wycena kotwiczy się na niej.")
    # Liczba palet, jaką magazyn potwierdził, że jest w stanie przygotować. Gdy
    # ustawiona, to ona (a nie sam szacunek objętościowy) idzie do spedycji na wycenę.
    warehouse_pallets = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Palet potwierdzonych przez magazyn",
        help_text="Liczba palet uzgodniona z magazynem — kotwiczy wycenę i wysyłkę do spedycji.")
    # Wybrane przy imporcie typy pojazdu/wysyłki (klucze z vehicle_load.VEHICLES po
    # przecinku, np. "naczepa,cont40"). Sterują tym, czym liczymy/wizualizujemy/wyceniamy.
    transport_modes = models.CharField(max_length=200, blank=True,
                                       verbose_name="Typy wysyłki (pojazdy)")
    # Komentarz planisty na wypadek rozjazdu danych przy przypiętej liczbie palet
    # (np. model 3D / szacunek ≠ liczba uzgodniona z magazynem lub z SAP).
    anchor_note = models.CharField(max_length=300, blank=True,
                                   verbose_name="Komentarz do liczby palet (rozjazd danych)")
    # Tryb załadunku: domyślnie na paletach. "loose" = luzem (bez palet) — wtedy (lub gdy
    # wybrany pojazd to kontener) wizualizujemy kontener z kartonami luzem zamiast palet.
    LOAD_MODE = [("pallets", "Na paletach"), ("loose", "Bez palet (luzem)")]
    load_mode = models.CharField(max_length=10, choices=LOAD_MODE, default="pallets",
                                 verbose_name="Tryb załadunku")
    # Powiadomienie klienta o załadunku towaru + przybliżona data dostawy (z reguły
    # zdefiniowana wcześniej przez spedycję na wybranej ofercie; można nadpisać).
    client_eta = models.DateField(null=True, blank=True,
                                  verbose_name="Przybliżona data dostawy do klienta")
    client_loaded_notified_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Powiadomiono klienta o załadunku")
    # Fired once, when every handling unit (pallet) of the shipment has been controlled OK,
    # so the "all pallets ready" alert isn't re-sent on each subsequent scan.
    ready_notified_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Powiadomiono o gotowości wszystkich palet")
    author = models.CharField(max_length=120, blank=True, verbose_name="Autor dostawy")
    author_email = models.CharField(max_length=200, blank=True, verbose_name="E-mail autora")
    # Pallet-height scenario picked for the quote + warehouse build (cm). When set, the
    # other scenario is frozen in the UI so the warehouse knows which version to prepare.
    selected_pallet_height_cm = models.IntegerField(
        null=True, blank=True, verbose_name="Wybrana wysokość palety [cm]")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ui_shipment"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["-created_at"]
        # Import HU szuka Shipment po (name, is_stock) — DB-003. Meta.indexes zamiast
        # db_index=True: AddIndex to sam CREATE INDEX, a AlterField na SQLite przebudowuje tabelę.
        indexes = [models.Index(fields=["name", "is_stock"], name="shipment_name_stock_idx")]

    def __str__(self):
        return self.name

    def lines_count(self):
        return self.lines.count()

    def picking_summary(self):
        """Ile palet gotowych (is_completed) i czy czekamy na kolejne (picking niezakończony)."""
        ready = sum(1 for h in self.handling_units.all() if h.is_completed)
        return {"ready": ready, "waiting": not self.picking_complete}

    def transport_mode_keys(self):
        """Selected vehicle/shipment-type keys (from transport_modes), order preserved."""
        return [m.strip() for m in (self.transport_modes or "").split(",") if m.strip()]

    def primary_vehicle_key(self):
        """First chosen transport type (the one we visualise against), or '' if none."""
        keys = self.transport_mode_keys()
        return keys[0] if keys else ""

    def wants_container_viz(self):
        """True when we should show the CONTAINER (loose-carton) view rather than pallets:
        either loading loose ('bez palet') or the chosen vehicle is a sea container."""
        return self.load_mode == "loose" or self.primary_vehicle_key().startswith("cont")

    @property
    def is_pallet_count_anchored(self):
        """True when the pallet count is pinned to an agreed/real figure (SAP HU or the
        warehouse-committed count) — the efficiency slider then can't move it."""
        return bool(self.actual_hu_count or self.warehouse_pallets)

    def workflow(self):
        """Milestone checklist for the delivery process (status machine).

        Returns a list of {key,label,state,detail}; state ∈ done|warn|wait|err."""
        offers = list(self.quote_offers.all())
        sent = len(offers)
        quoted = sum(1 for o in offers if o.submitted_at)
        selected = next((o for o in offers if o.selected), None)
        wh = {w.kind: w for w in self.wh_confirmations.all()}
        da = getattr(self, "driver", None)
        hus = list(self.handling_units.all())
        hu_escaped = sum(1 for h in hus if h.status == "escaped")
        active_hus = [h for h in hus if h.status != "escaped"]
        hu_total = len(active_hus)
        hu_ok = sum(1 for h in active_hus if h.status == "ok")
        hu_bad = sum(1 for h in active_hus if h.status == "to_recheck")

        def wh_state(w):
            if not w:
                return "wait", "—"
            return ({"yes": "done", "no": "err", "pending": "warn"}[w.status],
                    w.get_status_display())

        s1, d1 = wh_state(wh.get("pre"))
        s2, d2 = wh_state(wh.get("date"))
        return [
            {"key": "offer", "label": "Oferta przygotowana",
             # lines_count (adnotacja listy przesyłek) oszczędza zapytanie per wiersz.
             "state": "done" if (self.lines_count if hasattr(self, "lines_count")
                                 else self.lines.exists()) else "wait", "detail": ""},
            {"key": "wh1", "label": "Potwierdzenie magazynu (15 min)", "state": s1, "detail": d1},
            {"key": "sent", "label": "Wysłane do spedycji",
             "state": "done" if sent else "wait", "detail": f"{sent} adres(ów)"},
            {"key": "quoted", "label": "Wyceniono",
             "state": "done" if quoted else "wait", "detail": f"{quoted}/{sent}"},
            {"key": "selected", "label": "Wybrana spedycja",
             "state": "done" if selected else "wait",
             "detail": (f"{selected.carrier_name} · {selected.amount} {selected.currency}"
                        if selected else "")},
            {"key": "wh2", "label": "Potwierdzenie przygotowania (data odbioru)", "state": s2, "detail": d2},
            {"key": "driver", "label": "Dane kierowcy",
             "state": "done" if (da and da.filled_at) else "wait",
             "detail": (da.driver_plate if da else "")},
            {"key": "hu", "label": "Kontrola HU (sprawdzone)",
             "state": ("err" if hu_bad else ("done" if hu_total and hu_ok == hu_total else "wait")),
             "detail": f"{hu_ok}/{hu_total}" + (f" · {hu_bad} do rekontroli" if hu_bad else "")
                       + (f" · {hu_escaped} bez kontroli" if hu_escaped else "")},
            {"key": "pickup", "label": "Potwierdzony odbiór",
             "state": ("done" if (da and da.pickup_status == "confirmed") else
                       ("err" if (da and da.pickup_status == "declined") else "wait")),
             "detail": (da.get_pickup_status_display() if da else "")},
        ]

    def workflow_primary(self):
        """A single status label (the furthest reached, or a blocker) + colour + is_blocker.
        Ikonę ostrzeżenia dorysowuje szablon (Lucide), nie emoji w tekście."""
        steps = self.workflow()
        if any(s["state"] == "err" for s in steps):
            bad = next(s for s in steps if s["state"] == "err")
            return bad["label"], "#b91c1c", True
        done = [s for s in steps if s["state"] == "done"]
        last = done[-1] if done else steps[0]
        return last["label"], "#1a56db", False

    def selected_offer(self):
        """The chosen forwarder offer, if any (uses prefetched quote_offers)."""
        return next((o for o in self.quote_offers.all() if o.selected), None)

    # BIZ-003 / Q-41 („Automatycznie”): JEDYNE miejsce przejść statusu. Klucz = status
    # docelowy, wartość = statusy, z których wolno przejść. Formularz nie ustawia statusu.
    STATUS_TRANSITIONS = {
        "confirmed": ("draft",),                        # wybór oferty spedycji
        "sent":      ("draft", "confirmed"),            # kierowca potwierdził odbiór
        "draft":     ("confirmed", "cancelled"),        # ponowna wycena (requote)
        "cancelled": ("draft", "confirmed", "sent"),    # anulowanie przez Transport
    }

    def _transition(self, target):
        """Przejdź do `target`, jeśli wolno z bieżącego statusu. Zwraca True przy zmianie;
        niedozwolone przejście (albo już w `target`) = no-op, False."""
        if self.status not in self.STATUS_TRANSITIONS[target]:
            return False
        self.status = target
        self.save(update_fields=["status", "updated_at"])
        return True

    def mark_confirmed(self):
        return self._transition("confirmed")

    def mark_sent(self):
        return self._transition("sent")

    def mark_draft(self):
        return self._transition("draft")

    def mark_cancelled(self):
        return self._transition("cancelled")

    def hu_checked_ready(self):
        """True when every controllable HU is OK — the key 'ready to ship' signal.
        HU 'escaped' (wyjechało bez kontroli — dyspozycja lidera, F2) jest wykluczone,
        żeby wysyłka nie wisiała 'niegotowa' w nieskończoność; fakt ominięcia jest zapisany."""
        active = [h for h in self.handling_units.all() if h.status != "escaped"]
        return bool(active) and all(h.status == "ok" for h in active)

    def has_controlled_hu(self):
        """True gdy którakolwiek HU weszła w kontrolę (status ≠ planned albo ma próby
        kontroli). Regeneracja HU / usunięcie przesyłki skasowałyby wtedy kaskadą historię
        kontroli (HUStatusEvent, próby, zgłoszenia, zdjęcia) — DB-002. Relacja wsteczna,
        bez importu huctl (granica aplikacji-liści)."""
        return self.handling_units.filter(
            ~models.Q(status="planned") | models.Q(control_attempts__isnull=False)).exists()

    def pallet_readiness(self):
        """(controlled-OK, total) handling units (pallets) — live build progress as the
        warehouse scans each pallet. 'escaped' HU wykluczone z licznika. (0, 0) gdy brak HU."""
        active = [h for h in self.handling_units.all() if h.status != "escaped"]
        return sum(1 for h in active if h.status == "ok"), len(active)

    def destination_address(self):
        """Human-readable destination for maps/quotes (city, postal, country)."""
        parts = [self.destination_city, self.destination_postal, self.destination_country]
        return ", ".join(p for p in parts if p)

    def google_maps_url(self):
        """Google Maps directions link (origin from settings) → shows the km on open.
        No API key needed; opens the route in Google Maps."""
        dest = self.destination_address()
        if not dest:
            return None
        from urllib.parse import quote
        from django.conf import settings
        origin = getattr(settings, "SHIPMENT_ORIGIN_ADDRESS", "") or ""
        url = "https://www.google.com/maps/dir/?api=1&destination=" + quote(dest)
        if origin:
            url += "&origin=" + quote(origin)
        return url


class ShipmentLine(models.Model):
    UNIT = [
        ("szt", "Sztuki (szt)"),
        ("kar", "Kartony (KAR)"),
        ("pal", "Palety"),
    ]
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("ui.Product", on_delete=models.PROTECT, verbose_name="Produkt")
    # Durable business-key reference (the product code) — FK→kod seam for the master-data
    # split (a FK can't span databases; the code can). Auto-filled from the FK in save(),
    # so every writer keeps working unchanged. Readers should prefer `product_ref`.
    product_code = models.CharField(max_length=50, blank=True, default="",
                                    verbose_name="Kod produktu")
    quantity = models.FloatField(verbose_name="Ilość")
    unit = models.CharField(max_length=10, choices=UNIT, default="kar", verbose_name="Jednostka")
    # Original unit label from an imported file (e.g. "OP", "SZT") — display only.
    source_unit = models.CharField(max_length=20, blank=True, verbose_name="Jedn. źródłowa")
    notes = models.CharField(max_length=200, blank=True, verbose_name="Uwagi")
    order = models.PositiveSmallIntegerField(default=0)

    @property
    def product_ref(self):
        """Business key (code) of the line's product — new field first, FK as fallback."""
        return self.product_code or (self.product.code if self.product_id else "")

    def product_data(self):
        """Master data of the line's product via the master-data client (local DB today,
        the master-data service when MASTER_DATA_URL is set). None when unknown."""
        code = self.product_ref
        if not code:
            return None
        from ui import master_data_client
        return master_data_client.get_product(code)

    def save(self, *args, **kwargs):
        # Keep the business key in lockstep with the FK — one hook covers every writer.
        if self.product_id:
            code = (self.product.code or "")[:50]
            if code and self.product_code != code:
                self.product_code = code
                uf = kwargs.get("update_fields")
                if uf is not None:
                    kwargs["update_fields"] = set(uf) | {"product_code"}
        super().save(*args, **kwargs)

    class Meta:
        db_table = "ui_shipmentline"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        ordering = ["order", "id"]
        unique_together = [("shipment", "product")]


class TransportKpiSnapshot(models.Model):
    """Cache dashboardu KPI transportu — jeden wiersz (load()). Widok renderuje z tego
    snapshotu zamiast przeliczać packing WSZYSTKICH shipmentów przy każdym żądaniu
    (O(N) silnika pakowania per request). Odświeżany zadaniem Celery (beat co godzinę)
    albo leniwie przy wejściu, gdy snapshot jest starszy niż TTL widoku."""
    data = models.JSONField(default=dict, verbose_name="Dane KPI (kpi + charts)")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Ostatnie przeliczenie")

    class Meta:
        db_table = "ui_transportkpisnapshot"   # W2: tabela zostaje po ui_* (SeparateDatabaseAndState)
        verbose_name = "Snapshot KPI transportu"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"KPI transportu z {self.updated_at:%Y-%m-%d %H:%M}"

    @classmethod
    def load(cls):
        return cls.objects.first()

__all__ = [n for n in list(globals().keys()) if not n.startswith('__')]
