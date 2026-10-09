from django.apps import AppConfig

class UiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ui"

    def ready(self):
        # Register signal handlers (auto-create UserProfile for every auth.User).
        from . import signals  # noqa: F401
