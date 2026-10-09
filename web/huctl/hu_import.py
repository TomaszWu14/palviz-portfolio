"""Import wsadu HU (plik SAP / pull Power BI) — serwis, nie widok.

Wspólny rdzeń dla uploadu pliku (huctl.views.hu) i zadania Celery w rdzeniu `ui`
(scheduled_powerbi_stock_pull). Wydzielone z widoków w ARCH-001. Czyste parsowanie
(aliasy nagłówków, liczby, daty, REF) leży w ``huctl.hu_import_parse`` (CODE-001);
tu zostaje zapis — stare nazwy parserów są re-eksportowane dla wołających."""
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from huctl.hu_import_parse import (  # noqa: F401  (re-eksport: stare nazwy dla hu.py/ukraine)
    _HU_ALIASES, _TRUTHY, _map_hu_columns, _match_product_in_ref, _parse_date_any,
    cell as _cell, is_truthy, item_units, parse_float, parse_qty, resolve_product_code,
)
from huctl.models import HandlingUnit, HandlingUnitItem
from ui.models import PalletizationInstruction, Product, Shipment
from ui.views.core.xlsx import MAX_IMPORT_ROWS

_STOCK_NAME = "Stock magazynowy"
_HU_SAVE_FIELDS = ["location", "warehouse_type", "recipient_type", "picker",
                   "is_completed", "stock_status", "length_cm", "width_cm", "height_cm"]
_SH_SAVE_FIELDS = ["wz_number", "kunnr", "destination_country", "customer",
                   "outbound_delivery_date", "outbound_created_date",
                   "picking_complete", "picking_complete_at"]
# BIZ-001: HU „w kontroli” — status ≠ planned albo są już próby kontroli.
_LOCKED_Q = ~Q(status="planned") | Q(control_attempts__isnull=False)


def _load_ppc_by_code():
    """Pieces-per-carton (OP per KAR) per product code — najnowsza aktywna instrukcja."""
    ppc_by_code = {}
    for instr in (PalletizationInstruction.objects.filter(is_active=True)
                  .order_by("product__code", "-version").select_related("product")):
        ppc_by_code.setdefault(instr.product.code, instr.pcs_per_carton or 1)
    return ppc_by_code


def _load_locked(picks):
    """BIZ-001: kod → pk HU już w kontroli, wśród kodów z wsadu (paczki po 500 — limit SQLite)."""
    picks, locked = sorted(picks), {}
    for i in range(0, len(picks), 500):
        locked.update(HandlingUnit.objects.filter(code__in=picks[i:i + 500]).filter(_LOCKED_Q)
                      .values_list("code", "pk").distinct())
    return locked


class _ImportRun:
    """Stan jednego przebiegu importu: słowniki referencyjne, cache, brudne obiekty, liczniki."""

    def __init__(self, idx, rows):
        from ui.models import Customer
        self.idx = idx
        self.products = {p.code: p for p in Product.objects.filter(is_active=True)}
        # Klienci po KUNNR (bez zer wiodących z obu stron) — twarde wiązanie dostawa→klient.
        self.customers_by_kunnr = {c.kunnr.lstrip("0"): c
                                   for c in Customer.objects.exclude(kunnr="") if c.kunnr.strip("0")}
        self.ppc_by_code = _load_ppc_by_code()
        self.code_set = set(self.products)
        self.sh_cache, self.hu_cache, self.seq_of = {}, {}, {}
        self.hu_dirty, self.sh_dirty, self.new_items = {}, {}, []
        # BIZ-001: HU w kontroli są zamrożone — feed nie zmienia im pozycji, metadanych
        # ani dostawy/seq. Liczymy pominięte wiersze i same HU (komunikat po imporcie).
        self.locked = _load_locked({_cell(r, idx, "pickhu") for r in rows} - {""})
        self.locked_seen, self.skipped_locked = set(), 0
        # Wsad kontroli HU jest zawsze wydaniowy → każdy wiersz ma Dokument (dostawę). Wiersz bez
        # Dokumentu w feedzie z dostawami to BŁĄD wsadu (nie cichy „stock") — liczymy, by ostrzec.
        self.no_delivery = self.with_delivery = 0
        # Spec wsadu (19/20 kolumn): partia dostawcy i termin ważności są OBOWIĄZKOWE.
        # Wiersz bez nich wchodził po cichu „z dziurą" (grill 2026-09-05, pyt. 30) —
        # liczymy, żeby import głośno ostrzegł zamiast przemilczeć.
        self.missing_batch = self.missing_expiry = 0

    def cell(self, row, field):
        return _cell(row, self.idx, field)

    def mark_sh(self, shipment):
        self.sh_dirty[shipment.pk] = shipment

    def mark_hu(self, hu):
        self.hu_dirty[hu.pk] = hu


