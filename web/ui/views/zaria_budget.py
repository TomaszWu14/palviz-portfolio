# ZARIA — wewnętrzny asystent AI (czat z modelami LLM). Employee-facing chat screens;
# admin configuration/usage-report screens live in admin.py.

from django.db.models import Sum, F, Q
from django.utils import timezone

from ..models import (ZariaModel, ZariaModelRoleAccess, ZariaUserModelAccess, ZariaConfig,
                      ZariaUserBudget, ZariaRoleTokenBudget, ZariaRolePermission,
                      ZariaConversation, ZariaMessage, Task)
from ..notifications import notify, owner_users
from ..zaria_llm import provider_health

MAX_API_HISTORY = 10   # historia wysyłana do API obcinana do ostatnich N wiadomości


def effective_max_tokens(config):
    return config.max_tokens or 1000


def validate_message(content, config):
    """Zwróć polski komunikat błędu lub None gdy wiadomość jest OK (typ + długość)."""
    if not isinstance(content, str):
        return "Nieprawidłowy format wiadomości."
    if not content.strip():
        return "Wiadomość jest pusta."
    limit = config.max_message_chars or 8000
    if len(content) > limit:
        return f"Wiadomość jest za długa (maks. {limit} znaków)."
    return None


def trim_history(messages, n=MAX_API_HISTORY):
    """Ostatnie n wiadomości wysyłanych do API (obcięcie kontekstu = kontrola kosztu)."""
    return messages[-n:] if len(messages) > n else messages


def month_token_usage(user):
    """Suma tokenów (wejście+wyjście) użytkownika w bieżącym miesiącu kalendarzowym."""
    now = timezone.localtime()
    return ZariaMessage.objects.filter(
        conversation__user=user, role="assistant",
        created_at__year=now.year, created_at__month=now.month,
    ).aggregate(t=Sum(F("prompt_tokens") + F("completion_tokens")))["t"] or 0


def token_budget_for(user):
    """Miesięczny limit tokenów użytkownika (0 = bez limitu). Nadpisanie per rola
    (najbardziej liberalne z ról użytkownika) albo domyślny globalny."""
    if user.is_superuser:
        return 0
    role_names = list(user.groups.values_list("name", flat=True))
    vals = list(ZariaRoleTokenBudget.objects.filter(group_name__in=role_names)
                .values_list("monthly_tokens", flat=True))
    if vals:
        return 0 if 0 in vals else max(vals)
    return ZariaConfig.load().default_monthly_token_budget or 0


def token_budget_exceeded(user):
    budget = token_budget_for(user)
    return bool(budget) and month_token_usage(user) >= budget


def _accessible_models(user):
    """Active ZariaModel rows this user may use: role grid, with a per-user
    override on top (force-allow/force-deny). Superusers see everything."""
    models = list(ZariaModel.objects.filter(is_active=True))
    if user.is_superuser:
        return models
    role_names = set(user.groups.values_list("name", flat=True))
    role_allowed = {ra.model_id for ra in ZariaModelRoleAccess.objects.filter(model__in=models)
                    if ra.group_name in role_names}
    # Wygasłe nadpisania (expires_at w przeszłości) ignorujemy → powrót do siatki ról.
    overrides = {o.model_id: o.allowed for o in
                 ZariaUserModelAccess.objects.filter(user=user, model__in=models) if o.is_active()}
    return [m for m in models if overrides.get(m.id, m.id in role_allowed)]


def can_compare(user):
    """Czy użytkownik ma prawo do trybu porównania (uprawnienie roli). Superuser zawsze."""
    if user.is_superuser:
        return True
    role_names = set(user.groups.values_list("name", flat=True))
    return ZariaRolePermission.objects.filter(group_name__in=role_names, can_compare=True).exists()


def allowed_effort(user, raw):
    """Wysiłek z żądania → dozwolona wartość. INT-007: 'high' (adaptive thinking, sufit
    ≥16000 tokenów w zaria_llm._thinking_params) tylko dla Administratorów/Master Data;
    pozostałym spada do 'auto' (max_tokens = effective_max_tokens z ZariaConfig)."""
    from core.roles import GROUP_ADMIN, GROUP_MASTER_DATA, has_role
    if raw not in ("low", "medium", "high"):
        return "auto"
    if raw == "high" and not has_role(user, GROUP_ADMIN, GROUP_MASTER_DATA):
        return "auto"
    return raw


def _provider_availability(providers):
    """{provider: (ok, detail)} z health-checkiem cache'owanym 60 s (Redis) — żeby nie
    pingować przy każdym renderze. Degradacja: niedostępny provider → modele wyszarzone."""
    from django.core.cache import cache
    out = {}
    for p in set(providers):
        key = f"zaria:health:{p}"
        val = cache.get(key)
        if val is None:
            val = provider_health(p)
            cache.set(key, val, timeout=60)
        out[p] = val
    return out


