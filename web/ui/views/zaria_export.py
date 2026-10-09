# ZARIA — wewnętrzny asystent AI (czat z modelami LLM). Employee-facing chat screens;
# admin configuration/usage-report screens live in admin.py.

from .core import (
    neutralize_workbook,
    module_required, get_object_or_404, render, HttpResponse, messages, redirect,
    require_POST, JsonResponse, json, settings
)
from django.urls import reverse
from django.utils import timezone

from ..models import (ZariaConfig,
                      ZariaConversation, ZariaMessage, ZariaMailLog)
from .. import zaria_llm
from ..zaria_llm import cost_pln, ZariaLLMError
from .zaria_budget import allowed_effort, _accessible_models, _check_budget_threshold, _check_global_budget_threshold, _month_spend_pln, _user_budget_pln, effective_max_tokens, trim_history  # noqa: F401
from ..platform_modules import can_open_module
from .zaria_chat import GUARD_HTTP_STATUS, ZARIA_MODULE_DENIED, _send_guards, _system_prompt_with_rag  # noqa: F401

MAILTO_MAX = 1800   # bezpieczny limit długości URL mailto: — powyżej sugerujemy .eml


def _mail_footer(model_name):
    """Stopka NIENEGOCJOWALNA — odbiorca musi wiedzieć, skąd pochodzi treść."""
    return (f"\n\n—\nWygenerowane przez asystenta AI ZARIA — model {model_name or '—'}, "
            f"data {timezone.localdate():%Y-%m-%d}. Treść wymaga weryfikacji przed użyciem.")


def _transcript(conv, scope):
    """(temat_domyślny, treść) dla zakresu: 'last' = ostatnia odpowiedź, 'all' = cała rozmowa.
    Odrzucone warianty porównania pomijamy."""
    msgs = [m for m in conv.messages.all() if not m.rejected]
    if scope == "last":
        last = next((m for m in reversed(msgs) if m.role == "assistant" and not m.error), None)
        body = last.content if last else ""
    else:
        parts = []
        for m in msgs:
            if m.role == "user":
                parts.append(f"[Pytanie]\n{m.content}")
            elif m.role == "assistant" and not m.error:
                parts.append(f"[ZARIA]\n{m.content}")
        body = "\n\n".join(parts)
    model_name = conv.model.display_name if conv.model else ""
    return (conv.title or "Rozmowa ZARIA"), body + _mail_footer(model_name)


@module_required("zaria")
def zaria_mail(request, pk):
    """GET: formularz przygotowania maila. POST: loguje przygotowanie (ZariaMailLog) i zwraca
    albo plik .eml (fmt=eml), albo stronę z linkiem „Otwórz w Outlooku" (mailto:). Wysyła
    człowiek — ZARIA tylko przygotowuje wersję roboczą. Log = potencjalna droga wypływu danych."""
    conv = get_object_or_404(ZariaConversation, pk=pk, user=request.user)
    if request.method != "POST":
        return render(request, "ui/zaria/mail.html", {"conversation": conv,
                      "default_subject": conv.title or "Rozmowa ZARIA"})
    scope = request.POST.get("scope") if request.POST.get("scope") in ("last", "all") else "last"
    to = (request.POST.get("to") or "").strip()[:300]
    cc = (request.POST.get("cc") or "").strip()[:300]
    fmt = request.POST.get("fmt")
    subject_default, body = _transcript(conv, scope)
    subject = (request.POST.get("subject") or subject_default).strip()[:300]

    ZariaMailLog.objects.create(conversation=conv, user=request.user, to=to, cc=cc,
                                subject=subject, scope=scope,
                                transport="eml" if fmt == "eml" else "mailto")

    if fmt == "eml":
        from email.message import EmailMessage
        eml = EmailMessage()
        eml["To"] = to
        if cc:
            eml["Cc"] = cc
        eml["Subject"] = subject
        eml.set_content(body)
        resp = HttpResponse(bytes(eml), content_type="message/rfc822")
        resp["Content-Disposition"] = 'attachment; filename="zaria.eml"'
        return resp

    from urllib.parse import quote, urlencode
    params = {"subject": subject, "body": body}
    if cc:
        params["cc"] = cc
    mailto = f"mailto:{quote(to)}?{urlencode(params)}"
    too_long = len(mailto) > MAILTO_MAX
    if too_long:   # skróć treść w mailto — pełna wersja w .eml
        short = body[:1200] + "\n\n[…] Treść skrócona — pełna w załączniku .eml."
        params["body"] = short
        mailto = f"mailto:{quote(to)}?{urlencode(params)}"
    return render(request, "ui/zaria/mail.html", {"conversation": conv, "result": True,
                  "mailto": mailto, "too_long": too_long, "scope": scope, "subject": subject,
                  "to": to, "cc": cc})


