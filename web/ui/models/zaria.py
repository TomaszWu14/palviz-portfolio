from django.db import models

class ZariaModel(models.Model):
    """One selectable LLM model in the ZARIA catalog. Admin-managed; drives both the
    employee model picker and per-role/per-user access + cost calculation."""
    PROVIDERS = [("anthropic", "Anthropic"), ("openai", "OpenAI"),
                 ("azure_openai", "Azure OpenAI"), ("ollama", "Ollama (lokalny)")]

    key = models.CharField(max_length=60, unique=True, verbose_name="Identyfikator modelu",
                           help_text="Identyfikator modelu u dostawcy, np. claude-sonnet-4-5")
    display_name = models.CharField(max_length=100, verbose_name="Nazwa wyświetlana")
    provider = models.CharField(max_length=20, choices=PROVIDERS, verbose_name="Dostawca")
    is_active = models.BooleanField(default=True, verbose_name="Aktywny")
    price_input_per_1k = models.DecimalField(max_digits=10, decimal_places=6, default=0,
                                             verbose_name="Cena za 1k tokenów wejściowych (PLN)")
    price_output_per_1k = models.DecimalField(max_digits=10, decimal_places=6, default=0,
                                              verbose_name="Cena za 1k tokenów wyjściowych (PLN)")
    system_prompt = models.TextField(blank=True, verbose_name="Prompt systemowy (nadpisuje globalny)")
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name="Kolejność")

    class Meta:
        ordering = ["sort_order", "display_name"]
        verbose_name = "Model ZARIA"
        verbose_name_plural = "Modele ZARIA"

    def __str__(self):
        return self.display_name

    def effective_system_prompt(self):
        return self.system_prompt or ZariaConfig.load().system_prompt


class ZariaModelRoleAccess(models.Model):
    """Which roles may use which ZARIA model. No rows for a model → nobody but
    superusers/admins sees it — curated-by-default (unlike ControlledWarehouseType's
    empty-means-unrestricted, a paid LLM catalog should start closed)."""
    model = models.ForeignKey(ZariaModel, on_delete=models.CASCADE, related_name="role_access")
    group_name = models.CharField(max_length=60, verbose_name="Rola")

    class Meta:
        unique_together = [("model", "group_name")]
        verbose_name = "Dostęp roli do modelu ZARIA"
        verbose_name_plural = "Dostępy ról do modeli ZARIA"

    def __str__(self):
        return f"{self.group_name} → {self.model.display_name}"


class ZariaUserModelAccess(models.Model):
    """Per-user override on top of the role grid — force-ALLOW or force-DENY one model
    for one user. No row → inherit ZariaModelRoleAccess. Mirrors UserModuleAccess."""
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="zaria_model_access")
    model = models.ForeignKey(ZariaModel, on_delete=models.CASCADE, related_name="user_overrides")
    allowed = models.BooleanField(verbose_name="Dostęp")
    # „Dostęp podwyższony" (np. Opus na wniosek): grant z datą nadania i opcjonalnym
    # wygaśnięciem. Po expires_at wiersz jest ignorowany → powrót do siatki ról.
    granted_at = models.DateTimeField(null=True, blank=True, verbose_name="Nadano")
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="Wygasa (puste = bezterminowo)")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("user", "model")]
        verbose_name = "Dostęp użytkownika do modelu ZARIA"
        verbose_name_plural = "Dostępy użytkowników do modeli ZARIA"

    def __str__(self):
        return f"{self.user_id}:{self.model_id}={'+' if self.allowed else '−'}"

    def is_active(self, now=None):
        """Czy nadpisanie obowiązuje teraz (wygasły grant/deny nie liczy się)."""
        if self.expires_at is None:
            return True
        from django.utils import timezone as _tz
        return self.expires_at > (now or _tz.now())


class ZariaRolePermission(models.Model):
    """Uprawnienia funkcjonalne per rola, niezależne od dostępu do modeli. Na razie
    jedno: `can_compare` (tryb porównania dwóch modeli). Brak wiersza → domyślnie brak."""
    group_name = models.CharField(max_length=60, unique=True, verbose_name="Rola")
    can_compare = models.BooleanField(default=False, verbose_name="Może porównywać modele")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Uprawnienia roli ZARIA"
        verbose_name_plural = "Uprawnienia ról ZARIA"

    def __str__(self):
        return f"{self.group_name}: compare={self.can_compare}"


