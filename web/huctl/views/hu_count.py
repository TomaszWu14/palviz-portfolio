# Silnik liczenia pozycji: kafelki jednostek, przeliczniki JP/AJM, zapis wyniku.

from ui.views.core import (
    HandlingUnit, HandlingUnitItem, HUControlAttempt, HUQualityIssue,
    _controller, get_object_or_404, messages, redirect, settings,
)
from django.utils import timezone
from django.db import transaction
from .hu_helpers import _qty_error, _recheck_by_original, _type_controlled, _valid_photo, _zone_ok  # noqa: F401
from .hu_hub import _maybe_log_photo_skip, _needs_photo  # noqa: F401
from .hu_helpers import _ensure_started  # noqa: F401
from ..count_policy import (count_gate, is_blind_recount_needed,
                            photo_required_for_flags, qty_mismatch, whole_units_ok)

_INSTR_UNSET = object()


def _instr_for(item):
    """Najnowsza aktywna instrukcja paletyzacji dla pozycji — cache'owana NA INSTANCJI.

    `_alt_conv` i `_unit_factors` pytały o to samo osobno, więc ekran detalu robił dwa
    zapytania na pozycję (a liczenie trzy na tę jedną). Cache siedzi na obiekcie pozycji,
    więc jego zasięg to naturalnie jedno żądanie — dane pozostają „live from master data",
    tak jak opisują to konwertery."""
    cached = getattr(item, "_instr_cache", _INSTR_UNSET)
    if cached is not _INSTR_UNSET:
        return cached
    from ui.models import PalletizationInstruction
    instr = None
    if item.product_id:
        instr = (PalletizationInstruction.objects.filter(product_id=item.product_id, is_active=True)
                 .order_by("-version").first())
    item._instr_cache = instr
    return instr


def _prefetch_instructions(items):
    """Wypełnia cache instrukcji dla całej listy pozycji JEDNYM zapytaniem (zamiast N)."""
    from ui.models import PalletizationInstruction
    ids = {it.product_id for it in items if it.product_id}
    by_product = {}
    if ids:
        # order_by(product_id, -version) + setdefault → pierwsza napotkana to najnowsza wersja.
        for instr in (PalletizationInstruction.objects
                      .filter(product_id__in=ids, is_active=True)
                      .order_by("product_id", "-version")):
            by_product.setdefault(instr.product_id, instr)
    for it in items:
        it._instr_cache = by_product.get(it.product_id)


def _unit_tile_key(sap_unit):
    """Map a SAP unit code (JM/AJM) to one of the four counting-tile keys used on the
    scanner screen: base / opz / kar / pal. Unknown → 'base' (the piece/OP tile)."""
    u = (sap_unit or "").strip().upper()
    if u.startswith(("KAR", "CAR")):
        return "kar"
    if u.startswith("PAL"):
        return "pal"
    if u.startswith("OPZ") or "ZBIOR" in u:
        return "opz"
    return "base"


