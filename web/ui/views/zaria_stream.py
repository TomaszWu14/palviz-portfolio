# ZARIA — wewnętrzny asystent AI (czat z modelami LLM). Employee-facing chat screens;
# admin configuration/usage-report screens live in admin.py.
import time

from .core import (json, logging, module_required, require_POST, get_object_or_404, JsonResponse)

from ..models import (ZariaConfig,
                      ZariaConversation, ZariaMessage)
from .. import zaria_llm
from ..zaria_llm import cost_pln, ZariaLLMError
from ..zaria_ratelimit import check_and_increment
from .zaria_budget import _check_budget_threshold, _check_global_budget_threshold, _month_spend_pln, _user_budget_pln, can_compare, can_use_zaria_model, effective_max_tokens, global_budget_exceeded, token_budget_exceeded, token_budget_for, trim_history, validate_message  # noqa: F401
from .zaria_chat import GUARD_HTTP_STATUS, _access_guard, _auto_pick_model, _send_guards, _system_prompt_with_rag  # noqa: F401


def _stream_response(user, conv, model, history, system, config, budget,
                     compare_group="", variant="", warnings=None):
    """Wspólny generator SSE (czat i porównanie). Streamuje `model`, na końcu (lub przy
    przerwaniu klienta) zapisuje ZariaMessage — z compare_group/variant, gdy to porównanie.
    Zwraca StreamingHttpResponse."""
    from django.http import StreamingHttpResponse

    def _sse(obj):
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    def _persist(text, ptok, ctok, latency, error=""):
        if not ctok and text:
            ctok = max(1, len(text) // 4)   # ponytail: ~4 znaki/token do korekty gdy dojdzie tokenizer
        cost = cost_pln(model, ptok, ctok)
        ZariaMessage.objects.create(
            conversation=conv, role="assistant", content=text, model=model,
            prompt_tokens=ptok, completion_tokens=ctok, cost_pln=cost,
            latency_ms=latency, error=error[:300],
            compare_group=compare_group, variant=variant)
        conv.save(update_fields=["updated_at"])
        _check_budget_threshold(user, _month_spend_pln(user), budget)
        _check_global_budget_threshold()
        return cost

    def event_stream():
        acc, ptok, ctok = [], 0, 0
        if warnings:   # pominięte załączniki (obraz/PDF bez vision, plik >10 MB) — nie po cichu
            yield _sse({"warning": " · ".join(warnings)})
        t0 = time.monotonic()
        try:
            for ev in zaria_llm.stream_complete(model, history, system, effective_max_tokens(config),
                                      effort=conv.effort):
                if ev[0] == "delta":
                    acc.append(ev[1])
                    yield _sse({"delta": ev[1]})
                elif ev[0] == "done":
                    ptok, ctok = ev[1], ev[2]
        except ZariaLLMError as exc:
            _persist("".join(acc), ptok, ctok, int((time.monotonic() - t0) * 1000), error=str(exc))
            yield _sse({"error": str(exc)})
            return
        except GeneratorExit:
            _persist("".join(acc), ptok, ctok, int((time.monotonic() - t0) * 1000))
            raise
        except Exception as exc:
            # Nieoczekiwany błąd mid-stream (spoza SDK/ZariaLLMError) NIE może zniknąć po cichu —
            # inaczej użytkownik widzi urwany strumień bez wyjaśnienia, a odpowiedź nie jest zapisana.
            logging.getLogger(__name__).exception("ZARIA stream failed mid-way")
            _persist("".join(acc), ptok, ctok, int((time.monotonic() - t0) * 1000),
                     error=f"{type(exc).__name__}: {exc}")
            yield _sse({"error": "Błąd usługi AI. Spróbuj ponownie później."})
            return
        latency = int((time.monotonic() - t0) * 1000)
        cost = _persist("".join(acc), ptok, ctok, latency)
        yield _sse({"done": True, "variant": variant, "prompt_tokens": ptok,
                    "completion_tokens": ctok, "cost_pln": float(cost), "latency_ms": latency})

    resp = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"   # nginx: nie buforuj SSE
    return resp


def _compare_history(conv):
    """Historia wątku do wysłania modelowi: role user + assistant bez odrzuconych; dla
    tury porównawczej z dwoma jeszcze niewybranymi wariantami bierzemy 'a' jako reprezentanta
    kontekstu (ponytail: prosty wybór, wystarcza — operator i tak zaraz wybiera)."""
    msgs = list(conv.messages.filter(role__in=["user", "assistant"]).order_by("created_at"))
    groups_with_a = {m.compare_group for m in msgs
                     if m.role == "assistant" and m.variant == "a" and not m.rejected and m.compare_group}
    out = []
    for m in msgs:
        if m.role == "assistant":
            if m.rejected:
                continue
            if m.variant == "b" and m.compare_group in groups_with_a:
                continue
        out.append({"role": m.role, "content": m.content})
    return out


@module_required("zaria")
@require_POST
def zaria_send_stream(request, pk):
    """SSE streaming odpowiedzi do okna czatu (zastępuje imitację). Body: content.
    Odpowiedź text/event-stream: `data: {"delta": "..."}` ... `data: {"done": true, ...}`
    albo `data: {"error": "..."}`. Zapis ZariaMessage na końcu; przerwanie po stronie
    klienta (przycisk „Zatrzymaj") zapisuje częściową odpowiedź z policzonymi tokenami.
    Bramki (walidacja/limit/budżet) współdzielone z POST i /api/chat przez _send_guards."""
    conv = get_object_or_404(ZariaConversation, pk=pk, user=request.user)
    content = (request.POST.get("content") or "").strip()
    config = ZariaConfig.load()

    # Moduł + RODO przed parsowaniem załączników (wspólny guard, SEC-008).
    code, err = _access_guard(request.user, conv, config)
    if code:
        return JsonResponse({"error": err}, status=GUARD_HTTP_STATUS[code])

    model = conv.model or _auto_pick_model(request.user, content) or config.default_model
    budget = _user_budget_pln(request.user)

    # Załączniki (PDF/obraz natywnie do Claude; Excel/Word → tekst). Treść pliku idzie tylko
    # do modelu; w bazie zostaje sam marker z nazwami (RODO). Bez plików — ścieżka bez zmian.
    files = request.FILES.getlist("files")
    media_blocks, extracted_text, names, _warn = [], "", [], []
    if files:
        from ..zaria_attachments import build_payload, last_message_content
        media_blocks, extracted_text, names, _warn = build_payload(files, model.provider if model else "")
        if not content:
            content = "Załączony plik."

    code, err = _send_guards(request.user, content, config, model, conv)
    if code:
        return JsonResponse({"error": err}, status=GUARD_HTTP_STATUS[code])

    stored = content + ("\n\n📎 " + ", ".join(names) if names else "")
    ZariaMessage.objects.create(conversation=conv, role="user", content=stored, model=model)
    if not conv.title:
        conv.title = content[:80]
    conv.save(update_fields=["title", "updated_at"])
    history = trim_history([{"role": m.role, "content": m.content}
              for m in conv.messages.filter(role__in=["user", "assistant"]).order_by("created_at")])
    if (media_blocks or extracted_text) and history:
        history[-1]["content"] = last_message_content(content, media_blocks, extracted_text)
    system = _system_prompt_with_rag(model, config, content, conv.system_prompt_template,
                                    request.user)
    return _stream_response(request.user, conv, model, history, system, config, budget,
                            warnings=_warn)


# ── Tryb porównania (F4): operator porównuje, system pokazuje oba wyniki ───────

@module_required("zaria")
@require_POST
def zaria_compare_start(request, pk):
    """Start tury porównania: waliduje oba modele (dostęp + budżet) i tworzy wiadomość
    użytkownika z nowym compare_group. Zwraca {group, a, b}. NIE wysyła zapytań — to robią
    dwa równoległe strumienie compare_stream. System nie ocenia — ocenia operator."""
    import uuid
    conv = get_object_or_404(ZariaConversation, pk=pk, user=request.user)
    if not can_compare(request.user):
        return JsonResponse({"error": "Brak uprawnienia do trybu porównania."}, status=403)
    ma, mb = conv.model, conv.model_b
    if not ma or not mb or ma.id == mb.id:
        return JsonResponse({"error": "Porównanie wymaga dwóch różnych modeli."}, status=400)
    content = (request.POST.get("content") or "").strip()
    config = ZariaConfig.load()
    if config.rodo_notice and conv.accepted_privacy_notice_at is None:
        return JsonResponse({"error": "Zaakceptuj komunikat RODO przed wysłaniem wiadomości."}, status=403)
    err = validate_message(content, config)
    if err:
        return JsonResponse({"error": err}, status=400)
    for m in (ma, mb):
        if not can_use_zaria_model(request.user, m):
            return JsonResponse({"error": f"Brak dostępu do modelu {m.display_name}."}, status=403)
    if not check_and_increment(request.user, config.rate_limit_per_minute, config.rate_limit_per_day):
        return JsonResponse({"error": "Przekroczono limit wiadomości. Spróbuj ponownie za chwilę."}, status=429)
    # Porównanie zużywa budżet OBU modeli — przy przekroczeniu blokujemy cały tryb.
    if token_budget_exceeded(request.user):
        return JsonResponse({"error": f"Przekroczono miesięczny limit tokenów ZARIA "
                             f"({token_budget_for(request.user)})."}, status=403)
    if global_budget_exceeded():
        return JsonResponse({"error": "Osiągnięto globalny miesięczny budżet ZARIA — "
                             "porównanie chwilowo zablokowane."}, status=403)
    group = uuid.uuid4().hex
    ZariaMessage.objects.create(conversation=conv, role="user", content=content,
                                model=ma, compare_group=group)
    if not conv.title:
        conv.title = content[:80]
    conv.save(update_fields=["title", "updated_at"])
    meta = lambda m: {"id": m.id, "name": m.display_name, "provider": m.provider,
                      "local": m.provider == "ollama"}
    return JsonResponse({"group": group, "a": meta(ma), "b": meta(mb)})


@module_required("zaria")
@require_POST
def zaria_compare_stream(request, pk):
    """Streamuje JEDNĄ stronę porównania (side=a|b) dla danego compare_group. Wywoływane
    dwukrotnie równolegle przez klienta. Zapisuje odpowiedź jako wariant a/b."""
    conv = get_object_or_404(ZariaConversation, pk=pk, user=request.user)
    group = (request.POST.get("group") or "").strip()
    side = request.POST.get("side")
    if side not in ("a", "b") or not group:
        return JsonResponse({"error": "Nieprawidłowe parametry porównania."}, status=400)
    if not conv.messages.filter(compare_group=group, role="user").exists():
        return JsonResponse({"error": "Nieznana tura porównania."}, status=404)
    if conv.messages.filter(compare_group=group, role="assistant", variant=side).exists():
        return JsonResponse({"error": "Ten wariant już wygenerowano."}, status=409)
    model = conv.model if side == "a" else conv.model_b
    if not model or not can_use_zaria_model(request.user, model):
        return JsonResponse({"error": "Brak dostępu do modelu."}, status=403)
    config = ZariaConfig.load()
    last_user = conv.messages.filter(compare_group=group, role="user").first()
    history = trim_history(_compare_history(conv))
    system = _system_prompt_with_rag(model, config, last_user.content if last_user else "",
                                    conv.system_prompt_template, request.user)
    budget = _user_budget_pln(request.user)
    return _stream_response(request.user, conv, model, history, system, config, budget,
                            compare_group=group, variant=side)


@module_required("zaria")
@require_POST
def zaria_compare_pick(request, pk):
    """Operator wybiera wariant (side=a|b): drugi wariant zostaje oznaczony jako odrzucony
    (zostaje w historii — Q: tura miała dwa warianty), a wątek kontynuuje wybranym modelem
    (koniec trybu porównania)."""
    conv = get_object_or_404(ZariaConversation, pk=pk, user=request.user)
    group = (request.POST.get("group") or "").strip()
    side = request.POST.get("side")
    if side not in ("a", "b") or not group:
        return JsonResponse({"error": "Nieprawidłowe parametry."}, status=400)
    chosen = conv.model if side == "a" else conv.model_b
    conv.messages.filter(compare_group=group, role="assistant").exclude(variant=side).update(rejected=True)
    conv.model = chosen
    conv.model_b = None
    conv.compare_mode = False
    conv.save(update_fields=["model", "model_b", "compare_mode", "updated_at"])
    return JsonResponse({"ok": True, "model": chosen.display_name if chosen else ""})


# ── F5: wysyłka mailem (mailto/.eml) + eksport MD/PDF ─────────────────────────

__all__ = [
    '_stream_response',
    '_compare_history',
    'zaria_send_stream',
    'zaria_compare_start',
    'zaria_compare_stream',
    'zaria_compare_pick',
]
