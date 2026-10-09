from django.db import models
from .catalog import Product

class Task(models.Model):
    """A work item, either created manually or auto-raised by the discrepancy engine.
    Team tasks (assignee NULL) are visible to the whole owning group; `dedup_key` keeps
    re-running the engine from duplicating the same open task."""
    STATUS = [("todo", "Do zrobienia"), ("in_progress", "W toku"), ("done", "Zrobione")]
    PRIORITY = [("low", "Niski"), ("normal", "Normalny"), ("high", "Wysoki")]
    CATEGORY = [("manual", "Ręczne"), ("stock_discrepancy", "Niezgodność stocku"),
                ("recurring", "Cykliczne"), ("zaria_budget", "Budżet ZARIA"),
                ("hu_fix", "Naprawcze (kontrola HU)"), ("hu_stale", "HU zniknęła z importu"),
                ("md_exception", "Niezgodność master daty"),
                ("carton_dim_mismatch", "Rozjazd wymiarów kartonu")]

    title = models.CharField(max_length=200, verbose_name="Tytuł")
    description = models.TextField(blank=True, verbose_name="Opis")
    category = models.CharField(max_length=24, choices=CATEGORY, default="manual")
    priority = models.CharField(max_length=8, choices=PRIORITY, default="normal")
    status = models.CharField(max_length=12, choices=STATUS, default="todo", db_index=True)
    assignee = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="tasks", verbose_name="Przypisane do")
    created_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="created_tasks")
    due_date = models.DateField(null=True, blank=True, verbose_name="Termin")
    source_ref = models.CharField(max_length=80, blank=True, verbose_name="Źródło (np. HU)")
    url = models.CharField(max_length=300, blank=True)
    # Hard links to the related object(s) — set by the discrepancy engine / editable later.
    related_hu = models.ForeignKey("huctl.HandlingUnit", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="tasks", verbose_name="Powiązany HU")
    related_product = models.ForeignKey("Product", on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="tasks", verbose_name="Powiązany produkt")
    # Durable business-key reference to the product (its code) — the migration path off the
    # cross-context FK. When master data becomes its own service/DB a FK can't span databases,
    # but the code (a stable business key) can. New writers set this; readers prefer it and
    # fall back to the FK. Once every reader is off the FK, `related_product` can be dropped.
    related_product_code = models.CharField(max_length=50, blank=True, default="",
                                            verbose_name="Kod powiązanego produktu")
    related_location = models.CharField(max_length=50, blank=True, verbose_name="Powiązana lokalizacja")
    dedup_key = models.CharField(max_length=120, blank=True, default="", db_index=True,
                                 verbose_name="Klucz deduplikacji")
    overdue_last_reminded = models.DateField(null=True, blank=True)   # last 'past due' reminder date
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_overdue(self):
        from django.utils import timezone
        return bool(self.due_date and self.status != "done" and self.due_date < timezone.localdate())

    @property
    def related_product_ref(self):
        """The related product's business key (code) — new field first, FK as fallback.
        Read this instead of `related_product` so call sites survive the master-data DB split."""
        return self.related_product_code or (self.related_product.code if self.related_product_id else "")

    def related_product_data(self):
        """Fetch the related product's master data (dict) via the master-data client — local
        DB today, the master-data service when MASTER_DATA_URL is set. None when unset/unknown."""
        code = self.related_product_ref
        if not code:
            return None
        from ui import master_data_client
        return master_data_client.get_product(code)

    class Meta:
        # NB: priority is a text choice (low/normal/high), so "-priority" would sort
        # alphabetically and put 'high' LAST — drop it from the default ordering. Consumers
        # that need importance order (the task list) sort by an explicit Case/When.
        ordering = ["status", "-created_at"]
        constraints = [
            # Twarda gwarancja idempotencji API /tasks: co najwyżej JEDNO otwarte zadanie
            # per dedup_key, niezależnie od liczby workerów (mutex w locmem nie domykał
            # okna wyścigu między procesami).
            models.UniqueConstraint(
                fields=["dedup_key"],
                condition=~models.Q(status="done") & ~models.Q(dedup_key=""),
                name="uniq_open_task_per_dedup_key"),
        ]
        verbose_name = "Zadanie"
        verbose_name_plural = "Zadania"

    def __str__(self):
        return self.title


class ProductViewHistory(models.Model):
    """Ostatnio oglądane indeksy w MATINFO — per UŻYTKOWNIK, server-side (B1).
    Wcześniej localStorage: ginęło z czyszczeniem przeglądarki i mieszało operatorów
    na współdzielonych terminalach Zebra. Jeden wiersz per (user, product), viewed_at
    podbijany przy każdym wejściu."""
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE,
                             related_name="product_views")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="+")
    viewed_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "product"],
                                               name="uniq_product_view_user_product")]
        ordering = ["-viewed_at"]
        verbose_name = "Historia podglądu materiału"
        verbose_name_plural = "Historia podglądów materiałów"

    def __str__(self):
        return f"{self.user_id}→{self.product_id} {self.viewed_at:%Y-%m-%d %H:%M}"