class ZariaConfig(models.Model):
    """Global ZARIA defaults — one row, editable in a dedicated admin form."""
    default_model = models.ForeignKey(ZariaModel, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="+", verbose_name="Model domyślny")
    system_prompt = models.TextField(blank=True, verbose_name="Globalny prompt systemowy")
    monthly_budget_pln = models.DecimalField(max_digits=10, decimal_places=2, default=0,
                                             verbose_name="Globalny miesięczny budżet (PLN, 0=brak limitu)")
    per_user_monthly_budget_pln = models.DecimalField(max_digits=10, decimal_places=2, default=0,
        verbose_name="Domyślny miesięczny budżet na użytkownika (PLN, 0=brak limitu)")
    rate_limit_per_minute = models.PositiveSmallIntegerField(default=20, verbose_name="Limit wiadomości/minutę")
    rate_limit_per_day = models.PositiveIntegerField(default=200, verbose_name="Limit wiadomości/dzień")
    # DEAD od czasu zniesienia twardego bloku per-user PLN (P13) — per-user PLN tylko ostrzega;
    # jedyny twardy cap pieniężny to globalny (poniżej). Pole zostaje dla zgodności/cleanupu.
    hard_block_over_budget = models.BooleanField(default=True,
        verbose_name="Blokuj wysyłkę po przekroczeniu budżetu (inaczej: tylko ostrzeżenie)")
    # % globalnego capu, przy którym łapie twarda blokada — bufor na miękkość agregatu
    # (60s cache + sprawdzenie przed wysyłką), żeby realne przekroczenie mieściło się poniżej capu.
    hard_block_fraction = models.PositiveSmallIntegerField(default=97,
        verbose_name="Próg twardej blokady globalnej (% capu, 1–100)")
    # Maks. liczba tokenów odpowiedzi (konfigurowalne; wymóg: domyślnie 1000).
    max_tokens = models.PositiveIntegerField(default=1000, verbose_name="Maks. tokenów odpowiedzi")
    # Limit długości pojedynczej wiadomości użytkownika (znaki).
    max_message_chars = models.PositiveIntegerField(default=8000, verbose_name="Maks. długość wiadomości (znaki)")
    # Domyślny miesięczny limit TOKENÓW na użytkownika (obok budżetu PLN); 0 = brak limitu.
    default_monthly_token_budget = models.PositiveIntegerField(default=800000,
        verbose_name="Domyślny miesięczny limit tokenów / użytkownika (0=brak)")
    rodo_notice = models.TextField(blank=True, verbose_name="Treść komunikatu RODO")
    # RAG-lite (roadmapa, Fala 5): wykryte w pytaniu kody (SKU/HU/lokalizacja/klient)
    # są dociągane z bazy GROOVE (tylko odczyt) i doklejane do promptu systemowego.
    rag_enabled = models.BooleanField(default=True,
        verbose_name="RAG: wzbogacaj odpowiedzi danymi GROOVE (tylko odczyt)")
    # F3: retencja — usuwaj NIEzapisane wątki starsze niż N dni (0 = nie usuwaj).
    # Ostrzeżenie w UI pojawia się 7 dni przed usunięciem.
    retention_days = models.PositiveIntegerField(default=0,
        verbose_name="Retencja: usuwaj niezapisane wątki starsze niż N dni (0=nigdy)")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Konfiguracja ZARIA (globalna)"
        verbose_name_plural = verbose_name

    def __str__(self):
        return "Konfiguracja ZARIA"

    # Ton z roadmapy (wywiad Q24): polski, przyjazny — asystent-kolega, ale konkretny.
    DEFAULT_SYSTEM_PROMPT = (
        "Jesteś ZARIA — wewnętrzną asystentką AI firmy ACME "
        "(dystrybucja wyrobów medycznych, logistyka magazynowa). Odpowiadasz po polsku, "
        "przyjaźnie i po koleżeńsku, ale konkretnie i rzeczowo. Gdy nie znasz odpowiedzi, "
        "mówisz to wprost. Nie zmyślasz danych firmowych. Krótkie pytanie = krótka "
        "odpowiedź; rozbudowana pomoc tylko, gdy temat tego wymaga.")

    @classmethod
    def load(cls):
        return cls.objects.first() or cls.objects.create(system_prompt=cls.DEFAULT_SYSTEM_PROMPT)