def _annotate_picked_units(items, hu=None):
    """Podpowiedź „w jakiej jednostce pobrano" dla każdej pozycji — z logu pobrań SAP
    (PickerActivity). JEDNO zapytanie na cały ekran. Ustawia na pozycji:
      • it.picked_units — zbiór kluczy kafli do pulsowania (np. {"kar","base"}),
      • it.picked_label — napis np. „2 KAR + 1 OP" (puste gdy brak dopasowania).
    Dopasowanie po (indeks=ref_code, partia=lot); pusty lot po którejś stronie → match po
    samym indeksie. To podpowiedź, nie autorytet — luźny match wystarcza.
    # ponytail: match po (material, lot); zawęź oknem czasu przesyłki dopiero gdy pojawią się fałszywe podpowiedzi."""
    from ui.models import PickerActivity
    for it in items:
        it.picked_units, it.picked_label = set(), ""
    codes = {it.ref_code for it in items if it.ref_code}
    if not codes:
        return
    # Okno czasowe: od utworzenia dostawy (z 1-dniowym zapasem), fallback 30 dni.
    # Bez tego hint sumował WSZYSTKIE historyczne pobrania materiału (rosnący skan
    # per request + błędna ilość przy powtarzalnych indeksach).
    from datetime import timedelta
    since = None
    ship_date = getattr(getattr(hu, "shipment", None), "outbound_created_date", None)
    if ship_date:
        since = ship_date - timedelta(days=1)
    rows = PickerActivity.objects.filter(material_code__in=codes, qty__isnull=False)
    rows = (rows.filter(confirmed_at__date__gte=since) if since
            else rows.filter(confirmed_at__gte=timezone.now() - timedelta(days=30)))
    rows = rows.values_list("material_code", "lot", "unit", "qty")
    # (material, lot) → {unit_upper: suma_qty}
    picks = {}
    for mat, lot, unit, qty in rows:
        key = (mat, (lot or "").strip())
        picks.setdefault(key, {}).setdefault((unit or "").strip().upper(), 0.0)
        picks[key][(unit or "").strip().upper()] += float(qty or 0)
    for it in items:
        if not it.ref_code:
            continue
        it_lot = (it.lot or "").strip()
        agg = {}
        for (mat, lot), units in picks.items():
            if mat != it.ref_code:
                continue
            if it_lot and lot and lot != it_lot:
                continue          # obie strony mają serię i się różnią → nie ta linia
            for u, q in units.items():
                agg[u] = agg.get(u, 0.0) + q
        if not agg:
            continue
        # Zbij surowe kody SAP na 4 kanoniczne kafle (base/opz/kar/pal) — inaczej brudne
        # warianty kodu (KAR/KARTON/Kar.) wyciekały jako osobne „jednostki" (>4 w podpowiedzi).
        bucket = {}
        for u, q in agg.items():
            k = _unit_tile_key(u)
            bucket[k] = bucket.get(k, 0.0) + q
        it.picked_units = set(bucket)
        # Podpowiadamy TYLKO jednostkę pobrania, bez ilości — liczba z logu pobrań była
        # gotową odpowiedzią i kontroler ją potwierdzał zamiast liczyć. Max 4 kafle.
        _names = {"base": it.base_unit or "OP", "opz": "OPZ", "kar": "KAR", "pal": "PAL"}
        it.picked_label = " + ".join(_names.get(k, k) for k, _q in
                                     sorted(bucket.items(), key=lambda kv: -kv[1]))


def _alt_conv(item):
    """Right-hand counting unit + conversion for a position, taken LIVE from master data
    (so it's correct even for HUs generated before the rule existed):
      • KAR via pcs_per_carton (when > 1),
      • else OPZ via pcs_per_inner_pack (when > 1),
      • else the stored base/alt ratio, else 1 (no conversion).
    Returns (alt_unit_label, base-units-per-alt-unit)."""
    instr = _instr_for(item)
    if instr:
        if instr.pcs_per_carton and instr.pcs_per_carton > 1:
            return "KAR", float(instr.pcs_per_carton)
        if getattr(instr, "pcs_per_inner_pack", 0) and instr.pcs_per_inner_pack > 1:
            return "OPZ", float(instr.pcs_per_inner_pack)
    if item.base_qty and item.alt_qty and abs(item.base_qty - item.alt_qty) > 1e-9:
        return (item.alt_unit or "KAR"), item.base_qty / item.alt_qty
    return (item.alt_unit or "KAR"), 1.0


