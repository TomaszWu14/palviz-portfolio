"""Moduł „Hierarchia opakowań" (PHV — Packaging Hierarchy Viewer).

Pracownik magazynu wpisuje/skanuje REF (skaner sprzętowy = klawiatura, pole ma autofocus)
i widzi hierarchię opakowań z master daty: sztuka → OPZ → karton → paleta, z ilościami
i wagami na każdym poziomie oraz alertami o brakujących przelicznikach. Z tego samego
ekranu zgłasza błąd master daty (5 typów, foto) — mail idzie na PHV_ISSUE_EMAIL,
status śledzi po #ID / w „Moich zgłoszeniach".

Dane wprost z istniejącej master daty (PalletizationInstruction + Product) — zero nowych
integracji (SAP jest read-only; feed już zasila te modele).
"""

import logging
from .core import (
    module_required, _any_role, require_POST, JsonResponse, settings, Q, render, messages,
    redirect, send_mail, _safe_referer, _leader
)
from django.utils import timezone
from ..models import (PackagingIssue, Product, LocationIssue)
from ..product_lookup import resolve_ref

# Kształt kodu lokalizacji, np. „B0-01-100A" / „B0-07-300C-1". Lustro
# import_locations.CODE_RE — trzymane lokalnie, by nie importować z management command.
# ponytail: jedno miejsce prawdy byłoby lepsze; przy zmianie formatu zsynchronizuj oba.
from .phv_data import CHANGE_PALLETIZATION_REASONS, LOCATION_SUGGESTIONS, MISSING_CONVERSION_UNITS, OPTIMIZATION_ISSUE_TYPES, PROCESS_CHOICES, _LOC_RE, _RETIRED_ISSUE_TYPES, _assistant_context, _hierarchy, _issue_stat, _location_card, _material_desc, _storage_strategy, _valid_image  # noqa: F401

log = logging.getLogger(__name__)

@module_required("phv")
@require_POST
def phv_assistant(request):
    """Asystent produktu — lokalny model Ollama streszcza/odpowiada na podstawie danych
    systemu o REF. 100% lokalnie (bez clouda): wymuszony provider 'ollama'. Dane produktu
    lecą tylko do lokalnego modelu (spójne z read-only SAP i wymogiem prywatności)."""
    from .. import zaria_llm
    if not zaria_llm.is_configured("ollama"):
        return JsonResponse(
            {"ok": False, "error": "Model lokalny (Ollama) nie jest skonfigurowany na serwerze."},
            status=503)

    ref = (request.POST.get("ref") or "").strip()[:50]
    question = (request.POST.get("question") or "").strip()[:1000]
    product = resolve_ref(ref)
    if not product:
        return JsonResponse({"ok": False, "error": "Nie znaleziono indeksu (REF)."}, status=404)

    hierarchy = _hierarchy(product)
    from ..hierarchy import enrich_pallet_metrics
    enrich_pallet_metrics(hierarchy["levels"], hierarchy["summary"], hierarchy["instr"])
    strategy = _storage_strategy(product)
    context = _assistant_context(product, _material_desc(product), hierarchy, strategy)

    system_prompt = (
        "Jesteś asystentem magazynowym GROOVE (firma ACME). Odpowiadasz po polsku, zwięźle "
        "i WYŁĄCZNIE na podstawie danych systemu podanych poniżej. Jeśli danych brakuje — "
        "powiedz wprost, że system ich nie ma; niczego nie zgaduj i nie wymyślaj.\n\n"
        "=== DANE SYSTEMU O PRODUKCIE ===\n" + context)
    messages = [{"role": "user",
                 "content": question or "Streść wszystko, co system wie o tym produkcie."}]

    from types import SimpleNamespace
    model = SimpleNamespace(provider="ollama",
                            key=getattr(settings, "MATINFO_OLLAMA_MODEL", "") or "llama3.1")
    try:
        text, *_rest = zaria_llm.complete(model, messages, system_prompt, max_tokens=800)
    except zaria_llm.ZariaLLMError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=502)
    return JsonResponse({"ok": True, "answer": text})