def _annotate_availability(models):
    """Ustaw m.available (bool) i m.health_detail. Degradacja dotyczy TYLKO lokalnego
    providera (Ollama) — chmurowe zostają dostępne w pickerze, ich błędy łapie wysyłka
    (inaczej brak klucza w devie wyszarzałby wszystko)."""
    local = [m.provider for m in models if m.provider == "ollama"]
    avail = _provider_availability(local) if local else {}
    for m in models:
        if m.provider == "ollama":
            ok, detail = avail.get("ollama", (True, ""))
            m.available, m.health_detail = ok, detail
        else:
            m.available, m.health_detail = True, ""
    return models


def can_use_zaria_model(user, model):
    if user.is_superuser:
        return True
    override = ZariaUserModelAccess.objects.filter(user=user, model=model).first()
    if override is not None and override.is_active():
        return override.allowed
    role_names = set(user.groups.values_list("name", flat=True))
    return ZariaModelRoleAccess.objects.filter(model=model, group_name__in=role_names).exists()


def _month_spend_pln(user):
    now = timezone.localtime()
    return ZariaMessage.objects.filter(
        conversation__user=user, role="assistant",
        created_at__year=now.year, created_at__month=now.month,
    ).aggregate(total=Sum("cost_pln"))["total"] or 0


def _user_budget_pln(user):
    override = ZariaUserBudget.objects.filter(user=user).first()
    if override is not None:
        return override.monthly_budget_pln
    return ZariaConfig.load().per_user_monthly_budget_pln


ALERT_THRESHOLD = 0.8       # wczesne ostrzeżenie przy 80% budżetu (per-user i globalnego)
SECOND_ALERT_OF_BLOCK = 0.95  # „ostatnie ostrzeżenie" globalne — % PROGU BLOKADY (nie capu),
                              # więc zawsze wypada tuż pod blokadą niezależnie od hard_block_fraction


def _check_budget_threshold(user, spend, budget):
    """Alert once per user per month when spend crosses ALERT_THRESHOLD of the budget
    (dedup-key idiom from notifications._raise_task, via Task.category='zaria_budget')."""
    if not budget or spend < float(budget) * ALERT_THRESHOLD:
        return
    period = timezone.localdate().strftime("%Y-%m")
    dedup = f"zaria_budget:{user.id}:{period}"[:120]
    if Task.objects.filter(dedup_key=dedup).exists():
        return
    pct = int(round(float(spend) / float(budget) * 100)) if budget else 0
    Task.objects.create(
        title=f"ZARIA: {user.username} — {pct}% budżetu miesięcznego",
        description=f"Wydano {spend} PLN z budżetu {budget} PLN w tym miesiącu.",
        category="zaria_budget", dedup_key=dedup, url="/admin-panel/zaria/usage/")
    notify(owner_users(), f"ZARIA: budżet {pct}% — {user.username}",
          f"Wydano {spend} PLN z budżetu {budget} PLN.", level="warning",
          url="/admin-panel/zaria/usage/", email=True)


def org_month_spend_pln():
    """Wydatek CAŁEJ organizacji od początku miesiąca (PLN), cache'owany w Redis ~60 s —
    żeby nie sumować całej tabeli przy każdej wysyłce."""
    from django.core.cache import cache
    now = timezone.localtime()
    key = f"zaria:org_spend:{now:%Y%m}"
    val = cache.get(key)
    if val is None:
        val = float(ZariaMessage.objects.filter(
            role="assistant", created_at__year=now.year, created_at__month=now.month,
        ).aggregate(total=Sum("cost_pln"))["total"] or 0)
        cache.set(key, val, timeout=60)
    return val


def _global_block_threshold():
    """Efektywny próg twardej blokady globalnej w PLN (cap * hard_block_fraction%), albo 0
    gdy globalny cap nie ustawiony. Blokada łapie poniżej capu — bufor na miękkość agregatu."""
    cfg = ZariaConfig.load()
    cap = cfg.monthly_budget_pln
    if not cap:
        return 0.0
    frac = min(max(int(cfg.hard_block_fraction or 100), 1), 100)
    return float(cap) * frac / 100.0


def global_budget_exceeded():
    """True gdy globalny miesięczny budżet org jest ustawiony i osiągnięto próg blokady."""
    thr = _global_block_threshold()
    return bool(thr) and org_month_spend_pln() >= thr


