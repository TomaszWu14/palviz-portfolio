from django.apps import AppConfig


class Wh3dConfig(AppConfig):
    """Magazyn 3D (wh3d) — wydzielona aplikacja: model hali, mapa lokalizacji,
    edytor layoutu, master lokalizacji, heatmapa, typy regałów.

    W1 wydzielenia (plan wh3d): widoki+szablony w osobnej aplikacji; modele
    nadal w ui.models (przenosiny = W2, SeparateDatabaseAndState). URL-e i
    ścieżki szablonów bez zmian (namespace ui:, templates ui/warehouse_*) —
    zero churnu w linkach; granicę pilnuje test_wh3d_boundary."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "wh3d"
    verbose_name = "Magazyn 3D"
