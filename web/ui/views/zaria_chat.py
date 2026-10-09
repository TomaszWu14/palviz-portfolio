# ZARIA — wewnętrzny asystent AI (czat z modelami LLM). Employee-facing chat screens;
# admin configuration/usage-report screens live in admin.py.

from .core import (
    module_required, render, require_POST, messages, redirect, get_object_or_404,
    JsonResponse, settings
)
from django.db.models import Sum, F
from django.utils import timezone

from ..models import (ZariaConfig,
                      ZariaConversation, ZariaMessage, ZariaSystemPromptTemplate)
from .. import zaria_llm
from ..zaria_llm import cost_pln, ZariaLLMError
from ..zaria_ratelimit import check_and_increment
from ..platform_modules import can_open_module
from .zaria_budget import allowed_effort, _accessible_models, _annotate_availability, _check_budget_threshold, _check_global_budget_threshold, _month_spend_pln, _saved_conversations, _sidebar_groups, _user_budget_pln, can_compare, can_use_zaria_model, effective_max_tokens, global_budget_exceeded, month_token_usage, token_budget_exceeded, token_budget_for, trim_history, validate_message  # noqa: F401

@module_required("zaria")
def zaria_home(request):
    """Module landing: the employee's conversation list + start-new-chat action.
    ?q= przeszukuje WŁASNE rozmowy (tytuły i treść wiadomości) — pełna prywatność."""
    from django.utils.translation import gettext_lazy as _
    q = (request.GET.get("q") or "").strip()
    prompt_tiles = [
        {"title": _("Napisz maila do dostawcy"), "desc": _("Uprzejma korespondencja B2B"),
         "tpl": _("Napisz uprzejmy e-mail do dostawcy w sprawie: ")},
        {"title": _("Przetłumacz na EN"), "desc": _("Zachowaj rzeczowy ton"),
         "tpl": _("Przetłumacz na język angielski, zachowując rzeczowy ton: ")},
        {"title": _("Podsumuj dokument"), "desc": _("Streszczenie w punktach"),
         "tpl": _("Streść poniższy tekst w 5 punktach: ")},
        {"title": _("Wyjaśnij błąd SAP"), "desc": _("Diagnoza komunikatu"),
         "tpl": _("Wyjaśnij, co oznacza ten komunikat/błąd SAP i jak go rozwiązać: ")},
    ]
    models = _annotate_availability(_accessible_models(request.user))
    return render(request, "ui/zaria/home.html", {
        "sidebar_groups": _sidebar_groups(request.user, q),
        "saved_conversations": _saved_conversations(request.user),
        "models": models,
        # Tryb porównania: A = zawsze model LOKALNY (Ollama), B = modele chmurowe.
        "models_local": [m for m in models if m.provider == "ollama"],
        "models_cloud": [m for m in models if m.provider != "ollama"],
        "prompt_tiles": prompt_tiles,
        "prompt_templates": list(ZariaSystemPromptTemplate.objects.filter(is_active=True)),
        "can_compare": can_compare(request.user),
        "q": q,
    })


@module_required("zaria")
def zaria_user_panel(request):
    """Panel użytkownika: własne zużycie (dziś / miesiąc / wykres 30 dni), pozostały
    budżet, lista przypisanych modeli, skróty do preferencji. Tylko dane własne."""
    from datetime import timedelta
    from django.db.models.functions import TruncDate
    user = request.user
    now = timezone.localtime()
    today = now.date()
    start = today - timedelta(days=29)
    rows = (ZariaMessage.objects.filter(conversation__user=user, role="assistant",
                                        created_at__date__gte=start)
            .annotate(d=TruncDate("created_at")).values("d")
            .annotate(spend=Sum("cost_pln"),
                      toks=Sum(F("prompt_tokens") + F("completion_tokens"))).order_by("d"))
    by_day = {r["d"]: r for r in rows}
    series, max_spend = [], 0.0
    for i in range(30):
        d = start + timedelta(days=i)
        sp = float(by_day.get(d, {}).get("spend") or 0)
        max_spend = max(max_spend, sp)
        series.append({"date": d, "spend": sp, "toks": by_day.get(d, {}).get("toks") or 0})
    for pt in series:
        pt["pct"] = int(round(pt["spend"] / max_spend * 100)) if max_spend else 0

    today_spend = float(by_day.get(today, {}).get("spend") or 0)
    month_spend = _month_spend_pln(user)
    budget = _user_budget_pln(user)
    remaining = (float(budget) - float(month_spend)) if budget else None
    usage_pct = int(min(round(float(month_spend) / float(budget) * 100), 100)) if budget else 0
    tok_used = month_token_usage(user)
    tok_budget = token_budget_for(user)
    return render(request, "ui/zaria/user_panel.html", {
        "sidebar_groups": _sidebar_groups(user),
        "saved_conversations": _saved_conversations(user),
        "series": series,
        "today_spend": today_spend,
        "month_spend": month_spend,
        "budget": budget,
        "remaining": remaining,
        "usage_pct": usage_pct,
        "tok_used": tok_used,
        "tok_budget": tok_budget,
        "models": _accessible_models(user),
        "can_compare": can_compare(user),
    })


