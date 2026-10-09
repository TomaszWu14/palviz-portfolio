"""Server-side LLM call path for ZARIA. The vendor API key never reaches the
client — this module is the only place that reads it and makes the HTTP call.

Modeled on `powerbi.py`'s `_cfg()`/`is_configured()` shape rather than its
MSAL/token-cache machinery: a vendor API key is a static server secret, no
OAuth flow needed. Pricing lives on `ZariaModel` (admin-edited, already in
PLN) — no runtime currency conversion here.
"""
import logging
import re
import time

from django.conf import settings


def _cfg(name, default=""):
    return getattr(settings, name, default) or default


def is_configured(provider):
    """True when the server has credentials to call `provider`."""
    if provider == "anthropic":
        return bool(_cfg("ZARIA_ANTHROPIC_API_KEY"))
    if provider == "openai":
        return bool(_cfg("ZARIA_OPENAI_API_KEY"))
    if provider == "azure_openai":
        return bool(_cfg("ZARIA_AZURE_OPENAI_API_KEY") and _cfg("ZARIA_AZURE_OPENAI_ENDPOINT"))
    if provider == "ollama":
        return bool(_cfg("ZARIA_OLLAMA_BASE_URL"))
    return False


# Filtr PII przed wysyłką do dostawcy CHMUROWEGO (Ollama = lokalnie, bez maskowania).
# Cyfry sklejone z literą/myślnikiem albo dłuższe ciągi (EAN-13, SSCC-18, kody HU)
# nie pasują — lookaroundy wymagają granicy ciągu. Zapis w bazie bez zmian.
# ponytail: regex, nie NER — nazwiska/adresy przechodzą; podnieść, gdy audyt tego zażąda.
# Gołe 9 cyfr to częściej nr SAP (dostawa/klient/materiał) niż telefon — maskujemy je
# tylko po słowie kluczowym; inaczej telefon = prefiks +48/0048 albo grupy z separatorami.
_END = r"(?![\w-]|[ -]\d)"
_PII = [
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[e-mail]"),
    (re.compile(r"(?<![\w+])(?:\+|00)48(?:[ .()-]*\d){9}" + _END), "[telefon]"),
    (re.compile(r"(?<![\w+-])(?<!\d[ -])(?:\d{3}[ -]\d{3}[ -]\d{3}|\d{2}[ -]\d{3}[ -]\d{2}[ -]\d{2})"
                + _END), "[telefon]"),
    (re.compile(r"(?<![\w+-])\d{11}(?![\w-])"), "[PESEL]"),
]
_PHONE_KW = re.compile(r"(?i)\b(telefon|tel|komórka|kom|phone|mobile)\b([^\d\n]{0,15}?)"
                       r"(?<!\d)\d{9}(?![\w-])")


def mask_pii(text):
    for rx, label in _PII:
        text = rx.sub(label, text)
    return _PHONE_KW.sub(r"\1\2[telefon]", text)


def _mask_payload(provider, messages, system_prompt):
    """(messages, system_prompt) z zamaskowanym PII dla dostawców chmurowych.
    Treść wiadomości bywa listą bloków (załączniki) — maskujemy bloki tekstowe."""
    if provider == "ollama":
        return messages, system_prompt

    def _content(c):
        if isinstance(c, str):
            return mask_pii(c)
        if isinstance(c, list):
            return [{**b, "text": mask_pii(b["text"])}
                    if isinstance(b, dict) and isinstance(b.get("text"), str) else b
                    for b in c]
        return c
    return ([{**m, "content": _content(m.get("content"))} for m in messages],
            mask_pii(system_prompt or ""))


class ZariaLLMError(Exception):
    """Raised on any vendor/network failure — the caller records it on the
    ZariaMessage row and shows a friendly message, never a 500."""


def _max_retries():
    try:
        return int(_cfg("ZARIA_MAX_RETRIES", 3))
    except (TypeError, ValueError):
        return 3


