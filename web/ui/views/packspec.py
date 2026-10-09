# E3: packspec dla przyjęć — import pliku + walidacja vs master data (MARM/instrukcja).
# Minimalny ekran listy z alertami rozbieżności (pełny proces przyjęć GR = osobna
# inicjatywa, jawnie poza zakresem pakietu — patrz plan).
from .core import (_md_role, render, require_POST, messages, redirect, get_object_or_404)
from ..models import PackagingIssue, PackSpec, PackSpecBatch, Product


# Aliasy nagłówków importu (jak w innych wsadach — elastyczne dopasowanie).
_COLS = {
    "ref_code": ["ref", "indeks", "material", "materiał", "kod"],
    "pcs_per_carton": ["szt/karton", "szt / karton", "pcs_per_carton", "szt w kartonie"],
    "cartons_per_pallet": ["kartony/paleta", "kartony / paleta", "cartons_per_pallet",
                           "kartonów na palecie"],
    "pcs_per_pallet": ["szt/paleta", "szt / paleta", "pcs_per_pallet"],
    "note": ["uwagi", "note", "komentarz"],
}


def _validate_row(spec, instr):
    """Porównaj packspec z master datą. Zwraca listę rozbieżności (pusta = zgodne).
    Porównujemy tylko pola PODANE w packspec (None = nie deklarowano)."""
    diffs = []
    if instr is None:
        return ["brak instrukcji paletyzacji w master dacie"]
    if spec.pcs_per_carton and instr.pcs_per_carton \
            and spec.pcs_per_carton != instr.pcs_per_carton:
        diffs.append(f"szt/karton: packspec {spec.pcs_per_carton} ≠ MARM {instr.pcs_per_carton}")
    from ..hierarchy import unit_factors
    f = unit_factors(instr)
    if spec.cartons_per_pallet and f["cpp"] and spec.cartons_per_pallet != f["cpp"]:
        est = " (szacunek)" if f["estimated"] else ""
        diffs.append(f"kartony/paleta: packspec {spec.cartons_per_pallet} ≠ master {f['cpp']}{est}")
    if spec.pcs_per_pallet and f["pal"] and spec.pcs_per_pallet != int(f["pal"]):
        diffs.append(f"szt/paleta: packspec {spec.pcs_per_pallet} ≠ master {int(f['pal'])}")
    return diffs


@_md_role
def packspec_list(request):
    """Widok przyjęć (minimalny): aktywna partia packspec z walidacją per wiersz.
    Rozbieżność = wyraźny alert + przycisk „Utwórz zgłoszenie" (packspec_mismatch)."""
    batch = PackSpecBatch.objects.filter(is_active=True).first()
    rows = []
    if batch:
        specs = batch.rows.select_related("product").prefetch_related(
            "product__instructions")
        open_issues = set(PackagingIssue.objects.filter(
            issue_type="packspec_mismatch").exclude(status="resolved")
            .values_list("ref_code", flat=True))
        for spec in specs:
            instr = spec.product.latest_instruction() if spec.product else None
            diffs = _validate_row(spec, instr)
            rows.append({"spec": spec, "instr": instr, "diffs": diffs,
                         "issue_open": spec.ref_code in open_issues})
    n_mismatch = sum(1 for r in rows if r["diffs"])
    return render(request, "ui/packspec/list.html", {
        "batch": batch, "rows": rows, "n_mismatch": n_mismatch})


@_md_role
@require_POST
def packspec_upload(request):
    """Import packspec z Excela (nagłówki wg aliasów _COLS). Nowa partia aktywna,
    stare dezaktywowane (wzorzec warehouse_master_upload)."""
    import openpyxl
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Brak pliku.")
        return redirect("ui:packspec_list")
    if f.size > 10 * 1024 * 1024:
        messages.error(request, "Plik zbyt duży (max 10 MB).")
        return redirect("ui:packspec_list")
    try:
        ws = openpyxl.load_workbook(f, read_only=True, data_only=True).active
    except Exception as exc:
        messages.error(request, f"Błąd odczytu pliku: {exc}")
        return redirect("ui:packspec_list")

    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter, None) or []
    cells = [str(c).lower().strip() if c else "" for c in header]
    col_map = {}
    for field, aliases in _COLS.items():
        for i, cell in enumerate(cells):
            if any(a in cell for a in aliases):
                col_map[field] = i
                break
    if "ref_code" not in col_map:
        messages.error(request, "Nie znaleziono kolumny REF/indeks. Sprawdź nagłówki.")
        return redirect("ui:packspec_list")

    def _int(row, field):
        idx = col_map.get(field)
        if idx is None or idx >= len(row) or row[idx] in (None, ""):
            return None
        try:
            return max(0, int(float(str(row[idx]).replace(",", ".")))) or None
        except (TypeError, ValueError):
            return None

    from django.db import transaction
    with transaction.atomic():
        batch = PackSpecBatch.objects.create(
            name=(request.POST.get("name") or f.name)[:120], uploaded_by=request.user)
        PackSpecBatch.objects.exclude(pk=batch.pk).update(is_active=False)
        bulk, seen = [], set()
        for row in rows_iter:
            if not row:
                continue
            idx = col_map["ref_code"]
            ref = str(row[idx]).strip() if (idx < len(row) and row[idx]) else ""
            if not ref or ref in seen:
                continue
            seen.add(ref)
            note_i = col_map.get("note")
            bulk.append(PackSpec(
                batch=batch, ref_code=ref[:50],
                product=Product.objects.filter(code__iexact=ref).first(),
                pcs_per_carton=_int(row, "pcs_per_carton"),
                cartons_per_pallet=_int(row, "cartons_per_pallet"),
                pcs_per_pallet=_int(row, "pcs_per_pallet"),
                note=(str(row[note_i]).strip()[:200]
                      if (note_i is not None and note_i < len(row) and row[note_i]) else "")))
        PackSpec.objects.bulk_create(bulk)
        batch.row_count = len(bulk)
        batch.save(update_fields=["row_count"])
    from ..models import ImportRun
    ImportRun.record("packspec", rows=len(bulk), label=batch.name, user=request.user)
    messages.success(request, f"Packspec wgrany: {len(bulk)} pozycji.")
    return redirect("ui:packspec_list")


@_md_role
@require_POST
def packspec_report_mismatch(request, pk: int):
    """Jedno kliknięcie: zgłoszenie rozbieżności packspec/MARM do pipeline'u zgłoszeń."""
    spec = get_object_or_404(PackSpec, pk=pk)
    instr = spec.product.latest_instruction() if spec.product else None
    diffs = _validate_row(spec, instr)
    if not diffs:
        messages.info(request, "Brak rozbieżności — zgłoszenie niepotrzebne.")
        return redirect("ui:packspec_list")
    if PackagingIssue.objects.filter(ref_code=spec.ref_code,
                                     issue_type="packspec_mismatch") \
            .exclude(status="resolved").exists():
        messages.info(request, f"Zgłoszenie dla {spec.ref_code} już istnieje.")
        return redirect("ui:packspec_list")
    PackagingIssue.objects.create(
        ref_code=spec.ref_code, product=spec.product,
        issue_type="packspec_mismatch",
        description="; ".join(diffs)[:500], reporter=request.user)
    messages.success(request, f"Zgłoszono rozbieżność packspec dla {spec.ref_code}.")
    return redirect("ui:packspec_list")


__all__ = ["packspec_list", "packspec_upload", "packspec_report_mismatch"]