@module_required("zaria")
@require_POST
def zaria_new_conversation(request):
    models = _accessible_models(request.user)
    if not models:
        messages.error(request, "Brak dostępnych modeli ZARIA dla Twojej roli.")
        return redirect("ui:zaria_home")
    by_id = {str(m.id): m for m in models}
    effort = allowed_effort(request.user, request.POST.get("effort"))

    # Tryb porównania: dwa różne modele, wymaga uprawnienia can_compare.
    if request.POST.get("compare") == "1" and can_compare(request.user):
        ma = by_id.get(request.POST.get("model_a"))
        mb = by_id.get(request.POST.get("model_b"))
        if not ma or not mb or ma.id == mb.id:
            messages.error(request, "Wybierz dwa różne modele do porównania.")
            return redirect("ui:zaria_home")
        # Porównanie = lokalny vs chmura: A musi być modelem lokalnym (Ollama), B chmurowym.
        if ma.provider != "ollama" or mb.provider == "ollama":
            messages.error(request, "W porównaniu Model A musi być lokalny (Ollama), a Model B chmurowy.")
            return redirect("ui:zaria_home")
        conv = ZariaConversation.objects.create(user=request.user, model=ma, model_b=mb,
                                                compare_mode=True, effort=effort)
        return redirect("ui:zaria_conversation", pk=conv.pk)

    model_id = request.POST.get("model")
    if model_id == "auto":
        model = None      # routing per wiadomość: tani → mocny (patrz _auto_pick_model)
    else:
        model = by_id.get(model_id) or ZariaConfig.load().default_model or models[0]
    # Uwaga: pusty select szablonu wysyła "" — filter(pk="") wywala 500 na PostgreSQL
    # (invalid input syntax for integer), choć SQLite to toleruje. Bierzemy tylko cyfry.
    tpl_id = (request.POST.get("template") or "").strip()
    template = (ZariaSystemPromptTemplate.objects.filter(pk=tpl_id, is_active=True).first()
                if tpl_id.isdigit() else None)
    conv = ZariaConversation.objects.create(user=request.user, model=model,
                                            system_prompt_template=template, effort=effort)
    return redirect("ui:zaria_conversation", pk=conv.pk)


def _auto_pick_model(user, content):
    """Routing tani→mocny (roadmapa, wywiad Q29): krótkie/proste pytania idą do
    najtańszego dostępnego modelu, dłuższe/złożone do najdroższego (proxy „mocy").
    ponytail: heurystyka długości+sygnałów zamiast klasyfikatora LLM — podnieść,
    gdy realne użycie pokaże błędne wybory."""
    models = _accessible_models(user)
    if not models:
        return None
    def _price(m):
        return float(m.price_input_per_1k + m.price_output_per_1k)
    cheap = min(models, key=_price)
    strong = max(models, key=_price)
    complex_signals = ("```" in content or len(content) > 600
                       or content.count("\n") > 8
                       or any(w in content.lower() for w in
                              ("przeanalizuj", "napisz raport", "zaplanuj", "porównaj",
                               "przetłumacz dokument", "wygeneruj")))
    return strong if complex_signals else cheap


@module_required("zaria")
def zaria_conversation(request, pk):
    conv = get_object_or_404(ZariaConversation, pk=pk)
    # Rozmowy są PRYWATNE (roadmapa, wywiad Q26): nawet admin/superuser nie czyta
    # cudzych rozmów — audyt obejmuje wyłącznie zbiorcze statystyki kosztów.
    if conv.user_id != request.user.id:
        return render(request, "ui/403.html", status=403)
    config = ZariaConfig.load()
    month_spend = _month_spend_pln(request.user)
    budget = _user_budget_pln(request.user)
    usage_pct = int(min(round(float(month_spend) / float(budget) * 100), 100)) if budget else 0
    msgs = list(conv.messages.all())
    ctx = {
        "conversation": conv,
        "msgs": msgs,
        "sidebar_groups": _sidebar_groups(request.user),
        "saved_conversations": _saved_conversations(request.user),
        "retention_warning": _retention_warning_days(conv, config),
        "models": _accessible_models(request.user),
        "needs_privacy_notice": conv.accepted_privacy_notice_at is None and bool(config.rodo_notice),
        "rodo_notice": config.rodo_notice,
        "month_spend": month_spend,
        "budget": budget,
        "usage_pct": usage_pct,
    }
    if conv.compare_mode:
        name_a = conv.model.display_name if conv.model else "A"
        name_b = conv.model_b.display_name if conv.model_b else "B"
        ctx["compare_turns"] = _compare_turns(msgs, name_a, name_b)
    return render(request, "ui/zaria/conversation.html", ctx)