def _thinking_params(zaria_model, effort, max_tokens):
    """Parametry rozszerzonego myślenia dla Anthropic wg wysiłku rozmowy.
    Zwraca (extra_kwargs, max_tokens). effort=auto → bez zmian. Haiku nie wspiera
    `effort` — pomijamy. Dla pozostałych: adaptive thinking + output_config.effort;
    myślenie liczy się do max_tokens, więc podnosimy sufit, by nie ucinać odpowiedzi."""
    if effort in ("low", "medium", "high") and "haiku" not in (zaria_model.key or ""):
        return ({"thinking": {"type": "adaptive"},
                 "output_config": {"effort": effort}}, max(max_tokens, 16000))
    return ({}, max_tokens)


def complete(zaria_model, messages, system_prompt, max_tokens=1000, effort="auto"):
    """Call the vendor for `zaria_model` (a ZariaModel row) with `messages`
    (list of {"role": "user"|"assistant", "content": str}, oldest first, already
    trimmed by the caller) and `system_prompt`. `max_tokens` caps the reply.
    Returns (reply_text, prompt_tokens, completion_tokens, latency_ms). Raises
    ZariaLLMError (with a Polish, user-safe message) on any vendor/network error —
    never leaks the API key or a raw 500."""
    if not is_configured(zaria_model.provider):
        raise ZariaLLMError(f"Dostawca '{zaria_model.provider}' nie jest skonfigurowany na serwerze.")
    messages, system_prompt = _mask_payload(zaria_model.provider, messages, system_prompt)

    t0 = time.monotonic()
    try:
        if zaria_model.provider == "anthropic":
            text, prompt_tokens, completion_tokens = _complete_anthropic(
                zaria_model, messages, system_prompt, max_tokens, effort)
        elif zaria_model.provider in ("openai", "azure_openai", "ollama"):
            # Ollama serwuje endpoint zgodny z OpenAI (/v1/chat/completions) —
            # ten sam klient, inny base_url, klucz nieużywany.
            text, prompt_tokens, completion_tokens = _complete_openai(
                zaria_model, messages, system_prompt, max_tokens)
        else:
            raise ZariaLLMError(f"Nieobsługiwany dostawca: {zaria_model.provider}")
    except ZariaLLMError:
        raise
    except Exception as exc:  # last-resort catch — translate, never surface raw/secret-bearing text
        raise ZariaLLMError("Błąd usługi AI. Spróbuj ponownie później.") from exc
    latency_ms = int((time.monotonic() - t0) * 1000)
    return text, prompt_tokens, completion_tokens, latency_ms


# Vendor errors → friendly Polish. Kept out of the message string is any raw exception
# text (which could echo request details); we map by type only.
def _anthropic_friendly(anthropic, exc):
    if isinstance(exc, anthropic.APITimeoutError):
        return "Model nie odpowiedział w wyznaczonym czasie. Spróbuj ponownie."
    if isinstance(exc, anthropic.RateLimitError):
        return "Usługa AI jest chwilowo przeciążona (limit zapytań). Spróbuj za chwilę."
    if isinstance(exc, anthropic.APIStatusError):
        if getattr(exc, "status_code", None) == 529:
            return "Usługa AI jest przeciążona. Spróbuj ponownie za chwilę."
        return "Błąd usługi AI. Spróbuj ponownie później."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Nie udało się połączyć z usługą AI. Spróbuj ponownie."
    return None


