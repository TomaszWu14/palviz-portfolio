"""App signals: auto-create UserProfile + zwrotka do zgłaszającego przy zmianie
statusu zgłoszenia (PackagingIssue/LocationIssue)."""
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import LocationIssue, PackagingIssue, UserProfile


@receiver(post_save, sender=get_user_model())
def ensure_user_profile(sender, instance, created, **kwargs):
    """Create the profile on user creation; get_or_create keeps it idempotent for
    users that pre-date this model (their profile is made on first save)."""
    UserProfile.objects.get_or_create(user=instance)


# ── Zwrotka do zgłaszającego (grill 2026-09-05, pyt. 29/91): status zgłoszenia
# zmieniany w adminie/panelu → reporter dostaje powiadomienie zamiast ciszy.
# Sygnał na modelu = działa niezależnie od tego, GDZIE ktoś zmieni status.

def _stash_old_status(sender, instance, **kwargs):
    if instance.pk:
        old = sender.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
        instance._old_status = old


def _notify_reporter_on_status(sender, instance, created, **kwargs):
    old = getattr(instance, "_old_status", None)
    if created or old is None or old == instance.status or not instance.reporter_id:
        return
    try:
        from .notifications import notify
        label = instance.get_status_display()
        what = getattr(instance, "ref_code", "") or getattr(instance, "location_code", "")
        note = getattr(instance, "resolver_notes", "") or getattr(instance, "resolution", "")
        notify([instance.reporter],
               f"Zgłoszenie {what or f'#{instance.pk}'}: {label}",
               body=note[:300] or "Status Twojego zgłoszenia został zmieniony.",
               level="info", url="/phv/moje/")
    except Exception:
        pass       # powiadomienie to dodatek — nigdy nie wywraca zapisu


for _model in (PackagingIssue, LocationIssue):
    pre_save.connect(_stash_old_status, sender=_model,
                     dispatch_uid=f"issue_status_old_{_model.__name__}")
    post_save.connect(_notify_reporter_on_status, sender=_model,
                      dispatch_uid=f"issue_status_notify_{_model.__name__}")