def _unit_factors(item):
    """Conversion factors (base-units per alt-unit) for the four counting tiles shown on a
    position: base (OP/SZT), OPZ, KAR, PAL. A factor of None means that unit is NOT defined
    for this index → its tile greys out ("brak przelicznika"). Taken LIVE from master data.
      • OPZ via pcs_per_inner_pack (when > 1),
      • KAR via pcs_per_carton (when > 1),
      • PAL = a FULL pallet = cartons_per_pallet × pcs_per_carton (from the chosen layout),
        so 1 PAL is many cartons (1 KAR ≠ 1 PAL); greys out when no pallet layout is known.
    Returns a dict {base_unit, opz, kar, pal}.

    Przeliczniki ze WSPÓLNEGO serwisu hierarchy.unit_factors (to samo źródło co
    MATINFO/desktop) — wcześniej liczone lokalnie z innymi fallbackami (bez
    inner_pack.units_per_pack, własny estymator PAL) → ten sam indeks pokazywał
    różne wartości na skanerze i w MATINFO."""
    from ui.hierarchy import unit_factors
    instr = _instr_for(item)
    f = unit_factors(instr)
    opz, kar, pal = f["opz"], f["kar"], f["pal"]
    # Fallback to the stored base/alt ratio when master data has no carton/inner-pack split.
    if kar is None and opz is None and item.base_qty and item.alt_qty \
            and abs(item.base_qty - item.alt_qty) > 1e-9:
        ratio = item.base_qty / item.alt_qty
        if (item.alt_unit or "KAR").upper().startswith("OP"):
            opz = ratio
        else:
            kar = ratio
    return {"base_unit": item.base_unit or "OP", "opz": opz, "kar": kar, "pal": pal}


# Kolejność i pola formularza czterech kafli liczenia.
_TILE_SPECS = [("base", "Podstawowa", "qty_base"), ("opz", "OPZ", "c_opz"),
               ("kar", "KAR", "c_kar"), ("pal", "PAL", "c_pal")]


def _count_tiles(item):
    """Kafle liczenia dla pozycji, podzielone na (widoczne, ukryte-pod-„inne”).
    Domyślnie widoczne są tylko jednostki POBRANE przez pikera (`picked_units`);
    reszta — z przelicznikiem — ląduje pod „+ inne jednostki” (uścig przy rozjeździe
    piking↔rzeczywistość). Brak danych o pobraniu → wszystkie z przelicznikiem widoczne.
    Kafle bez przelicznika w ogóle się nie renderują (znikają szare „brak przelicznika”)."""
    factors = item.factors
    picked = getattr(item, "picked_units", None) or set()
    base_unit = factors.get("base_unit") or "OP"
    shown, hidden = [], []
    for key, name, field in _TILE_SPECS:
        factor = 1.0 if key == "base" else factors.get(key)
        if not factor:
            continue                                  # brak przelicznika → pomiń kafel
        if key == "base":
            value = item.counted_qty if item.controlled else item.confirm_units.get("base")
        else:
            value = item.confirm_units.get(key)
        tile = {"key": key, "cls": f"utile--{key}", "name": name, "field": field,
                "unit": base_unit if key == "base" else key.upper(),
                "factor": factor, "base_unit": base_unit, "is_base": key == "base",
                "hint": key in picked, "value": value}
        (shown if (not picked or key in picked) else hidden).append(tile)
    return shown, hidden


def _counted_base(item, qty_base, qty_alt):
    """LEGACY (offline stary klient): ilość bazowa z konwertera lustrzanego — base ALBO
    alt×ppc (nie sumuje). Nowe liczenie online jest addytywne: patrz `_counted_from_units`."""
    ppc = _alt_conv(item)[1]                                       # base-units per alt-unit
    if qty_base is not None:
        return qty_base
    if qty_alt is not None and ppc:
        return qty_alt * ppc
    return qty_alt if qty_alt is not None else 0.0


# Liczenie ADDYTYWNE: kontroler wpisuje w KAŻDEJ jednostce ile fizycznie zliczył (np.
# 1 KAR + 5 OP), a suma po przeliczeniu na bazę = ilość oczekiwana. Kafle nie są już
# lustrem tej samej ilości — to niezależne składniki. Klucze = kafle na ekranie.
def _counted_from_units(item, units):
    """Suma bazowa ze składników per jednostka: {base, opz, kar, pal} × przelicznik.
    `base` ma przelicznik 1; pozostałe wg `_unit_factors` (None = brak → składnik pomijany)."""
    fac = _unit_factors(item)
    factors = {"base": 1.0, "opz": fac.get("opz"), "kar": fac.get("kar"), "pal": fac.get("pal")}
    total = 0.0
    for key, qty in (units or {}).items():
        f = factors.get(key)
        if qty is not None and f:
            total += qty * f
    return total