def _complete_anthropic(zaria_model, messages, system_prompt, max_tokens, effort="auto"):
    import anthropic

    # max_retries → SDK retries 429/529/timeout with exponential backoff before raising.
    client = anthropic.Anthropic(
        api_key=_cfg("ZARIA_ANTHROPIC_API_KEY"),
        timeout=float(_cfg("ZARIA_REQUEST_TIMEOUT_SEC", 60)),
        max_retries=_max_retries(),
    )
    # System prompt as a cache_control block → Anthropic prompt caching (shared prompt
    # across many users ⇒ big token/cost saving on the cached prefix).
    if system_prompt:
        system_param = [{"type": "text", "text": system_prompt,
                         "cache_control": {"type": "ephemeral"}}]
    else:
        system_param = anthropic.NOT_GIVEN
    extra, max_tokens = _thinking_params(zaria_model, effort, max_tokens)
    try:
        resp = client.messages.create(
            model=zaria_model.key,
            system=system_param,
            messages=messages,
            max_tokens=max_tokens,
            **extra,
        )
    except anthropic.APIError as exc:
        raise ZariaLLMError(_anthropic_friendly(anthropic, exc)
                            or "Błąd usługi AI. Spróbuj ponownie później.") from exc
    text = "".join(block.text for block in resp.content if block.type == "text")
    return text, resp.usage.input_tokens, resp.usage.output_tokens


def _complete_openai(zaria_model, messages, system_prompt, max_tokens):
    import openai

    common = dict(timeout=float(_cfg("ZARIA_REQUEST_TIMEOUT_SEC", 60)), max_retries=_max_retries())
    if zaria_model.provider == "azure_openai":
        client = openai.AzureOpenAI(
            api_key=_cfg("ZARIA_AZURE_OPENAI_API_KEY"),
            azure_endpoint=_cfg("ZARIA_AZURE_OPENAI_ENDPOINT"),
            api_version="2024-10-21", **common)
    elif zaria_model.provider == "ollama":
        client = openai.OpenAI(api_key="ollama",   # Ollama ignoruje klucz, SDK go wymaga
                               base_url=_cfg("ZARIA_OLLAMA_BASE_URL"), **common)
    else:
        client = openai.OpenAI(api_key=_cfg("ZARIA_OPENAI_API_KEY"), **common)
    chat_messages = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages
    try:
        resp = client.chat.completions.create(
            model=zaria_model.key, messages=chat_messages, max_tokens=max_tokens)
    except openai.APIError as exc:
        raise ZariaLLMError("Błąd usługi AI. Spróbuj ponownie później.") from exc
    text = (resp.choices[0].message.content or "") if resp.choices else ""
    # Ollama może nie zwrócić `usage` — nie gub poprawnej odpowiedzi (jak w ścieżce stream).
    usage = getattr(resp, "usage", None)
    return (text,
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0)


def cost_pln(zaria_model, prompt_tokens, completion_tokens):
    """Cost of one exchange in PLN, from the admin-edited per-1k prices."""
    from decimal import Decimal
    return (Decimal(prompt_tokens) / 1000) * zaria_model.price_input_per_1k \
        + (Decimal(completion_tokens) / 1000) * zaria_model.price_output_per_1k


# ── Streaming (SSE) ───────────────────────────────────────────────────────────
# Dyspozytor jak `complete()`, ale zwraca generator zdarzeń. Zdarzenia:
#   ("delta", tekst)                 — kolejny fragment odpowiedzi
#   ("done", prompt_tokens, completion_tokens)  — końcowe zużycie
# Wołający (widok SSE) skleja tekst i zapisuje ZariaMessage na "done" albo przy
# przerwaniu (GeneratorExit). ponytail: dyspozytor funkcyjny zamiast ABC —
# polimorfizm realnie potrzebny dopiero przy trybie porównania (F4); podnieść wtedy.

def stream_complete(zaria_model, messages, system_prompt, max_tokens=1000, effort="auto"):
    """Yield zdarzeń streamingu dla `zaria_model`. Rzuca ZariaLLMError (PL) przy
    błędzie dostawcy — nigdy nie wycieka klucza ani surowego 500."""
    if not is_configured(zaria_model.provider):
        raise ZariaLLMError(f"Dostawca '{zaria_model.provider}' nie jest skonfigurowany na serwerze.")
    messages, system_prompt = _mask_payload(zaria_model.provider, messages, system_prompt)
    if zaria_model.provider == "anthropic":
        yield from _stream_anthropic(zaria_model, messages, system_prompt, max_tokens, effort)
    elif zaria_model.provider in ("openai", "azure_openai", "ollama"):
        yield from _stream_openai(zaria_model, messages, system_prompt, max_tokens)
    else:
        raise ZariaLLMError(f"Nieobsługiwany dostawca: {zaria_model.provider}")