def _fuzzy_suggestions(q, have, limit=10):
    """Rozmyte podpowiedzi REF tolerujace literowki/transpozycje (RapidFuzz).

    Uzupelnia — nie zastepuje — wynik zapytania `icontains`, gdy uzytkownik przekreci kod
    (np. 'B0-38-741A' zamiast '...471A'). RapidFuzz jest importowany leniwie: gdy pakietu
    brak, funkcja zwraca [] i ekran dziala jak dawniej. `have` = kody juz pokazane."""
    try:
        from rapidfuzz import fuzz, process
    except Exception:
        return []
    rows = list(Product.objects.filter(is_active=True).values_list("code", "name"))
    if not rows:
        return []
    names = dict(rows)
    hits = process.extract(q.upper(), [c for c, _ in rows], scorer=fuzz.WRatio,
                           processor=str.upper, limit=limit + len(have))
    out = []
    for code, score, _ in hits:
        if score < 70 or code in have:
            continue
        out.append({"code": code, "name": names.get(code, "")})
        if len(out) >= limit:
            break
    return out


@module_required("phv")
def phv_home(request):
    """Ekran główny: wyszukiwarka REF/EAN + karta hierarchii + formularz zgłoszenia."""
    q = (request.GET.get("q") or "").strip()
    product = hierarchy = issue_stat = location = strategy = None
    material_desc = ""
    vol_pct = None
    suggestions = []
    if q and _LOC_RE.match(q.upper()):
        # Kod pasuje do kształtu lokalizacji → karta lokalizacji zamiast materiału.
        location = _location_card(q)
    elif q:
        # REF / EAN sztuki (product.ean) → potem EAN kartonu i EAN opak. zbiorczego (OPZ),
        # żeby operator mógł zeskanować DOWOLNY kod z palety. Join przez aktywną instrukcję
        # paletyzacji (brak FK Carton/InnerPack→Product); fallback carton.inner_pack jak w
        # build_hierarchy. .distinct().first() — ten sam produkt co renderuje hierarchia.
        # B3: 8/13 cyfr (EAN-8/EAN-13) → najpierw ścieżka EAN, potem REF — skan kodu
        # kreskowego nie koliduje z czysto numerycznymi REF-ami.
        product = resolve_ref(q)
        if product:
            # B1: historia podglądów per user (server-side; nie localStorage —
            # terminale są współdzielone). update_or_create podbija viewed_at.
            from ..models import ProductViewHistory
            ProductViewHistory.objects.update_or_create(user=request.user, product=product)
            hierarchy = _hierarchy(product)
            # Metryki prezentacji (wypełnienie palety % + per-poziom mult) — wspólny
            # helper z widokiem plannera: identyczne liczby na obu ekranach.
            from ..hierarchy import enrich_pallet_metrics
            vol_pct = enrich_pallet_metrics(
                hierarchy["levels"], hierarchy["summary"], hierarchy["instr"])
            issue_stat = _issue_stat(product)
            strategy = _storage_strategy(product)
            material_desc = _material_desc(product)
        else:
            suggestions = list(Product.objects.filter(
                Q(code__icontains=q) | Q(name__icontains=q), is_active=True)
                .values("code", "name")[:10])
            # Za malo trafien z podciagu → dobierz rozmyte (literowki/transpozycje w REF).
            if len(suggestions) < 5:
                have = {s["code"] for s in suggestions}
                suggestions += _fuzzy_suggestions(q, have, limit=10 - len(suggestions))
    # Alert „Brak przelicznika" → formularz zgłoszenia otwarty z prewypełnionym typem.
    prefill_missing = bool(hierarchy and any(
        a.startswith("Brak przelicznika") for a in hierarchy["alerts"]))
    # B1: 10 ostatnio oglądanych (bez duplikatów — unikalność per user×product w modelu).
    recent = []
    if not q:
        from ..models import ProductViewHistory
        recent = list(ProductViewHistory.objects.filter(user=request.user)
                      .select_related("product")[:10])
    from .. import zaria_llm
    return render(request, "ui/phv/home.html", {
        "recent": recent,
        "q": q, "product": product, "material_desc": material_desc,
        # Tryb „tylko poziomy opakowań" (link z kontroli HU): ukryj stany magazynowe.
        "levels_only": request.GET.get("levels") == "1",
        "assistant_enabled": zaria_llm.is_configured("ollama"),
        "hierarchy": hierarchy, "suggestions": suggestions,
        # Picker bez typów wycofanych; stare wiersze i tak mają get_issue_type_display.
        "issue_types": [(v, l) for v, l in PackagingIssue.TYPES if v not in _RETIRED_ISSUE_TYPES],
        "prefill_missing": prefill_missing,
        "vol_pct": vol_pct, "issue_stat": issue_stat,
        "strategy": strategy, "process_choices": PROCESS_CHOICES,
        "location_suggestions": LOCATION_SUGGESTIONS,
        "palletization_reasons": CHANGE_PALLETIZATION_REASONS,
        "has_variant_b": product.has_variant_b() if product else False,
        "location": location, "location_issue_types": LocationIssue.TYPES,
    })


