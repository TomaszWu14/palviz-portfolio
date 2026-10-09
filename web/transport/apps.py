from django.apps import AppConfig


class TransportConfig(AppConfig):
    """Wycena przesyłek (transport) — wydzielona aplikacja: przesyłki, wycena frachtu,
    przewoźnicy, dyspozycja kierowców (SMS/formularz), KPI transportu.

    W1: widoki+szablony; modele nadal w ui.models (W2 = SeparateDatabaseAndState).
    URL-e w ui.urls (namespace ui:), ścieżki szablonów bez zmian; współdzielone
    helpery (pakery 3D) zostają w ui.views.core."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "transport"
    verbose_name = "Wycena przesyłek"