def _stream_anthropic(zaria_model, messages, system_prompt, max_tokens, effort="auto"):
    import anthropic
    client = anthropic.Anthropic(
        api_key=_cfg("ZARIA_ANTHROPIC_API_KEY"),
        timeout=float(_cfg("ZARIA_REQUEST_TIMEOUT_SEC", 60)),
        max_retries=_max_retries())
    if system_prompt:
        system_param = [{"type": "text", "text": system_prompt,
                         "cache_control": {"type": "ephemeral"}}]
    else:
        system_param = anthropic.NOT_GIVEN
    extra, max_tokens = _thinking_params(zaria_model, effort, max_tokens)
    try:
        with client.messages.stream(model=zaria_model.key, system=system_param,
                                    messages=messages, max_tokens=max_tokens, **extra) as stream:
            for text in stream.text_stream:
                yield ("delta", text)
            final = stream.get_final_message()
            yield ("done", final.usage.input_tokens, final.usage.output_tokens)
    except anthropic.APIError as exc:
        raise ZariaLLMError(_anthropic_friendly(anthropic, exc)
                            or "Błąd usługi AI. Spróbuj ponownie później.") from exc


def _stream_openai(zaria_model, messages, system_prompt, max_tokens):
    import openai
    common = dict(timeout=float(_cfg("ZARIA_REQUEST_TIMEOUT_SEC", 60)), max_retries=_max_retries())
    if zaria_model.provider == "azure_openai":
        client = openai.AzureOpenAI(api_key=_cfg("ZARIA_AZURE_OPENAI_API_KEY"),
                                    azure_endpoint=_cfg("ZARIA_AZURE_OPENAI_ENDPOINT"),
                                    api_version="2024-10-21", **common)
    elif zaria_model.provider == "ollama":
        client = openai.OpenAI(api_key="ollama", base_url=_cfg("ZARIA_OLLAMA_BASE_URL"), **common)
    else:
        client = openai.OpenAI(api_key=_cfg("ZARIA_OPENAI_API_KEY"), **common)
    chat_messages = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages
    # include_usage → zużycie w ostatnim chunku (Ollama może go nie dać → 0, licznik best-effort).
    try:
        resp = client.chat.completions.create(
            model=zaria_model.key, messages=chat_messages, max_tokens=max_tokens,
            stream=True, stream_options={"include_usage": True})
    except openai.APIError as exc:
        raise ZariaLLMError("Błąd usługi AI. Spróbuj ponownie później.") from exc
    usage = None
    try:
        for chunk in resp:
            if chunk.usage:
                usage = chunk.usage
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                yield ("delta", chunk.choices[0].delta.content)
    except openai.APIError as exc:
        raise ZariaLLMError("Błąd usługi AI. Spróbuj ponownie później.") from exc
    yield ("done", getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0)


def provider_health(provider):
    """Lekki health-check dostawcy → (ok: bool, detail: str). Anthropic/OpenAI/Azure:
    'skonfigurowany' = klucz obecny (bez płatnego pingu). Ollama: HTTP GET na base_url
    (lokalny zasób — realny ping tani). Wołane z cache 60 s (patrz views)."""
    if not is_configured(provider):
        return False, "Brak konfiguracji na serwerze"
    if provider == "ollama":
        import urllib.request
        base = _cfg("ZARIA_OLLAMA_BASE_URL").rstrip("/").removesuffix("/v1")
        try:
            with urllib.request.urlopen(base, timeout=3) as r:
                return (200 <= r.status < 500), ("dostępny" if 200 <= r.status < 500
                                                 else "serwer modelu zwrócił błąd")
        except Exception:
            logging.getLogger(__name__).warning("Health-check Ollama nieudany (%s)", base, exc_info=True)
            return False, "serwer modelu nie odpowiada"
    return True, "Skonfigurowany"
