from django.db import models

class LocationIssue(models.Model):
    """Zgłoszenie problemu LOKALIZACJI z modułu PHV (skaner): pracownik skanuje kod
    lokalizacji, widzi jej dane i zgłasza problem fizyczny (uszkodzona/brak etykiety,
    uszkodzona belka, brak HU). Osobne od PackagingIssue (tamto jest o master dacie
    materiału). Kod trzymamy jako string — skanowana lokalizacja bywa spoza aktywnej
    partii master daty, więc bez FK. Mail → PHV_ISSUE_EMAIL + bell dla Master Data."""
    TYPES = [
        ("damaged_label", "Uszkodzona etykieta"),
        ("missing_label", "Brak etykiety"),
        ("damaged_beam", "Uszkodzona belka"),
        ("missing_hu", "Brak HU w lokalizacji"),
        ("other", "Inne / uwaga"),
    ]
    STATUS = [("open", "Otwarte"), ("in_review", "W przeglądzie"), ("resolved", "Rozwiązane")]
    location_code = models.CharField(max_length=50, db_index=True, verbose_name="Lokalizacja")
    issue_type = models.CharField(max_length=24, choices=TYPES, verbose_name="Typ zgłoszenia")
    description = models.TextField(max_length=500, blank=True, verbose_name="Opis")
    photo = models.ImageField(upload_to="phv/loc/%Y/%m/", null=True, blank=True, verbose_name="Zdjęcie")
    reporter = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True,
                                 related_name="location_issues")
    status = models.CharField(max_length=12, choices=STATUS, default="open", db_index=True)
    resolver_notes = models.CharField(max_length=300, blank=True, verbose_name="Notatka rozwiązania")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Zgłoszenie lokalizacji"
        verbose_name_plural = "Zgłoszenia lokalizacji"

    def __str__(self):
        return f"#{self.pk} {self.location_code} · {self.get_issue_type_display()} ({self.status})"




# ─── Warehouse 3D/2D Model ────────────────────────────────────────────────────

class Notification(models.Model):
    """An in-app notification for one user (bell badge + list). Created by the system
    (e.g. stock discrepancies) or other flows."""
    LEVELS = [("info", "Info"), ("warning", "Ostrzeżenie"), ("error", "Błąd")]
    recipient = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="notifications")
    level = models.CharField(max_length=8, choices=LEVELS, default="warning")
    title = models.CharField(max_length=160, verbose_name="Tytuł")
    body = models.CharField(max_length=400, blank=True, verbose_name="Treść")
    url = models.CharField(max_length=300, blank=True, verbose_name="Link")
    is_read = models.BooleanField(default=False, db_index=True)
    # Pilny komunikat wymagający JAWNEGO potwierdzenia odczytu (modal, re-pokazywany co 30 s,
    # aż confirmed_at). `ack_group` łączy per-użytkownika kopie jednej wysyłki (widok nadawcy:
    # kto jeszcze nie potwierdził).
    requires_ack = models.BooleanField(default=False, db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    ack_group = models.CharField(max_length=40, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Powiadomienie"
        verbose_name_plural = "Powiadomienia"

    def __str__(self):
        return f"{self.recipient_id}: {self.title}"


class MessageThread(models.Model):
    """Wątek komunikatora wewnętrznego (kontroler ↔ lider/grupa): uczestnicy + kontekst.
    Same wiadomości w `Message`. Doręczenie/alert idzie przez Notification (dzwonek)."""
    subject = models.CharField(max_length=160, blank=True, verbose_name="Temat / kontekst")
    url = models.CharField(max_length=300, blank=True, verbose_name="Link kontekstu")
    participants = models.ManyToManyField("auth.User", related_name="message_threads")
    created_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)   # bump = nowa wiadomość

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Wątek wiadomości"
        verbose_name_plural = "Wątki wiadomości"

    def __str__(self):
        return self.subject or f"Wątek #{self.pk}"


class MessageRead(models.Model):
    """Znacznik odczytu wątku per uczestnik — wątek jest „nieprzeczytany", gdy ma
    wiadomości nowsze niż `last_read_at` (albo brak wpisu). Zasila licznik ✉ w nagłówku."""
    thread = models.ForeignKey(MessageThread, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="message_reads")
    last_read_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["thread", "user"],
                                               name="uniq_message_read_thread_user")]
        verbose_name = "Odczyt wątku"
        verbose_name_plural = "Odczyty wątków"

    def __str__(self):
        return f"{self.user_id}@{self.thread_id}: {self.last_read_at:%Y-%m-%d %H:%M}"


class Message(models.Model):
    thread = models.ForeignKey(MessageThread, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True,
                               blank=True, related_name="+")
    body = models.CharField(max_length=500, verbose_name="Treść")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.sender_id}: {self.body[:40]}"


