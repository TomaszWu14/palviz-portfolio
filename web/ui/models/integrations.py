from django.db import models

class SiteInfo(models.Model):
    """Facility info handed to drivers (single row, editable in admin): warehouse
    leaders' contacts, a site map image and movement instructions."""
    leaders = models.CharField(max_length=300, blank=True, verbose_name="Liderzy magazynu (kontakty)")
    site_map = models.ImageField(upload_to="site/", null=True, blank=True, verbose_name="Mapka obiektu")
    instructions = models.TextField(blank=True, verbose_name="Instrukcja poruszania się na obiekcie")

    class Meta:
        verbose_name = "Informacje o obiekcie (dla kierowcy)"
        verbose_name_plural = verbose_name

    def __str__(self):
        return "Informacje o obiekcie"

    @classmethod
    def current(cls):
        return cls.objects.first()


class PowerBIToken(models.Model):
    """Persisted MSAL token cache for the delegated (device-code) Power BI login — a
    single row. Stored in the DB so the refresh token survives container redeploys; the
    server then refreshes the access token silently (no browser). Connect once with
    `manage.py powerbi_connect`."""
    cache = models.TextField(blank=True, verbose_name="Cache tokenu (MSAL)")
    account = models.CharField(max_length=200, blank=True, verbose_name="Połączone konto")
    # Ostatni błąd logowania/pobrania — bez tego nieudane logowanie z przeglądarki ginęło
    # w wątku w tle i użytkownik widział tylko „nie działa”.
    last_error = models.TextField(blank=True, verbose_name="Ostatni błąd")
    last_error_at = models.DateTimeField(null=True, blank=True, verbose_name="Data ostatniego błędu")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Token Power BI (logowanie użytkownika)"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"Power BI: {self.account or 'niepołączony'}"

    @classmethod
    def load(cls):
        return cls.objects.first() or cls.objects.create()