@_any_role
def phv_suggest(request):
    """Podpowiedzi indeksu do wyszukiwarki (typeahead, od pierwszej litery).
    Guard: `_any_role` (nie `module_required("phv")`) — to reużywalny helper REF
    (zwraca tylko kod+nazwę aktywnych produktów, jak katalog) używany też przez
    carton_opt, warehouse/search, ukraine i panel lidera (UX audyt #9).
    Priorytet: kody zaczynające się od zapytania (istartswith), potem kody/nazwy
    zawierające fragment. Szybkie — bez RapidFuzz (ten zostaje na submit)."""
    q = (request.GET.get("q") or "").strip()
    if not q:
        return JsonResponse({"results": []})
    LIMIT = 8
    seen, results = set(), []

    def _add(qs):
        for row in qs.values("code", "name"):
            if row["code"] in seen:
                continue
            seen.add(row["code"])
            results.append(row)
            if len(results) >= LIMIT:
                return True
        return False

    base = Product.objects.filter(is_active=True)
    # B3: same cyfry → najpierw dopasowanie EAN (sztuka/karton/OPZ) — skan częściowego
    # kodu kreskowego też podpowiada. Nie-cyfrowe q omija tę gałąź (EAN-y są numeryczne).
    if q.isdigit():
        if _add(base.filter(Q(ean__startswith=q)
                            | Q(instructions__is_active=True,
                                instructions__carton__ean__startswith=q)
                            | Q(instructions__is_active=True,
                                instructions__inner_pack__ean__startswith=q))
                .distinct().order_by("code")):
            return JsonResponse({"results": results})
    # 1) kod od początku (najlepsze dla „od pierwszej litery"), 2) fragment kodu/nazwy.
    if _add(base.filter(code__istartswith=q).order_by("code")):
        return JsonResponse({"results": results})
    _add(base.filter(Q(code__icontains=q) | Q(name__icontains=q)).order_by("code"))
    return JsonResponse({"results": results})