# ── przesyłka ──────────────────────────────────────────────────────────────
def _max_seq(shipment):
    # max(seq), not count() — counting breaks unique_together(shipment, seq)
    # when HUs have a gap (e.g. one deleted), colliding with an existing seq.
    return shipment.handling_units.aggregate(m=Max("seq"))["m"] or 0


def _get_shipment(run, delivery):
    sh_name = (delivery or _STOCK_NAME)[:200]
    # Cache też po (nazwa, is_stock) — dostawa o nazwie „Stock magazynowy” i wiersz bez
    # dokumentu w jednym pliku to DWIE przesyłki (transport vs kontener stock).
    key = (sh_name, not delivery)
    shipment = run.sh_cache.get(key)
    if shipment is not None:
        return shipment
    # A row without a delivery column belongs to the stock container,
    # which lives in the Control module — not the transport shipments list.
    # Key on is_stock too, so a stock import never reuses (and pollutes) a real
    # transport shipment that happens to share the name — and vice-versa.
    # filter().first() zamiast get_or_create: name NIE jest unikalny, więc przy
    # zdublowanej nazwie get_or_create rzuca MultipleObjectsReturned (500) i wywala
    # cały atomic-import. Bierzemy pierwszą istniejącą (VBELN i tak unikalny w praktyce).
    is_stock = not delivery
    shipment = Shipment.objects.filter(name=sh_name, is_stock=is_stock).first()
    if shipment is None:
        shipment = Shipment.objects.create(name=sh_name, is_stock=is_stock)
    run.sh_cache[key] = shipment
    run.seq_of[shipment.pk] = _max_seq(shipment)
    return shipment


def _fill_shipment_blanks(run, shipment, row):
    """WZ, kraj i daty dostawy — fill-if-blank (nowe kolumny feedu są opcjonalne)."""
    wz = run.cell(row, "wz")
    if wz and not shipment.wz_number:
        shipment.wz_number = wz[:40]
        run.mark_sh(shipment)
    country = run.cell(row, "country")
    if country and not shipment.destination_country:
        shipment.destination_country = country[:2].upper()
        run.mark_sh(shipment)
    for field, key in (("outbound_delivery_date", "out_date"),
                       ("outbound_created_date", "out_created")):
        if getattr(shipment, field) is None:
            d = _parse_date_any(run.cell(row, key))
            if d:
                setattr(shipment, field, d)
                run.mark_sh(shipment)


def _fill_shipment_kunnr(run, shipment, row):
    kunnr = run.cell(row, "kunnr")
    if not kunnr or shipment.kunnr:
        return
    shipment.kunnr = kunnr[:20]
    run.mark_sh(shipment)
    if shipment.customer_id is None:
        match = run.customers_by_kunnr.get(kunnr.lstrip("0"))
        if match:
            shipment.customer = match


def _apply_picking_done(run, shipment, row):
    # Picking całej przesyłki: kolumna niesie prawdę per snapshot — ustawiaj w OBIE
    # strony jak przy "completed" HU, żeby cofnięcie w SAP też się odbiło.
    picking_raw = run.cell(row, "picking_done")
    if not picking_raw:
        return
    val = is_truthy(picking_raw)
    if shipment.picking_complete != val:
        shipment.picking_complete = val
        if val and not shipment.picking_complete_at:
            shipment.picking_complete_at = timezone.now()
        elif not val:
            shipment.picking_complete_at = None   # cofnięcie w SAP — znacznik nieaktualny
        run.mark_sh(shipment)