def _apply_item_count(user, hu, item, counted_base, flags, recheck,
                      client_id="", batch_ok=True, expiry_ok=True,
                      input_source="", device_id=""):
    """Apply one position count (shared by the live scanner POST and the offline batch
    sync). `counted_base` = ilość zliczona przeliczona do jednostki bazowej (addytywna suma
    kafli online, albo legacy z konwertera offline). Liczy mismatch, podnosi zgłoszenia
    jakości, oznacza pozycję i zapisuje attempt KPI. Zwraca wynik.

    Idempotencja (P3): gdy `client_id` (stabilny per-akcja id ze skanera) już ma zapisany
    attempt, zwracamy poprzedni wynik bez ponownego zapisu — replay offline-batcha nie
    podwaja KPI ani nie psuje timing."""
    if client_id:
        prev_att = HUControlAttempt.objects.filter(client_id=client_id).first()
        if prev_att is not None:
            return prev_att.result
    mismatch = qty_mismatch(counted_base, item.base_qty)

    qty_flags = {k for k in flags if k in HandlingUnitItem.QUANTITY_FLAGS}
    quality_flags = {k for k in flags if k not in HandlingUnitItem.QUANTITY_FLAGS}
    for key in quality_flags:
        HUQualityIssue.objects.get_or_create(
            hu=hu, item=item, issue_type=key, status="open",
            defaults={"raised_by": user})

    # Obowiązkowe potwierdzenie partii i daty (gdy feed je poda): brak potwierdzenia blokuje
    # „OK" — pozycja do wyjaśnienia, spójnie z niezgodnością ilości.
    confirm_missing = ((bool(item.vendor_batch) and not batch_ok)
                       or (item.expiry is not None and not expiry_ok))
    now = timezone.now()
    item.counted_qty = counted_base
    item.counted_unit = item.base_unit or "OP"
    item.vendor_batch_ok = bool(batch_ok)
    item.expiry_ok = bool(expiry_ok)
    item.result = "error" if (mismatch or qty_flags or confirm_missing) else "ok"
    item.error_flags = flags
    item.controlled = True
    item.controlled_at = now
    item.save()

    # Gap zawsze z DB (poprzednia próba kontrolera) — bez sesyjnego „skip po re-loginie".
    # Przerwy i tak wycina strona odczytu (KPI_MAX_GAP_SECONDS w _kpi_stats), a sesyjny
    # reset dawał furtkę: wyloguj/zaloguj przed każdą pozycją = zero danych timingu.
    prev = (HUControlAttempt.objects.filter(controller=user)
            .order_by("-created_at").first())
    gap = (now - prev.created_at).total_seconds() if prev else None
    HUControlAttempt.objects.create(
        hu=hu, item=item, controller=user, is_recheck=recheck,
        counted_qty=counted_base, counted_unit=item.counted_unit,
        exp_base_qty=item.base_qty, exp_base_unit=item.base_unit,
        exp_alt_qty=item.alt_qty, exp_alt_unit=item.alt_unit,
        result=item.result, error_flags=flags, seconds_since_prev=gap,
        client_id=client_id or "", input_source=input_source[:12], device_id=device_id[:64])
    return item.result


