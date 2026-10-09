from django.apps import AppConfig


class HuctlConfig(AppConfig):
    """Kontrola HU (huctl) — wydzielona aplikacja: skaner kontroli palet, rejestr HU
    (wsad/stock), wydruk etykiet HU.

    W1: widoki+szablony; modele nadal w ui.models (W2 = SeparateDatabaseAndState).
    URL-e w ui.urls (namespace ui:), ścieżki szablonów bez zmian; scanner/base.html
    zostaje w ui (współdzielony z PHV/MATINFO)."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "huctl"
    verbose_name = "Kontrola HU"