class Position(models.Model):
    """Stanowisko (master data użytkowników): warstwa POŚREDNIA nad zamrożonymi grupami
    Django (roles.py). Stanowisko = nazwa + zestaw istniejących grup; przypisanie
    stanowiska synchronizuje grupy konta, więc CAŁA istniejąca autoryzacja (dekoratory,
    flagi szablonów, MODULES) działa bez zmian w kodzie widoków. Nowe stanowisko będące
    kombinacją istniejących uprawnień = wpis w panelu, zero deployu. (Nowy RODZAJ
    uprawnienia nadal wymaga kodu — grupy to frozen contract SSO.)"""
    name = models.CharField(max_length=80, unique=True, verbose_name="Nazwa stanowiska")
    description = models.CharField(max_length=200, blank=True, verbose_name="Opis")
    groups = models.ManyToManyField("auth.Group", blank=True, related_name="positions",
                                    verbose_name="Grupy (role)")
    is_active = models.BooleanField(default=True, verbose_name="Aktywne")
    order = models.PositiveSmallIntegerField(default=100, verbose_name="Kolejność")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "Stanowisko"
        verbose_name_plural = "Stanowiska"

    def __str__(self):
        return self.name

    def apply_to(self, user):
        """Zsynchronizuj grupy konta ze stanowiskiem (nadpisuje ręczne przypisania)."""
        user.groups.set(self.groups.all())


class UserProfile(models.Model):
    """Per-user extension attached 1:1 to the built-in ``auth.User``.

    We deliberately keep ``auth.User`` and extend it here rather than swapping
    ``AUTH_USER_MODEL`` (the project already ships many migrations + live data and
    the deploy auto-runs ``migrate``, so an in-place swap would risk breaking
    login). This profile is the extension point — add per-user fields here. It is
    auto-created for every user by a ``post_save`` signal (``ui.signals``), so
    ``request.user.profile`` is always present.
    """
    user = models.OneToOneField("auth.User", on_delete=models.CASCADE, related_name="profile")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Telefon (SMS)")
    # Master data użytkowników (BLOK G): stanowisko (→ synchronizacja grup) + przełożony
    # (adresat „Napisz do lidera" — wcześniej tylko cała grupa Lider kontroli).
    position = models.ForeignKey(Position, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="users", verbose_name="Stanowisko")
    leader = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="subordinates", verbose_name="Lider (przełożony)")
    # Dział / zespół — źródło grupowania w raporcie zużycia ZARIA (per dział).
    department = models.CharField(max_length=80, blank=True, verbose_name="Dział")
    # Sekcja operatora — steruje motywem całej aplikacji skanera (pasek, tło, akcenty).
    # Puste = motyw bazowy (teal). Kody = przewoźnicy z ui.theme.WAREHOUSE_THEMES.
    section = models.CharField(max_length=20, blank=True, default="",
                              choices=[("GLS", "GLS"), ("BUS", "BUS"),
                                       ("GEIS", "GEIS"), ("EXPORT", "EXPORT")],
                              verbose_name="Sekcja")
    # Strefy kontroli, w których użytkownik MOŻE pracować (CSV kodów sekcji, np.
    # "BUS,EXPORT"). `section` wyżej = strefa AKTYWNA (jedna naraz, przełączana na
    # ekranie wyboru strefy). Puste = bez ograniczenia strefowego (zachowanie sprzed
    # przebudowy — twarde bramki nadal daje ControllerZone).
    allowed_sections = models.CharField(max_length=40, blank=True, default="",
                                        verbose_name="Dozwolone strefy kontroli")

    SECTION_CODES = ("GLS", "BUS", "GEIS", "EXPORT")

    @property
    def allowed_sections_list(self):
        return [s for s in (self.allowed_sections or "").split(",") if s]

    @classmethod
    def parse_allowed_sections(cls, values):
        """Lista z POST → (CSV dozwolonych stref w stałej kolejności, bad?) — bad=True
        gdy pojawił się nieznany kod strefy (walidacja u zlewu, jedno miejsce)."""
        picked = {str(v).strip().upper() for v in values}
        return (",".join(c for c in cls.SECTION_CODES if c in picked),
                bool(picked - set(cls.SECTION_CODES)))
    # Per-user opt-out of the module's e-mail notifications (in-app + SMS unaffected).
    email_notifications = models.BooleanField(default=True, verbose_name="Powiadomienia e-mail")
    # Presence: ostatnia aktywność HTTP (throttling 60 s w PresenceMiddleware) + typ
    # urządzenia z User-Agenta — panel lidera pokazuje „na czym" pracuje kontroler,
    # a wysyłka wiadomości ostrzega o odbiorcy offline / prawdopodobnie poza pracą.
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True,
                                        verbose_name="Ostatnia aktywność")
    last_device = models.CharField(max_length=12, blank=True, default="",
                                   choices=[("zebra", "Zebra"), ("mobile", "Telefon"),
                                            ("tablet", "Tablet"), ("desktop", "Komputer")],
                                   verbose_name="Ostatnie urządzenie")

    @property
    def has_camera(self):
        """Czy urządzenie użytkownika ma aparat — routing zadań „zrób zdjęcie"
        (skanery Zebra MC330L nie mają aparatu; komputer traktujemy tak samo)."""
        return self.last_device in ("mobile", "tablet")
    # Hasło nadane hurtem (import użytkowników) jest jednorazowe: middleware kieruje
    # takie konto na zmianę hasła, zanim wpuści je gdziekolwiek indziej.
    must_change_password = models.BooleanField(
        default=False, verbose_name="Wymuś zmianę hasła przy logowaniu")
    # Preferencje UI platformy (motyw + język). Puste = podążaj za localStorage/OS
    # (motyw) i cookie/Accept-Language (język) — ustawiane przez /prefs/.
    ui_theme = models.CharField(max_length=5, blank=True, default="",
                               choices=[("light", "light"), ("dark", "dark")],
                               verbose_name="Motyw interfejsu")
    ui_lang = models.CharField(max_length=5, blank=True, default="",
                              choices=[("pl", "pl"), ("en", "en")],
                              verbose_name="Język interfejsu")
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        verbose_name = "Profil użytkownika"
        verbose_name_plural = "Profile użytkowników"

    def __str__(self):
        return f"{self.user_id}: {self.phone}"