def _fill_shipment(run, shipment, row):
    """Uzupełnij metadane dostawy z dowolnego wiersza (wzorzec jak przy metadanych HU)."""
    _fill_shipment_blanks(run, shipment, row)
    _fill_shipment_kunnr(run, shipment, row)
    _apply_picking_done(run, shipment, row)


# ── HU ─────────────────────────────────────────────────────────────────────
def _next_seq(run, shipment):
    if shipment.pk not in run.seq_of:
        run.seq_of[shipment.pk] = _max_seq(shipment)
    run.seq_of[shipment.pk] += 1
    return run.seq_of[shipment.pk]


def _get_hu(run, pick, shipment):
    # pickHU jest globalnie unikalny (jedna fizyczna paleta), więc szukamy po
    # samym kodzie — bez kontekstu dostawy. Gdy SAP przeksięgował paletę na inną
    # dostawę, PRZEPINAMY istniejącą HU zamiast tworzyć duplikat, który rozjechałby
    # skanowanie (skan po kodzie trafiłby w losowy z dwóch wierszy) i zgubił
    # dotychczasową historię kontroli.
    hu = run.hu_cache.get(pick)
    if hu is not None:
        return hu
    hu = HandlingUnit.objects.filter(code=pick).first()
    if hu is None:
        hu = HandlingUnit.objects.create(shipment=shipment, seq=_next_seq(run, shipment), code=pick)
    elif hu.shipment_id != shipment.pk:
        hu.shipment = shipment
        hu.seq = _next_seq(run, shipment)
        hu.save(update_fields=["shipment", "seq"])
    run.hu_cache[pick] = hu
    return hu


def _fill_hu_blanks(run, hu, row):
    # Fill HU metadata from ANY row of the pickHU — a later row may carry the
    # location/warehouse/recipient/picker the first row left blank.
    for attr, key, n in (("location", "location", 40), ("warehouse_type", "warehouse", 40),
                         ("recipient_type", "recipient", 40), ("picker", "picker", 80)):
        val = run.cell(row, key)
        if val and not getattr(hu, attr):
            setattr(hu, attr, val[:n])
            run.mark_hu(hu)


def _apply_hu_snapshot(run, hu, row):
    # Status kompletacji: kolumna niesie prawdę per snapshot — ustawiaj w OBIE
    # strony (nie fill-if-blank), żeby cofnięcie kompletacji w SAP też się odbiło.
    completed_raw = run.cell(row, "completed")
    if completed_raw:
        val = is_truthy(completed_raw)
        if hu.is_completed != val:
            hu.is_completed = val
            run.mark_hu(hu)
    # Status zapasu (SAP) — prawda per snapshot, ustawiaj wprost gdy kolumna obecna.
    stock_status_raw = run.cell(row, "stock_status")
    if stock_status_raw and hu.stock_status != stock_status_raw[:12]:
        hu.stock_status = stock_status_raw[:12]
        run.mark_hu(hu)


def _fill_hu_dims(run, hu, row):
    # Wymiary palety (jeśli feed je poda) — fill-if-blank. 0 = „brak danych”, nie realny
    # wymiar: paleta nie ma zerowego boku, a UI/eksporty i tak traktują 0 jak brak (`or ""`).
    for attr, key in (("length_cm", "length"), ("width_cm", "width"), ("height_cm", "height")):
        if getattr(hu, attr) is None:
            fv = parse_float(run.cell(row, key))
            if fv:
                setattr(hu, attr, fv)
                run.mark_hu(hu)


# ── pozycja ────────────────────────────────────────────────────────────────
def _build_item(run, hu, row, ref):
    qty = parse_qty(run.cell(row, "qty"))
    unit = run.cell(row, "unit") or "szt"     # imported qty is the base unit (e.g. OP/Sztuka)
    # Link to a product (REF may be a composite like "(48301)V(BP-30F)") to get
    # the carton conversion, so counting in KAR validates the OP pick.
    code = resolve_product_code(ref, run.products, run.code_set)
    prod = run.products.get(code)
    alt_unit, alt_qty = item_units(qty, unit, run.ppc_by_code.get(code, 0) if code else 0)
    vb = run.cell(row, "vendor_batch")
    exp = _parse_date_any(run.cell(row, "expiry"))
    run.missing_batch += not vb
    run.missing_expiry += exp is None
    return HandlingUnitItem(
        hu=hu, ref_code=(prod.code if prod else ref)[:50], product=prod,
        description=(run.cell(row, "desc") or (prod.name if prod else ""))[:120],
        lot=run.cell(row, "lot")[:16], expiry=exp, vendor_batch=vb[:40],
        alt_unit=alt_unit[:20], alt_qty=alt_qty, base_unit=unit[:20], base_qty=qty,
        expected_qty=qty, unit=unit[:20], weight_kg=parse_float(run.cell(row, "weight")))