def _raise_global_alert(dedup, title, spend, cap):
    """Wspólny alert globalny raz/mies (dedup po kluczu) do adminów."""
    if Task.objects.filter(dedup_key=dedup).exists():
        return
    Task.objects.create(
        title=title,
        description=f"Wydano {spend:.2f} PLN z globalnego budżetu {cap} PLN w tym miesiącu.",
        category="zaria_budget", dedup_key=dedup, url="/admin-panel/zaria/usage/")
    notify(owner_users(), title,
          f"Wydano {spend:.2f} z {cap} PLN w tym miesiącu.", level="warning",
          url="/admin-panel/zaria/usage/", email=True)


def _check_global_budget_threshold():
    """Dwa alerty raz/mies, żeby twardy globalny sufit nie zaskoczył adminów: wczesny przy
    80% capu i „ostatnie ostrzeżenie" tuż pod progiem blokady (95% tego progu)."""
    cap = ZariaConfig.load().monthly_budget_pln
    if not cap:
        return
    spend = org_month_spend_pln()
    period = timezone.localdate().strftime("%Y-%m")
    # Ostatnie ostrzeżenie — liczone względem progu blokady, więc zawsze poniżej blokady.
    if spend >= _global_block_threshold() * SECOND_ALERT_OF_BLOCK:
        _raise_global_alert(f"zaria_budget_global_last:{period}"[:120],
                            "ZARIA: globalny budżet — tuż przed blokadą", spend, cap)
    # Wczesne ostrzeżenie — 80% capu.
    if spend >= float(cap) * ALERT_THRESHOLD:
        _raise_global_alert(f"zaria_budget_global:{period}"[:120],
                            "ZARIA: organizacja zbliża się do globalnego budżetu (80%)", spend, cap)


def _search_conversations(user, q):
    """Własne rozmowy użytkownika przefiltrowane po q (tytuł + treść). Postgres: pełnotekstowe
    (konfiguracja polska, websearch); SQLite/dev: icontains fallback. Prywatność: tylko swoje."""
    qs = ZariaConversation.objects.filter(user=user).select_related("model")
    if not q:
        return qs
    from django.db import connection
    if connection.vendor == "postgresql":
        # 'simple' zamiast 'polish' — konfiguracja 'polish' NIE jest wbudowana w standardowego
        # Postgresa (byłby błąd „text search configuration polish does not exist"). 'simple'
        # jest zawsze dostępna (lowercase+split, bez błędnego stemmingu dla PL). Polską można
        # włączyć osobno instalując słownik i zmieniając config tutaj.
        from django.contrib.postgres.search import SearchVector, SearchQuery
        vector = SearchVector("title", "messages__content", config="simple")
        return qs.annotate(sv=vector).filter(
            sv=SearchQuery(q, config="simple", search_type="websearch")).distinct()
    return qs.filter(Q(title__icontains=q) | Q(messages__content__icontains=q)).distinct()


def _sidebar_groups(user, q=""):
    """Kontekst sidebara: rozmowy użytkownika. Przypięte na górze (osobna grupa), reszta
    pogrupowana po dacie aktywności. ?q= przeszukuje własne rozmowy (patrz _search_conversations)."""
    from django.utils.translation import gettext_lazy as _
    qs = _search_conversations(user, q)
    today = timezone.localdate()
    pinned = [c for c in qs.filter(pinned=True)[:30]]
    buckets = [(_("Przypięte"), pinned), (_("Dziś"), []), (_("Wczoraj"), []),
               (_("Ostatnie 7 dni"), []), (_("Starsze"), [])]
    pinned_ids = {c.id for c in pinned}
    for c in qs[:100]:
        if c.id in pinned_ids:
            continue
        d = timezone.localtime(c.updated_at).date() if c.updated_at else today
        delta = (today - d).days
        idx = 1 if delta <= 0 else 2 if delta == 1 else 3 if delta <= 7 else 4
        buckets[idx][1].append(c)
    return [(label, items) for label, items in buckets if items]


def _saved_conversations(user):
    return list(ZariaConversation.objects.filter(user=user, is_saved=True).select_related("model")[:50])

__all__ = [
    'MAX_API_HISTORY',
    'effective_max_tokens',
    'validate_message',
    'trim_history',
    'month_token_usage',
    'token_budget_for',
    'token_budget_exceeded',
    '_accessible_models',
    'can_compare',
    '_provider_availability',
    '_annotate_availability',
    'can_use_zaria_model',
    '_month_spend_pln',
    '_user_budget_pln',
    'ALERT_THRESHOLD',
    'SECOND_ALERT_OF_BLOCK',
    '_check_budget_threshold',
    'org_month_spend_pln',
    '_global_block_threshold',
    'global_budget_exceeded',
    '_raise_global_alert',
    '_check_global_budget_threshold',
    '_search_conversations',
    '_sidebar_groups',
    '_saved_conversations',
]
