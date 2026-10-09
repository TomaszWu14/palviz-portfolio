from django.apps import AppConfig


class CoreConfig(AppConfig):
    """core — shared kernel (bez modeli, zero migracji): role/SSO-słownik,
    middleware, context processors, rejestr modułów platformy (hub) i health.

    Faza 8 wydzielenia: realny dom współdzielonej infrastruktury przeniesiony
    z `ui`; stare ścieżki (`ui.roles`, `ui.platform_modules`) działają dalej przez cienkie shimy
    re-eksportujące — kontrakt SSO (`oidc.py`) i 160+ importerów bez zmian.
    Modele zostają w `ui` (stąd runtime-only importy `ui.models` w tych plikach)."""
    name = "core"
    verbose_name = "Wspólny rdzeń"