def _compare_turns(msgs, name_a="A", name_b="B"):
    """Grupuje wiadomości trybu porównania w tury per compare_group; `cols` = lista
    (side, wiadomość, nazwa_modelu) do renderu dwóch kolumn (Q: tura miała dwa warianty)."""
    turns, index = [], {}
    for m in msgs:
        if not m.compare_group:
            continue
        t = index.get(m.compare_group)
        if t is None:
            t = {"user": None, "a": None, "b": None}
            index[m.compare_group] = t
            turns.append(t)
        if m.role == "user":
            t["user"] = m
        elif m.variant in ("a", "b"):
            t[m.variant] = m
    for t in turns:
        t["cols"] = [("a", t["a"], name_a), ("b", t["b"], name_b)]
    return turns


def _retention_warning_days(conv, config):
    """Ile dni zostało do usunięcia wątku przez retencję (None gdy nie grozi). Ostrzegamy
    na 7 dni przed. Zapisane wątki są wyłączone spod retencji."""
    days = config.retention_days or 0
    if not days or conv.is_saved:
        return None
    age = (timezone.now() - conv.updated_at).days if conv.updated_at else 0
    left = days - age
    return left if 0 < left <= 7 else (0 if left <= 0 else None)


@module_required("zaria")
@require_POST
def zaria_conv_action(request, pk):
    """Akcje na własnej rozmowie: zapis/odpięcie gwiazdki, przypięcie, tagi, zmiana nazwy,
    usunięcie. JSON {ok:true}. Tylko właściciel."""
    conv = get_object_or_404(ZariaConversation, pk=pk, user=request.user)
    action = request.POST.get("action")
    if action == "save":
        conv.is_saved = not conv.is_saved
        conv.save(update_fields=["is_saved", "updated_at"])
        return JsonResponse({"ok": True, "is_saved": conv.is_saved})
    if action == "pin":
        conv.pinned = not conv.pinned
        conv.save(update_fields=["pinned", "updated_at"])
        return JsonResponse({"ok": True, "pinned": conv.pinned})
    if action == "tags":
        conv.tags = (request.POST.get("tags") or "").strip()[:200]
        conv.save(update_fields=["tags", "updated_at"])
        return JsonResponse({"ok": True, "tags": conv.tags})
    if action == "rename":
        conv.title = (request.POST.get("title") or "").strip()[:200]
        conv.save(update_fields=["title", "updated_at"])
        return JsonResponse({"ok": True, "title": conv.title})
    if action == "delete":
        conv.delete()
        return JsonResponse({"ok": True, "deleted": True})
    return JsonResponse({"error": "Nieznana akcja."}, status=400)


@module_required("zaria")
@require_POST
def zaria_accept_privacy(request, pk):
    conv = get_object_or_404(ZariaConversation, pk=pk, user=request.user)
    conv.accepted_privacy_notice_at = timezone.now()
    conv.save(update_fields=["accepted_privacy_notice_at"])
    return redirect("ui:zaria_conversation", pk=pk)


ZARIA_MODULE_DENIED = "Brak dostępu do modułu ZARIA."
ZARIA_RODO_REQUIRED = "Zaakceptuj komunikat RODO przed wysłaniem wiadomości."
# Kod bramki → status HTTP dla ścieżek JSON (SSE i /api/chat) — jedna mapa dla obu.
GUARD_HTTP_STATUS = {"forbidden_module": 403, "privacy_notice": 403, "bad_request": 400,
                     "forbidden_model": 403, "rate_limited": 429, "token_budget": 403,
                     "global_budget": 403}


def _access_guard(user, conv, config):
    """Bramka DOSTĘPU (audyt SEC-008), wspólna dla czatu WWW, SSE i /api/chat: moduł
    'zaria' (rola + nadpisanie per-użytkownik — jak @module_required) i akceptacja
    komunikatu RODO w wątku (`conv=None` = brak akceptacji). Bez skutków ubocznych —
    odmowa nie zużywa rate-limitu. Zwraca (kod, komunikat) albo (None, None)."""
    if not can_open_module(user, "zaria"):
        return "forbidden_module", ZARIA_MODULE_DENIED
    if config.rodo_notice and (conv is None or conv.accepted_privacy_notice_at is None):
        return "privacy_notice", ZARIA_RODO_REQUIRED
    return None, None