def parse_count_form(post, item):
    """Sparsuj addytywne kafle liczenia → (units, counted_base, error). error=None gdy OK.

    Waliduje zakres (_qty_error) i całe-jednostki (whole_units_ok). Wołane TYLKO dla
    action=confirm — edit-reopen jest obsłużony w widoku przed parsowaniem, więc dawne
    strażniki `action != "edit"` są tu bezwarunkowo prawdziwe. Testowalne bez HTTP:
    podajesz QueryDict/dict i pozycję."""
    bad_input = []

    def _num(name):
        raw = (post.get(name) or "").strip().replace(",", ".")
        if raw == "":
            return None
        try:
            return float(raw)
        except ValueError:
            bad_input.append(raw)   # niepuste-nieparsowalne NIE może cicho liczyć się jako 0
            return None

    # Liczenie ADDYTYWNE: operator wpisuje ile zliczył w KAŻDEJ jednostce (kafle base/OPZ/
    # KAR/PAL), a suma po przeliczeniu na bazę = ilość oczekiwana (np. 1 KAR + 5 OP = 15 OP).
    # Baza pod „qty_base" (zgodność wsteczna); addytywne dodatki: c_opz/c_kar/c_pal.
    units = {"base": _num("qty_base"), "opz": _num("c_opz"),
             "kar": _num("c_kar"), "pal": _num("c_pal")}
    any_qty = any(v is not None for v in units.values())
    if bad_input:
        return units, 0.0, ("Nieczytelna ilość („" + bad_input[0][:20]
                            + "”) — wpisz liczbę (bez spacji, kropka/przecinek dziesiętny).")
    if _qty_error(*units.values()):
        return units, 0.0, "Nieprawidłowa ilość — podaj liczbę nieujemną w rozsądnym zakresie."
    counted_base = _counted_from_units(item, units)
    if any_qty and not whole_units_ok(counted_base):
        return units, counted_base, ("Wpisz całą liczbę jednostek — bez ułamków "
                                     "(np. nie „pół kartonu”; resztę policz w mniejszej jednostce).")
    return units, counted_base, None


