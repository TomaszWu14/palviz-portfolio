"""Persony testowe — te z ``tests/permissions.yaml`` (anon, nieaktywny, bez_roli, superuser,
9 grup ról) oraz dwie dodatkowe: ``zablokowany`` (django-axes) i ``nadpisanie_modulu``
(``UserModuleAccess``).

    from testkit.personas import PERSONAS, make, client_for
    client = client_for("Transport")           # zalogowany klient testowy (force_login)
    client = client_for("transport")           # to samo — slug ASCII (dla Windows/make)

``selected()`` zwraca personę z ``GROOVE_TEST_ROLE`` (``make test-role ROLE=…``) albo wszystkie.
"""
import os

from django.conf import settings
from django.test import Client

from core.roles import ALL_GROUPS

from .factories import PASSWORD, UserFactory, UserModuleAccessFactory

LOCKED_IP = "10.66.0.66"            # z tego adresu persona „zablokowany” ma 5 nieudanych prób
OVERRIDE_MODULE = ("transport", True)   # nadpisanie: bez roli, ale z dostępem do Transportu

# slug ASCII → nazwa persony (grupy ról to zamrożony kontrakt z core/roles.py)
SLUGS = {
    "anon": "anon", "nieaktywny": "nieaktywny", "bez_roli": "bez_roli", "superuser": "superuser",
    "admin": "Administratorzy", "master_data": "Master Data", "transport": "Transport",
    "kontrola_hu": "Kontrola HU", "lider": "Lider kontroli", "podglad": "Podgląd",
    "obsluga_klienta": "Obsługa klienta", "magazyn": "Magazyn",
    "optymalizacja": "Optymalizacja kartonów",
    "zablokowany": "zablokowany", "nadpisanie_modulu": "nadpisanie_modulu",
}
PERSONAS = list(SLUGS.values())
assert set(ALL_GROUPS) <= set(PERSONAS), "nowa grupa w core/roles.py — dodaj personę"


def resolve(name):
    """Nazwa persony z nazwy albo slugu (bez wielkości liter). ValueError z listą poprawnych."""
    key = (name or "").strip()
    if key in PERSONAS:
        return key
    if key.lower() in SLUGS:
        return SLUGS[key.lower()]
    raise ValueError(f"Nieznana persona {name!r}. Dostępne: {', '.join(SLUGS)}")


def selected():
    """Persony do przebiegu: jedna z ``GROOVE_TEST_ROLE`` albo wszystkie."""
    env = os.environ.get("GROOVE_TEST_ROLE", "").strip()
    return [resolve(env)] if env else list(PERSONAS)


def username(name):
    return "p_" + next(slug for slug, p in SLUGS.items() if p == name)


def make(name):
    """Użytkownik persony (``None`` dla anon). Idempotentne — drugi raz zwraca tego samego."""
    name = resolve(name)
    if name == "anon":
        return None
    kw = {"username": username(name)}
    if name == "superuser":
        kw.update(is_superuser=True, is_staff=True)
    elif name == "nieaktywny":
        kw["is_active"] = False
    elif name in ALL_GROUPS:
        kw["groups"] = [name]
    user = UserFactory(**kw)
    if name == "zablokowany":
        from axes.models import AccessAttempt
        AccessAttempt.objects.get_or_create(
            username=user.username, ip_address=LOCKED_IP,
            defaults={"failures_since_start": settings.AXES_FAILURE_LIMIT,
                      "user_agent": "testkit", "get_data": "", "post_data": "", "http_accept": "",
                      "path_info": "/login/"})
    elif name == "nadpisanie_modulu":
        UserModuleAccessFactory(user=user, module_key=OVERRIDE_MODULE[0], allowed=OVERRIDE_MODULE[1])
    return user


def client_for(name):
    """Klient testowy persony. Zalogowani przez ``force_login`` (bez axes, szybko); anon,
    nieaktywny i zablokowany zostają niezalogowani — ``zablokowany`` ma REMOTE_ADDR=LOCKED_IP,
    więc jego logowanie formularzem trafia w blokadę axes."""
    name = resolve(name)
    user = make(name)
    if name == "zablokowany":
        return Client(REMOTE_ADDR=LOCKED_IP)
    client = Client()
    if user is not None and user.is_active:
        client.force_login(user)
    return client


def login_form(client, name):
    """Logowanie PRAWDZIWYM formularzem /login/ (axes widzi żądanie) → odpowiedź."""
    return client.post("/login/", {"username": username(resolve(name)), "password": PASSWORD})
