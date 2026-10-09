"""RODO: wyszukanie danych osoby (art. 15) i retencja danych osobowych (art. 5 ust. 1 lit. e).

Rejestr czynności i uzasadnienie: audit/03-RODO-REJESTR.md (audyt GDPR-001/002).
Retencja jest domyślnie WYŁĄCZONA (0 dni) — okresy ustala IOD; włączenie przez env
GDPR_DRIVER_RETAIN_DAYS / GDPR_ACCESSLOG_RETAIN_DAYS."""
from datetime import timedelta

from django.apps import apps
from django.db.models import Q
from django.utils import timezone

# (app_label.Model, pola przeszukiwane, pola zwracane w wyniku)
PERSONAL_FIELDS = [
    ("auth.User", ("username", "email", "first_name", "last_name"), ("username", "email", "first_name", "last_name", "is_active")),
    ("ui.UserProfile", ("phone",), ("user_id", "phone")),
    ("ui.Customer", ("phone", "contact_email"), ("name", "phone", "contact_email")),
    ("ui.CustomerPackagingRule", ("alert_email",), ("customer_id", "alert_email")),
    ("transport.DriverAssignment", ("driver_name", "driver_phone", "driver_plate"), ("shipment_id", "driver_name", "driver_phone", "driver_plate", "created_at")),
    ("transport.QuoteRecipient", ("name", "email"), ("name", "email")),
    ("transport.ShipmentQuoteOffer", ("sender_name", "sender_email"), ("shipment_id", "sender_name", "sender_email", "submitted_at")),
    ("transport.Shipment", ("author_email",), ("name", "author_email")),
]


def find_person(term):
    """Wszystkie rekordy, w których występuje `term` (e-mail, telefon, login, nazwisko, nr rej.).
    Zwraca {"app.Model": [{"pk": .., pola..}]} — tylko modele z trafieniami."""
    term = (term or "").strip()
    if len(term) < 3:
        raise ValueError("Podaj co najmniej 3 znaki.")
    out = {}
    for label, search, show in PERSONAL_FIELDS:
        model = apps.get_model(label)
        q = Q()
        for f in search:
            q |= Q(**{f"{f}__icontains": term})
        rows = [{"pk": r["pk"], **{k: str(v) for k, v in r.items() if k != "pk"}}
                for r in model.objects.filter(q).values("pk", *show)]
        if rows:
            out[label] = rows
    return out


def anonymize_drivers(days):
    """Czyści dane kierowcy (imię, telefon, nr rej.) w przydziałach starszych niż `days`."""
    if not days:
        return 0
    model = apps.get_model("transport.DriverAssignment")
    cutoff = timezone.now() - timedelta(days=days)
    qs = model.objects.filter(created_at__lt=cutoff).exclude(driver_name="", driver_phone="", driver_plate="")
    return qs.update(driver_name="", driver_phone="", driver_plate="")


def purge_access_logs(days):
    """Usuwa logi logowania django-axes (IP, user-agent, login) starsze niż `days`."""
    if not days:
        return 0
    cutoff = timezone.now() - timedelta(days=days)
    n = 0
    for label in ("axes.AccessLog", "axes.AccessFailureLog"):
        n += apps.get_model(label).objects.filter(attempt_time__lt=cutoff).delete()[0]
    return n