def _send_guards(user, content, config, model, conv):
    """Wspólna bramka wysyłki (czat WWW, SSE i /api/chat): dostęp do modułu + RODO
    (_access_guard, SEC-008), walidacja treści, dostęp do modelu, rate-limit,
    miesięczny limit tokenów, globalny budżet. Zwraca (kod, komunikat) albo
    (None, None) gdy wolno wysłać. Jedno miejsce = ścieżki nie mogą się rozjechać
    (code review PR #254). Per-user PLN nie blokuje (P13) — tylko ostrzega po
    wysyłce; twardy cap pieniężny jest globalny, per-user twardy limit to TOKENY."""
    code, err = _access_guard(user, conv, config)
    if code:
        return code, err
    err = validate_message(content, config)
    if err:
        return "bad_request", err
    if not model or not can_use_zaria_model(user, model):
        return "forbidden_model", "Brak dostępu do wybranego modelu ZARIA."
    if not check_and_increment(user, config.rate_limit_per_minute, config.rate_limit_per_day):
        return "rate_limited", "Przekroczono limit wiadomości. Spróbuj ponownie za chwilę."
    if token_budget_exceeded(user):
        return "token_budget", (f"Przekroczono miesięczny limit tokenów ZARIA "
                                f"({token_budget_for(user)}). Reset 1. dnia miesiąca.")
    if global_budget_exceeded():
        contact = getattr(settings, "ADMIN_CONTACT_EMAIL", "")
        return "global_budget", ("Osiągnięto globalny miesięczny budżet ZARIA — wysyłka "
                                 "chwilowo zablokowana. Skontaktuj się z administratorem"
                                 + (f": {contact}" if contact else "."))
    return None, None


def _system_prompt_with_rag(model, config, content, template=None, user=None):
    """Prompt systemowy + (gdy włączone) blok danych GROOVE dopasowany do pytania
    (RAG-lite, tylko odczyt). `template` (F5) nadpisuje prompt modelu/globalny dla wątku.
    RAG filtruje źródła wg modułów dostępnych dla `user` (bez usera → brak danych)."""
    system = template.body if template else model.effective_system_prompt()
    if config.rag_enabled:
        from ..zaria_rag import context_for
        rag = context_for(content, user)
        if rag:
            system += "\n\n" + rag
    return system


@module_required("zaria")
@require_POST
def zaria_send_message(request, pk):
    conv = get_object_or_404(ZariaConversation, pk=pk, user=request.user)
    content = (request.POST.get("content") or "").strip()
    config = ZariaConfig.load()

    # conv.model=None ⇒ rozmowa w trybie AUTO — routing per wiadomość.
    model = conv.model or _auto_pick_model(request.user, content) or config.default_model
    budget = _user_budget_pln(request.user)
    # RODO (i moduł) sprawdza _send_guards jako PIERWSZE — ten sam komunikat co /api/chat.
    code, err = _send_guards(request.user, content, config, model, conv)
    if code:
        messages.error(request, err)
        return redirect("ui:zaria_conversation", pk=pk)

    ZariaMessage.objects.create(conversation=conv, role="user", content=content, model=model)
    if not conv.title:
        conv.title = content[:80]

    history = trim_history([{"role": m.role, "content": m.content}
              for m in conv.messages.filter(role__in=["user", "assistant"]).order_by("created_at")])
    try:
        text, prompt_tokens, completion_tokens, latency_ms = zaria_llm.complete(
            model, history, _system_prompt_with_rag(model, config, content, conv.system_prompt_template,
                                    request.user),
            max_tokens=effective_max_tokens(config), effort=conv.effort)
        cost = cost_pln(model, prompt_tokens, completion_tokens)
        ZariaMessage.objects.create(
            conversation=conv, role="assistant", content=text, model=model,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            cost_pln=cost, latency_ms=latency_ms)
    except ZariaLLMError as exc:
        ZariaMessage.objects.create(conversation=conv, role="assistant", content="",
                                    model=model, error=str(exc)[:300])
        messages.error(request, "Błąd wywołania modelu AI. Spróbuj ponownie.")

    conv.save(update_fields=["title", "updated_at"])
    _check_budget_threshold(request.user, _month_spend_pln(request.user), budget)
    _check_global_budget_threshold()
    return redirect("ui:zaria_conversation", pk=pk)

__all__ = [
    'zaria_home',
    'zaria_user_panel',
    'zaria_new_conversation',
    '_auto_pick_model',
    'zaria_conversation',
    '_compare_turns',
    '_retention_warning_days',
    'zaria_conv_action',
    'zaria_accept_privacy',
    'ZARIA_MODULE_DENIED',
    'ZARIA_RODO_REQUIRED',
    'GUARD_HTTP_STATUS',
    '_access_guard',
    '_send_guards',
    '_system_prompt_with_rag',
    'zaria_send_message',
]