@module_required("zaria")
def zaria_export(request, pk, fmt):
    """Eksport wątku: fmt=md → Markdown, fmt=pdf → PDF (WeasyPrint, lazy; fallback HTML)."""
    conv = get_object_or_404(ZariaConversation, pk=pk, user=request.user)
    lines = [f"# {conv.title or 'Rozmowa ZARIA'}", ""]
    for m in conv.messages.all():
        if m.rejected:
            continue
        if m.role == "user":
            lines.append(f"**Pytanie:** {m.content}\n")
        elif m.role == "assistant" and not m.error:
            lines.append(f"**ZARIA:** {m.content}\n")
    md = "\n".join(lines)
    if fmt == "md":
        resp = HttpResponse(md, content_type="text/markdown; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="zaria-{conv.pk}.md"'
        return resp
    # PDF przez WeasyPrint (lazy). Gdy brak biblioteki — zwróć HTML do druku (fallback).
    # escape() PRZED <br> — treść (w tym wyjście LLM) nie może wnosić HTML do eksportu,
    # który użytkownik otworzy lokalnie w przeglądarce (fallback .html).
    from django.utils.html import escape as _esc
    html = "<h1>{}</h1>{}".format(
        _esc(conv.title or "Rozmowa ZARIA"),
        "".join(f"<p><b>{'Pytanie' if m.role=='user' else 'ZARIA'}:</b> "
                f"{_esc(m.content or '').replace(chr(10), '<br>')}</p>"
                for m in conv.messages.all() if not m.rejected and not m.error and m.role in ('user', 'assistant')))
    try:
        from weasyprint import HTML
        pdf = HTML(string=f"<meta charset='utf-8'>{html}").write_pdf()
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="zaria-{conv.pk}.pdf"'
        return resp
    except Exception:
        resp = HttpResponse(f"<meta charset='utf-8'>{html}", content_type="text/html; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="zaria-{conv.pk}.html"'
        return resp


def _markdown_tables(text):
    """Wyciągnij tabele markdown z treści: lista tabel, tabela = lista wierszy
    (lista komórek). Wiersze separatora (|---|) pomijane."""
    import re
    tables, current = [], []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and len(s) > 1:
            if re.fullmatch(r"\|[\s:|-]+\|", s):
                continue   # separator nagłówka
            cells = [c.strip() for c in s[1:-1].split("|")]
            current.append(cells)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return [t for t in tables if len(t) >= 2]   # nagłówek + min. 1 wiersz danych


@module_required("zaria")
def zaria_msg_xlsx(request, msg_id):
    """Pobierz tabele z wiadomości asystenta jako plik .xlsx (openpyxl).
    Tylko właściciel rozmowy; wiele tabel → wiele arkuszy."""
    msg = get_object_or_404(ZariaMessage, pk=msg_id, conversation__user=request.user)
    tables = _markdown_tables(msg.content)
    if not tables:
        messages.error(request, "Ta wiadomość nie zawiera tabeli do eksportu.")
        return redirect("ui:zaria_conversation", pk=msg.conversation_id)
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    wb.remove(wb.active)
    for i, table in enumerate(tables, start=1):
        ws = wb.create_sheet(title=f"Tabela {i}")
        for row in table:
            # Liczby (także z przecinkiem dziesiętnym) zapisujemy jako liczby.
            out = []
            for c in row:
                v = c.replace(",", ".", 1) if c.count(",") == 1 else c
                try:
                    out.append(int(v) if v.isdigit() else float(v))
                except ValueError:
                    out.append(c)
            ws.append(out)
        for cell in ws[1]:
            cell.font = Font(bold=True)
    buf = BytesIO()
    neutralize_workbook(wb)  # SEC-007
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="zaria-{msg.conversation_id}-{msg.pk}.xlsx"'
    return resp


# ── JSON API: POST /api/chat ──────────────────────────────────────────────────
# Session-authenticated (nie per-IP), CSRF egzekwowane przez CsrfViewMiddleware
# (bez @csrf_exempt). Zwraca czytelne komunikaty PL + poprawne kody HTTP.

def _sanitize_history(raw, config):
    """Historia od klienta → tylko role user/assistant z tekstową treścią; twarde
    ograniczenie długości (ochrona przed nadużyciem kontekstu)."""
    limit = config.max_message_chars or 8000
    out = []
    if isinstance(raw, list):
        for m in raw:
            if (isinstance(m, dict) and m.get("role") in ("user", "assistant")
                    and isinstance(m.get("content"), str) and m["content"].strip()):
                out.append({"role": m["role"], "content": m["content"][:limit]})
    return out


@require_POST
def api_chat(request):
    """POST /api/chat — {message, model?, history?} → {reply, model, usage}.
    Uwierzytelnianie sesją; walidacja, limity, budżet i błędy dostawcy zwracane
    jako JSON z odpowiednim kodem HTTP i komunikatem po polsku. Bramka modułu 'zaria'
    i akceptacja RODO — te same co w czacie WWW (wspólny _send_guards, audyt SEC-008)."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Wymagane zalogowanie."}, status=401)
    # SEC-008: odebrany moduł (nadpisanie per-użytkownik) / rola bez modułu → 403 przed
    # czymkolwiek innym (nie tworzymy nawet wątku „(API)"). _send_guards sprawdza ponownie.
    if not can_open_module(request.user, "zaria"):
        return JsonResponse({"error": ZARIA_MODULE_DENIED}, status=403)

    config = ZariaConfig.load()
    try:
        payload = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"error": "Nieprawidłowe dane wejściowe (oczekiwano JSON)."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Nieprawidłowe dane wejściowe."}, status=400)

    content = payload.get("message")

    # Model: po kluczu lub id, spośród dostępnych dla roli użytkownika (inaczej 403).
    accessible = _accessible_models(request.user)
    requested = payload.get("model")
    if requested:
        model = next((m for m in accessible if m.key == requested or str(m.id) == str(requested)), None)
        if model is None:
            return JsonResponse(
                {"error": "Brak dostępu do wybranego modelu lub model nie istnieje."}, status=403)
    else:
        model = config.default_model if config.default_model in accessible else (accessible[0] if accessible else None)
        if model is None:
            return JsonResponse({"error": "Brak dostępnych modeli ZARIA dla Twojej roli."}, status=403)

    # Wątek „(API)" = licznik zużycia i nośnik akceptacji RODO dla ścieżki API.
    conv, _ = ZariaConversation.objects.get_or_create(
        user=request.user, title="(API)", defaults={"model": model})
    budget = _user_budget_pln(request.user)
    code, err = _send_guards(request.user, content, config, model, conv)
    if code:
        body = {"error": err}
        if code == "privacy_notice":
            # Akceptacja jak w czacie WWW: człowiek czyta komunikat w wątku „(API)"
            # i klika „Rozumiem, kontynuuj" — API nie ma skrótu do zgody.
            body["privacy_notice_url"] = request.build_absolute_uri(
                reverse("ui:zaria_conversation", args=[conv.pk]))
        return JsonResponse(body, status=GUARD_HTTP_STATUS[code])
    content = content.strip()
    # Opcjonalny wysiłek (jak w czacie WWW): auto/low/medium/high, nieznane → auto.
    effort = allowed_effort(request.user, payload.get("effort"))

    # Historia z żądania + bieżąca wiadomość, obcięte do ostatnich 10 (system prompt osobno).
    api_messages = trim_history(_sanitize_history(payload.get("history"), config)
                                + [{"role": "user", "content": content}])
    try:
        text, ptok, ctok, latency = zaria_llm.complete(
            model, api_messages, _system_prompt_with_rag(model, config, content, user=request.user),
            max_tokens=effective_max_tokens(config), effort=effort)
    except ZariaLLMError as exc:
        return JsonResponse({"error": str(exc) or "Błąd usługi AI."}, status=502)

    # Licznik zużycia (per user). Treść zapisywana tylko przy LOG_CONVERSATIONS=true.
    log_content = getattr(settings, "ZARIA_LOG_CONVERSATIONS", False)
    ZariaMessage.objects.create(conversation=conv, role="user", model=model,
                                content=content if log_content else "")
    cost = cost_pln(model, ptok, ctok)
    ZariaMessage.objects.create(
        conversation=conv, role="assistant", model=model,
        content=text if log_content else "",
        prompt_tokens=ptok, completion_tokens=ctok, cost_pln=cost, latency_ms=latency)
    conv.save(update_fields=["updated_at"])
    _check_budget_threshold(request.user, _month_spend_pln(request.user), budget)
    _check_global_budget_threshold()

    return JsonResponse({
        "reply": text, "model": model.key,
        "usage": {"input_tokens": ptok, "output_tokens": ctok, "cost_pln": float(cost)},
    })


# Publiczny kontrakt modułu — tylko widoki wpięte w urls.py (helpery budżetowo-
# limitowe zostają prywatne dla płaskiej przestrzeni `views`).

__all__ = [
    'MAILTO_MAX',
    '_mail_footer',
    '_transcript',
    'zaria_mail',
    'zaria_export',
    '_markdown_tables',
    'zaria_msg_xlsx',
    '_sanitize_history',
    'api_chat',
]