class ZariaRoleTokenBudget(models.Model):
    """Miesięczny limit tokenów na użytkownika, nadpisany per rola. Brak wiersza dla
    roli → dziedziczy ZariaConfig.default_monthly_token_budget. 0 = brak limitu.
    Użytkownik w wielu rolach dostaje najwyższy (najbardziej liberalny) limit."""
    group_name = models.CharField(max_length=60, unique=True, verbose_name="Rola")
    monthly_tokens = models.PositiveIntegerField(verbose_name="Miesięczny limit tokenów (0=brak)")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Limit tokenów ZARIA per rola"
        verbose_name_plural = "Limity tokenów ZARIA per rola"

    def __str__(self):
        return f"{self.group_name}: {self.monthly_tokens} tok./mies."


class ZariaAdminAudit(models.Model):
    """Append-only audyt zdarzeń administracyjnych ZARIA (zmiana promptu, limitów,
    ról/dostępów, modeli). Bez treści rozmów użytkowników."""
    actor = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, related_name="+")
    action = models.CharField(max_length=60, verbose_name="Zdarzenie")
    detail = models.CharField(max_length=400, blank=True, verbose_name="Szczegóły")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Audyt ZARIA (admin)"
        verbose_name_plural = "Audyt ZARIA (admin)"

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"


class ZariaUserBudget(models.Model):
    """Per-user override of the default monthly budget. No row → inherit
    ZariaConfig.per_user_monthly_budget_pln."""
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="zaria_budget")
    monthly_budget_pln = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Miesięczny budżet (PLN)")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Budżet ZARIA użytkownika"
        verbose_name_plural = "Budżety ZARIA użytkowników"

    def __str__(self):
        return f"{self.user_id}: {self.monthly_budget_pln} PLN/mies."


class ZariaConversation(models.Model):
    """One chat thread. Employee-owned; admins can read all for audit/RODO purposes."""
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="zaria_conversations")
    title = models.CharField(max_length=200, blank=True, verbose_name="Tytuł")
    model = models.ForeignKey(ZariaModel, on_delete=models.SET_NULL, null=True, related_name="conversations")
    # Tryb porównania (F4): system pokazuje odpowiedzi OBU modeli obok siebie, a operator
    # sam ocenia i ewentualnie wybiera. Brak auto-oceny. model_b = drugi model kolumny B.
    compare_mode = models.BooleanField(default=False, verbose_name="Tryb porównania")
    model_b = models.ForeignKey(ZariaModel, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="conversations_as_b", verbose_name="Model B (porównanie)")
    # F3: zapisane wątki (wyjęte spod retencji), przypięcie na górze, własne tagi (CSV).
    is_saved = models.BooleanField(default=False, verbose_name="Zapisany")
    pinned = models.BooleanField(default=False, verbose_name="Przypięty")
    tags = models.CharField(max_length=200, blank=True, default="", verbose_name="Tagi (po przecinku)")
    # Wysiłek (reasoning effort) rozmowy: auto = bez rozszerzonego myślenia; low/medium/high
    # mapowane na adaptive thinking + output_config.effort u dostawcy (zaria_llm).
    EFFORT_CHOICES = [("auto", "Auto"), ("low", "Niski"), ("medium", "Średni"), ("high", "Wysoki")]
    effort = models.CharField(max_length=10, choices=EFFORT_CHOICES, default="auto",
                              verbose_name="Wysiłek (effort)")
    # F5: wybrany firmowy szablon promptu systemowego (nadpisuje globalny/modelowy dla wątku).
    system_prompt_template = models.ForeignKey("ZariaSystemPromptTemplate", on_delete=models.SET_NULL,
                                               null=True, blank=True, related_name="conversations")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    accepted_privacy_notice_at = models.DateTimeField(null=True, blank=True, verbose_name="Akceptacja RODO")

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Konwersacja ZARIA"
        verbose_name_plural = "Konwersacje ZARIA"

    def __str__(self):
        # BEZ tytułu: tytuł = pierwsze słowa rozmowy, a __str__ renderuje się w Django
        # Adminie (listy, nagłówki) — treść nie może tam wyciekać (prywatność, Q26).
        return f"Konwersacja #{self.pk} ({self.user_id})"