def _import_row(run, row):
    pick, ref = run.cell(row, "pickhu"), run.cell(row, "ref")
    if not pick or not ref:
        return
    delivery = run.cell(row, "shipment")
    if delivery:
        run.with_delivery += 1
    else:
        run.no_delivery += 1
    if pick in run.locked:
        # BIZ-001: HU w kontroli — nic z feedu (ani dostawa/seq, ani metadane, ani pozycje);
        # metadane przesyłki z tego wiersza też pomijamy (nie tworzymy pustej dostawy).
        run.skipped_locked += 1
        run.locked_seen.add(pick)
        return
    shipment = _get_shipment(run, delivery)
    hu = _get_hu(run, pick, shipment)
    _fill_hu_blanks(run, hu, row)
    _fill_shipment(run, shipment, row)
    _apply_hu_snapshot(run, hu, row)
    _fill_hu_dims(run, hu, row)
    run.new_items.append(_build_item(run, hu, row, ref))


# ── zapis ──────────────────────────────────────────────────────────────────
def _persist(run):
    """Zapisz metadane, podmień pozycje, stempluj last_seen_at. Wiersze HU w kontroli
    (BIZ-001) zostały pominięte już w _import_row — ich pozycje zostają nietknięte."""
    for hu in run.hu_dirty.values():
        hu.save(update_fields=_HU_SAVE_FIELDS)
    for sh in run.sh_dirty.values():
        sh.save(update_fields=_SH_SAVE_FIELDS)
    # Replace items only for HUs that actually got fresh rows — otherwise a pickHU
    # whose rows were all skipped (e.g. blank ref) would lose its existing items.
    hu_ids = {it.hu_id for it in run.new_items}
    if hu_ids:
        HandlingUnitItem.objects.filter(hu_id__in=hu_ids).delete()
    HandlingUnitItem.objects.bulk_create(run.new_items, batch_size=2000)
    # Stamp every HU that appeared in this snapshot as "seen now" — an uncontrolled HU
    # that stops reappearing was likely booked out in SAP before we controlled it, and
    # is surfaced to the leader as a stale/vanished item (Q95/96).
    seen_ids = [h.pk for h in run.hu_cache.values()] + [run.locked[c] for c in run.locked_seen]
    if seen_ids:
        HandlingUnit.objects.filter(pk__in=seen_ids).update(last_seen_at=timezone.now())


def import_hu_rows(header, rows):
    """Shared HU-import core for both the file upload and the Power BI pull.

    Takes the table shape of _read_table (lowercased header + value-lists). Groups rows
    by (delivery, pickHU); a row without a delivery column lands in the stock container.
    Returns (True, {"hu": n, "items": m, ...}) or (False, error_message)."""
    idx = _map_hu_columns(header)
    if "pickhu" not in idx or "ref" not in idx:
        return False, "Brak rozpoznanych kolumn pickHU i REF. Nagłówki: " + ", ".join(header)
    if len(rows) > MAX_IMPORT_ROWS:
        return False, f"Zbyt dużo wierszy (max {MAX_IMPORT_ROWS:,}).".replace(",", " ")
    with transaction.atomic():
        run = _ImportRun(idx, rows)
        for row in rows:
            _import_row(run, row)
        _persist(run)
    # skipped_locked = pominięte WIERSZE (pozycje), locked_hus = ile HU w kontroli (BIZ-001).
    return True, {"hu": len(run.hu_cache) + len(run.locked_seen), "items": len(run.new_items),
                  "no_delivery": run.no_delivery, "with_delivery": run.with_delivery,
                  "missing_batch": run.missing_batch, "missing_expiry": run.missing_expiry,
                  "skipped_locked": run.skipped_locked, "locked_hus": len(run.locked_seen)}