@module_required("phv")
@require_POST
def phv_report(request):
    """Zgłoszenie błędu master daty: zapis + mail na PHV_ISSUE_EMAIL (best-effort)."""
    ref = (request.POST.get("ref_code") or "").strip()[:50]
    itype = request.POST.get("issue_type")
    if not ref or itype not in dict(PackagingIssue.TYPES) or itype in _RETIRED_ISSUE_TYPES:
        messages.error(request, "Podaj REF i typ zgłoszenia.")
        return redirect("ui:phv_home")
    description = (request.POST.get("description") or "").strip()[:500]
    correct_value = (request.POST.get("correct_value") or "").strip()[:80]

    # Walidacja pól wymaganych per typ (formularz pokazuje tylko właściwe pola; backend
    # jest strażnikiem, gdyby ktoś wysłał POST z pominięciem UI).
    def _back():
        return redirect(f"{request.path.replace('report/', '')}?q={ref}")
    if itype == "change_location" and correct_value not in dict(LOCATION_SUGGESTIONS):
        messages.error(request, "Wybierz lokalizację z listy.")
        return _back()
    if itype == "missing_process" and correct_value not in dict(PROCESS_CHOICES):
        messages.error(request, "Wybierz proces magazynowy.")
        return _back()
    if itype == "other" and not description:
        messages.error(request, 'Opisz problem — pole „Opis" jest wymagane.')
        return _back()
    # B6: zmiana paletyzacji wymaga powodu ze słownika (E1); „inny" wymaga opisu.
    if itype == "change_palletization":
        if correct_value not in dict(CHANGE_PALLETIZATION_REASONS):
            messages.error(request, "Wybierz powód zmiany paletyzacji.")
            return _back()
        if correct_value == "other" and not description:
            messages.error(request, "Opisz powód zmiany paletyzacji.")
            return _back()
        correct_value = dict(CHANGE_PALLETIZATION_REASONS)[correct_value]
    # „Brak renderu": sens zgłoszenia to fotodokumentacja — zdjęcie obowiązkowe.
    if itype == "missing_render" and not request.FILES.get("photo"):
        messages.error(request, "Dołącz zdjęcie jednostki — to zgłoszenie służy do uzupełnienia bazy zdjęć.")
        return _back()
    # „Brak przelicznika": trzeba wskazać, której jednostki dotyczy (PAZ/KAR/OPZ).
    unit = (request.POST.get("unit") or "").strip().upper()[:8]
    if itype == "missing_conversion" and unit not in MISSING_CONVERSION_UNITS:
        messages.error(request, "Wskaż jednostkę, której dotyczy brak przelicznika (PAZ / KAR / OPZ).")
        return _back()
    # „Zmień lokalizację": zapisz czytelną etykietę, nie kod techniczny.
    if itype == "change_location":
        correct_value = dict(LOCATION_SUGGESTIONS)[correct_value]

    photo = request.FILES.get("photo")
    if not _valid_image(photo):                       # weryfikacja realnego obrazu (nie nagłówka)
        messages.error(request, "Zdjęcie: prawdziwy obraz (JPG/PNG) do 12 MB.")
        return _back()
    issue = PackagingIssue.objects.create(
        ref_code=ref,
        product=resolve_ref(ref),
        issue_type=itype,
        description=description,
        current_value=(unit if itype == "missing_conversion"
                       else (request.POST.get("current_value") or "").strip()[:80]),
        correct_value=correct_value,
        photo=photo,
        reporter=request.user,
    )
    # Zdarzenie do n8n (best-effort) — triage zgłoszenia (Workflow C). Payload
    # minimalny: id + typ + REF. Opis/foto/klient NIE wychodzą (n8n czyta resztę
    # z systemu, a do Perplexity idzie tylko ogólny typ, nie treść zgłoszenia).
    from ..notifications import emit_event
    emit_event("matinfo_issue", {
        "issue_id": issue.pk, "issue_type": itype,
        "issue_type_display": issue.get_issue_type_display(), "ref_code": ref,
    })
    to = getattr(settings, "PHV_ISSUE_EMAIL", "")
    if to and getattr(settings, "EMAIL_HOST", ""):
        try:
            body = (f"Artykuł: {issue.product.name if issue.product else '—'} (REF: {ref})\n"
                    f"Typ: {issue.get_issue_type_display()}\n"
                    + (f"Aktualnie: {issue.current_value}\nPowinno być: {issue.correct_value}\n"
                       if issue.current_value or issue.correct_value else "")
                    + (f"Opis: {issue.description}\n" if issue.description else "")
                    + ("Foto: załączone w systemie\n" if issue.photo else "")
                    + f"Zgłaszający: {request.user.get_full_name() or request.user.get_username()}\n"
                    f"Status: {getattr(settings, 'SITE_BASE_URL', '')}/phv/?issue={issue.pk}")
            send_mail(f"[PHV] Nowe zgłoszenie #{issue.pk}: {issue.get_issue_type_display()} — {ref}",
                      body, None, [to], fail_silently=True)
        except Exception:
            pass          # mail best-effort — zgłoszenie i tak zapisane
    # Powiadom zespół in-app (bell) — natychmiastowa reakcja na problem. Zgłoszenia
    # optymalizacyjne (niewypełniony / dopasować do palety / za ciężki) trafiają do
    # operatora Optymalizacji kartonów i lądują w jego skrzynce; pozostałe do master daty.
    try:
        from ..notifications import notify, owner_users, optimizer_users
        if itype in OPTIMIZATION_ISSUE_TYPES:
            recipients = optimizer_users()
            url = f"/optymalizacja/?issue={issue.pk}"
        else:
            recipients = owner_users()
            url = f"/phv/?issue={issue.pk}"
        notify(recipients,
               f"Zgłoszenie hierarchii #{issue.pk}: {issue.get_issue_type_display()} — {ref}",
               (issue.description or issue.current_value or "")[:400],
               level="warning", url=url)
    except Exception:
        log.exception("Powiadomienie o zgłoszeniu hierarchii nie wysłane")
    messages.success(request, f"Problem zgłoszony. ID: #{issue.pk} — status sprawdzisz "
                              f"w „Moich zgłoszeniach”.")
    return _safe_referer(request, "ui:phv_home")   # walidacja hosta (open-redirect fix)