class ZariaMessage(models.Model):
    """One turn in a conversation — user prompt or assistant reply. Append-only audit
    log; cost/tokens are attached only to assistant rows (a user prompt has no
    completion cost). Also the source table for the usage report."""
    ROLE = [("user", "Użytkownik"), ("assistant", "Asystent"), ("system", "System")]

    @property
    def has_table(self):
        """Czy treść zawiera tabelę markdown (wiersz separatora |---|) — wtedy
        pokazujemy przycisk „Pobierz XLSX" (patrz zaria_msg_xlsx)."""
        import re
        return bool(re.search(r"^\s*\|[\s:|-]+\|\s*$", self.content or "", re.M))

    conversation = models.ForeignKey(ZariaConversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=ROLE)
    content = models.TextField()
    model = models.ForeignKey(ZariaModel, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="messages")
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    cost_pln = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)
    # Tryb porównania: wiadomości jednej tury dzielą compare_group (hex uuid); variant
    # 'a'/'b' = kolumna modelu; rejected=True gdy operator wybrał drugi wariant (zostaje
    # w historii jako odrzucony). Puste variant = zwykła (nie-porównawcza) wiadomość.
    compare_group = models.CharField(max_length=32, blank=True, default="", db_index=True)
    variant = models.CharField(max_length=1, blank=True, default="",
                               choices=[("a", "A"), ("b", "B")])
    rejected = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Wiadomość ZARIA"
        verbose_name_plural = "Wiadomości ZARIA"

    def __str__(self):
        return f"{self.conversation_id}/{self.role}: {self.content[:40]}"


class ZariaSystemPromptTemplate(models.Model):
    """Firmowa biblioteka promptów systemowych (F5): admin zarządza, użytkownik wybiera
    z listy przy zakładaniu rozmowy (np. „Standard ACME", „Korespondencja z dostawcą")."""
    name = models.CharField(max_length=120, verbose_name="Nazwa")
    body = models.TextField(verbose_name="Treść promptu systemowego")
    is_active = models.BooleanField(default=True, verbose_name="Aktywny")
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name="Kolejność")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Szablon promptu systemowego ZARIA"
        verbose_name_plural = "Szablony promptów systemowych ZARIA"

    def __str__(self):
        return self.name


class ZariaMailLog(models.Model):
    """Log przygotowań maili z odpowiedzi ZARII (F5). To potencjalna droga wypływu danych
    na zewnątrz — rejestrujemy KTO, KIEDY, DO KOGO, który wątek i jakim kanałem. Sama
    wysyłka jest po stronie człowieka (przygotowujemy wersję roboczą)."""
    TRANSPORT = [("mailto", "mailto"), ("eml", "plik .eml"), ("graph", "Microsoft Graph")]
    SCOPE = [("last", "Ostatnia odpowiedź"), ("all", "Cała rozmowa")]
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="zaria_mail_logs")
    conversation = models.ForeignKey(ZariaConversation, on_delete=models.SET_NULL, null=True,
                                     related_name="mail_logs")
    to = models.CharField(max_length=300, blank=True, verbose_name="Do")
    cc = models.CharField(max_length=300, blank=True, verbose_name="DW")
    subject = models.CharField(max_length=300, blank=True, verbose_name="Temat")
    scope = models.CharField(max_length=8, choices=SCOPE, default="last")
    transport = models.CharField(max_length=8, choices=TRANSPORT, default="mailto")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Log wysyłki maila ZARIA"
        verbose_name_plural = "Logi wysyłek maili ZARIA"

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.user_id}→{self.to}"