@_controller
def hu_control_count(request, pk, item_id):
    hu = get_object_or_404(HandlingUnit, pk=pk)
    item = get_object_or_404(HandlingUnitItem, pk=item_id, hu=hu)
    recheck = hu.status == "to_recheck"

    if request.method == "POST":
        # Pre-foto bramki (strefa/typ/statusy terminalne/rekontrola-innym/scan-enforce) —
        # jedna uporządkowana polityka, testowalna bez HTTP (huctl/count_policy.py). Muszą
        # działać na KAŻDEJ ścieżce liczenia (także auto-next z kolejki i deep-link, które
        # nie przechodzą przez hu_control_scan). Rekontrolę wykonuje INNY kontroler niż
        # prowadzący pierwotną kontrolę — pierwszego czytamy z audytu (odporne na takeover).
        gate = count_gate(
            zone_label=hu.warehouse_type or "—",
            zone_ok=_zone_ok(request.user, hu),
            type_ok=_type_controlled(hu),
            hu_status=hu.status,
            recheck=recheck,
            recheck_by_original=(recheck and _recheck_by_original(hu, request.user)),
            scan_src=request.session.get(f"hu_scan_src_{hu.pk}"),
            enforce=getattr(settings, "HU_SCAN_ENFORCE", False))
        if not gate.ok:
            messages.error(request, gate.message)
            return redirect("ui:hu_control_detail" if gate.redirect_to == "detail"
                            else "ui:hu_control_menu",
                            **({"pk": pk} if gate.redirect_to == "detail" else {}))
        # Backstop zdjęcia: auto-next/deep-link nie przechodzą przez hu_control_scan, więc
        # bramka foto musi zadziałać także tu (na urządzeniu z aparatem; Zebra przepuszczona).
        if _needs_photo(request, hu):
            return redirect("ui:hu_photo_check", pk=hu.pk)
        _maybe_log_photo_skip(request, hu)   # tryb detekcji: loguj pominięcie, nie blokuj
        # Pozycja zaksięgowana jako NIEZGODNOŚĆ (błąd pickera) — nie da się jej „poprawić"
        # ponownym liczeniem w tej samej kontroli. Rekontrolę wykona INNY kontroler po
        # zaksięgowaniu HU (second pair of eyes); autor może ją tylko podejrzeć.
        if item.controlled and item.result == "error" and not recheck:
            messages.error(request, "Ta pozycja to niezgodność — rekontrolę wykona inny "
                                    "kontroler po zaksięgowaniu HU. Możesz ją tylko podejrzeć.")
            return redirect("ui:hu_control_detail", pk=pk)
        action = request.POST.get("action", "confirm")     # "confirm" | "edit"
        if action == "edit":                                # re-open a counted position
            item.controlled = False
            item.result = ""
            item.save(update_fields=["controlled", "result"])
            request.session.pop(f"hu_confirm_{item.pk}", None)
            return redirect("ui:hu_control_detail", pk=pk)

        units, counted_base, qty_err = parse_count_form(request.POST, item)
        if qty_err:
            messages.error(request, qty_err)
            return redirect("ui:hu_control_detail", pk=pk)
        flags = {k: True for k, _ in HandlingUnitItem.ACTIVE_ERROR_FLAGS if request.POST.get(f"flag_{k}")}
        # Liczenie „w ciemno”: zgodność partii/daty jest domyślna (bez checkboxów). Niezgodność
        # zgłasza się flagą błędu (wrong_batch / wrong_expiry), więc tu zawsze potwierdzone.
        batch_ok = True
        expiry_ok = True
        photo = _valid_photo(request)
        # Damage and bad placement must be backed by a photo (evidence for the claim).
        _need = photo_required_for_flags(flags, bool(photo))
        if _need:
            messages.error(request, f"Zaznaczono „{_need}” — wymagane jest zdjęcie.")
            return redirect("ui:hu_control_detail", pk=pk)
        # Blind count: on a quantity discrepancy, make the controller recount before it is
        # booked as a (picker) error — this guards against the controller's own typo. They
        # must explicitly confirm ("Tak, jestem pewien") to book it. A photo-backed flag
        # can't be round-tripped through the session, so skip the prompt when a photo is
        # attached. Recheck already shows the expected qty, so no extra prompt there.
        sure = request.POST.get("sure") == "1"
        confirm_key = f"hu_confirm_{item.pk}"
        if is_blind_recount_needed(counted_base, item.base_qty,
                                   sure=sure, has_photo=bool(photo), recheck=recheck):
            request.session[confirm_key] = {"units": units, "flags": [k for k in flags]}
            request.session.modified = True
            return redirect("ui:hu_control_detail", pk=pk)
        with transaction.atomic():                  # serializuj kontrolerów na tej HU (P4)
            hu = HandlingUnit.objects.select_for_update().get(pk=hu.pk)
            if hu.status in ("ok", "escaped"):       # TOCTOU: HU domknięta między odczytem a lockiem
                messages.error(request, "HU została w międzyczasie zamknięta — liczenie anulowane.")
                return redirect("ui:hu_control_detail", pk=pk)
            # TOCTOU pozycji: dwa równoległe POST-y (double-click / dwie karty) mijają bramkę
            # „error" sprzed locka i dublują attempt (podwojone KPI) — odśwież i powtórz.
            item.refresh_from_db()
            if item.controlled and item.result == "error" and not recheck:
                messages.error(request, "Ta pozycja została już zaksięgowana jako niezgodność.")
                return redirect("ui:hu_control_detail", pk=pk)
            _ensure_started(hu, request.user)        # nie zostawiaj HU bez controlled_by
            _apply_item_count(request.user, hu, item, counted_base, flags, recheck,
                              batch_ok=batch_ok, expiry_ok=expiry_ok,
                              input_source=request.session.get(f"hu_scan_src_{hu.pk}", ""),
                              device_id=request.session.get("hu_device", ""))
        request.session.pop(confirm_key, None)      # discrepancy resolved → clear the prompt
        if photo:                                   # attach to the quality issue just raised
            iss = (HUQualityIssue.objects.filter(hu=hu, item=item, status="open")
                   .order_by("-raised_at").first())
            if iss and not iss.photo:
                iss.photo = photo
                iss.save(update_fields=["photo"])
        # (Master data przeniesione z listy błędów do osobnego zgłoszenia MATINFO: hu_md_report.)
        return redirect("ui:hu_control_detail", pk=pk)

    # Counting happens inline on the HU detail; a bare GET just returns there.
    return redirect("ui:hu_control_detail", pk=pk)

__all__ = [
    "_INSTR_UNSET",
    "_instr_for",
    "_prefetch_instructions",
    "_unit_tile_key",
    "_annotate_picked_units",
    "_alt_conv",
    "_unit_factors",
    "_TILE_SPECS",
    "_count_tiles",
    "_counted_base",
    "_counted_from_units",
    "_apply_item_count",
    "hu_control_count",
]