@module_required("phv")
@require_POST
def phv_location_report(request):
    """Zgłoszenie problemu FIZYCZNEGO lokalizacji (etykieta/belka/brak HU): zapis
    LocationIssue + mail na PHV_ISSUE_EMAIL + bell dla Master Data (best-effort).
    Kod trzymany jako string — lokalizacja bywa spoza aktywnej master daty."""
    code = (request.POST.get("location_code") or "").strip()[:50]
    itype = request.POST.get("issue_type")
    if not code or itype not in dict(LocationIssue.TYPES):
        messages.error(request, "Podaj lokalizację i typ zgłoszenia.")
        return redirect("ui:phv_home")
    photo = request.FILES.get("photo")
    if not _valid_image(photo):                       # weryfikacja realnego obrazu (nie nagłówka)
        messages.error(request, "Zdjęcie: prawdziwy obraz (JPG/PNG) do 12 MB.")
        return redirect(f"{request.path.replace('location-report/', '')}?q={code}")
    issue = LocationIssue.objects.create(
        location_code=code, issue_type=itype,
        description=(request.POST.get("description") or "").strip()[:500],
        photo=photo, reporter=request.user)
    # Zdarzenie do n8n (best-effort) — triage problemu lokalizacji (Workflow C).
    from ..notifications import emit_event
    emit_event("location_issue", {
        "issue_id": issue.pk, "issue_type": itype,
        "issue_type_display": issue.get_issue_type_display(), "location_code": code,
    })
    to = getattr(settings, "PHV_ISSUE_EMAIL", "")
    if to and getattr(settings, "EMAIL_HOST", ""):
        try:
            body = (f"Lokalizacja: {code}\n"
                    f"Typ: {issue.get_issue_type_display()}\n"
                    + (f"Opis: {issue.description}\n" if issue.description else "")
                    + ("Foto: załączone w systemie\n" if issue.photo else "")
                    + f"Zgłaszający: {request.user.get_full_name() or request.user.get_username()}\n")
            send_mail(f"[PHV] Problem lokalizacji #{issue.pk}: {issue.get_issue_type_display()} — {code}",
                      body, None, [to], fail_silently=True)
        except Exception:
            log.exception("Mail o problemie lokalizacji nie wysłany")
    try:
        from ..notifications import notify, owner_users
        notify(owner_users(),
               f"Problem lokalizacji #{issue.pk}: {issue.get_issue_type_display()} — {code}",
               (issue.description or "")[:400], level="warning", url=f"/phv/?q={code}")
    except Exception:
        log.exception("Powiadomienie o problemie lokalizacji nie wysłane")
    messages.success(request, f"Zgłoszono problem lokalizacji {code}. ID: #{issue.pk}.")
    return _safe_referer(request, "ui:phv_home")