class RecurringTask(models.Model):
    """Template that auto-generates a Task on a schedule (Celery beat advances next_run)."""
    INTERVAL = [("daily", "Codziennie"), ("weekly", "Co tydzień"), ("monthly", "Co miesiąc")]
    title = models.CharField(max_length=200, verbose_name="Tytuł")
    description = models.TextField(blank=True, verbose_name="Opis")
    priority = models.CharField(max_length=8, choices=Task.PRIORITY, default="normal")
    assignee = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="recurring_tasks", verbose_name="Przypisane do")
    interval = models.CharField(max_length=8, choices=INTERVAL, default="weekly", verbose_name="Częstotliwość")
    next_run = models.DateField(verbose_name="Następne uruchomienie")
    is_active = models.BooleanField(default=True, verbose_name="Aktywne")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["next_run"]
        verbose_name = "Zadanie cykliczne"
        verbose_name_plural = "Zadania cykliczne"

    def __str__(self):
        return f"{self.title} ({self.get_interval_display()})"


class TaskChecklistItem(models.Model):
    """A checklist line on a task; progress = done/total of these."""
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="checklist")
    text = models.CharField(max_length=200)
    done = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text


class TaskComment(models.Model):
    """A comment or a system activity-log entry on a task (status/assignment changes are
    logged as system entries; users add their own comments)."""
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="task_comments")
    body = models.CharField(max_length=500)
    is_system = models.BooleanField(default=False)   # auto activity entry vs. user comment
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.task_id}: {self.body[:40]}"



class UserModuleAccess(models.Model):
    """Per-user override for a GROOVE hub module — force-ALLOW or force-DENY access to a
    single module for one login, layered on top of the role defaults (the advanced
    login × module matrix). No row for a (user, module) → that user inherits the module's
    role gate. ``allowed=True`` grants regardless of role; ``allowed=False`` blocks even if
    the role would allow. Superusers always pass."""
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="module_access")
    module_key = models.CharField(max_length=40, verbose_name="Moduł")
    allowed = models.BooleanField(verbose_name="Dostęp")   # True = wymuś, False = zablokuj
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("user", "module_key")]
        verbose_name = "Dostęp użytkownika do modułu"
        verbose_name_plural = "Dostępy użytkowników do modułów"

    def __str__(self):
        return f"{self.user_id}:{self.module_key}={'+' if self.allowed else '−'}"


class AccessAudit(models.Model):
    """Audyt zmian ról i dostępów: kto (actor) komu (target_user) zmienił rolę lub
    nadpisanie modułu, kiedy. Bez treści — tylko fakt zmiany dostępu. (P3)"""
    actor = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, related_name="+")
    action = models.CharField(max_length=60, verbose_name="Zdarzenie")
    target_user = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+", verbose_name="Dotyczy użytkownika")
    detail = models.CharField(max_length=400, blank=True, verbose_name="Szczegóły")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Audyt dostępu"
        verbose_name_plural = "Audyt dostępu"

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"


# ─── ZARIA — wewnętrzny asystent AI (czat z modelami LLM) ─────────────────────