@module_required("phv")
def phv_my_issues(request):
    """„Moje zgłoszenia" (ostatnie 30 dni): zgłoszenia materiału (PackagingIssue) I
    lokalizacji (LocationIssue) w jednej liście. Podgląd pojedynczego po ?issue=ID
    dotyczy zgłoszeń materiału (istniejące linki statusu)."""
    from datetime import timedelta
    issue_id = request.GET.get("issue")
    # IDOR fix: podgląd tylko WŁASNEGO zgłoszenia (bez reporter=user dowolny user czytał
    # cudze przez ?issue=<id> — opis, zdjęcie, dane, tożsamość zgłaszającego).
    single = (PackagingIssue.objects.filter(pk=issue_id, reporter=request.user).first()
              if issue_id and issue_id.isdigit() else None)
    since = timezone.now() - timedelta(days=30)
    status = request.GET.get("status")

    def _rows(qs, kind, label_attr, detail_fn):
        if status in dict(PackagingIssue.STATUS):    # te same statusy w obu modelach
            qs = qs.filter(status=status)
        return [{
            "pk": o.pk, "kind": kind, "label": getattr(o, label_attr),
            "type_display": o.get_issue_type_display(), "detail": detail_fn(o),
            "status": o.status, "created_at": o.created_at, "photo": o.photo,
        } for o in qs.filter(reporter=request.user, created_at__gte=since)]

    rows = (_rows(PackagingIssue.objects.all(), "material", "ref_code",
                  lambda o: (f"{o.current_value or '—'} → {o.correct_value or '—'}"
                             if o.current_value or o.correct_value else o.description))
            + _rows(LocationIssue.objects.all(), "location", "location_code",
                    lambda o: o.description))
    rows.sort(key=lambda r: r["created_at"], reverse=True)

    return render(request, "ui/phv/my_issues.html", {
        "issues": rows[:100], "single": single, "f_status": status or "",
        "statuses": PackagingIssue.STATUS,
    })


@_leader
def reports_admin(request):
    """Panel lidera: WSZYSTKIE zgłoszenia z przycisku „Zgłoś" — PackagingIssue (master data)
    i LocationIssue (lokalizacja), wszystkich userów, z filtrem statusu. Osobne od panelu
    wątków komunikatora (`messages_admin`) — tam są rozmowy, tu zgłoszenia z magazynu."""
    status = request.GET.get("status") or ""

    def _rows(qs, kind, label_attr, detail_fn):
        if status in dict(PackagingIssue.STATUS):     # te same statusy w obu modelach
            qs = qs.filter(status=status)
        return [{
            "pk": o.pk, "kind": kind, "label": getattr(o, label_attr),
            "type_display": o.get_issue_type_display(), "detail": detail_fn(o),
            "status": o.status, "status_display": o.get_status_display(),
            "created_at": o.created_at, "photo": o.photo,
            "reporter": o.reporter.username if o.reporter else "—",
        } for o in qs.select_related("reporter")]

    rows = (_rows(PackagingIssue.objects.all(), "material", "ref_code",
                  lambda o: (f"{o.current_value or '—'} → {o.correct_value or '—'}"
                             if o.current_value or o.correct_value else o.description))
            + _rows(LocationIssue.objects.all(), "location", "location_code",
                    lambda o: o.description))
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    open_count = (PackagingIssue.objects.filter(status="open").count()
                  + LocationIssue.objects.filter(status="open").count())
    return render(request, "ui/phv/reports_admin.html", {
        "rows": rows[:300], "f_status": status, "statuses": PackagingIssue.STATUS,
        "open_count": open_count,
    })

__all__ = [
    'phv_assistant',
    '_fuzzy_suggestions',
    'phv_home',
    'phv_suggest',
    'phv_report',
    'phv_location_report',
    'phv_my_issues',
    'reports_admin',
]
