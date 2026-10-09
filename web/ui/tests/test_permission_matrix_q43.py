"""Macierz uprawnień — rozstrzygnięcie Q-43 (TEST-002, decyzja właściciela 2026-09-28).

Trasy „tylko login” są dostępne dla KAŻDEJ zalogowanej roli, także wąskich (Obsługa klienta,
Magazyn, Kontrola HU, Lider kontroli, Optymalizacja kartonów): osobiste (hub, profil,
wiadomości…) i magazynowe tylko do odczytu (skaner, wyszukiwarka, instrukcja z QR etykiety).
Styleguide /ui/ to narzędzie deweloperskie — tylko Administratorzy. W tests/permissions.yaml
nie zostaje żadne „?”."""
from pathlib import Path

import yaml
from django.test import TestCase
from django.urls import reverse

from testkit.personas import client_for

REPO = Path(__file__).resolve().parents[3]
NARROW = ["Obsługa klienta", "Magazyn", "Kontrola HU", "Lider kontroli", "Optymalizacja kartonów"]
# Trasy bez obiektu w URL — GET każdej wąskiej roli ma przejść (200 albo przekierowanie
# w aplikacji, np. kontroler na skaner), nigdy 403 ani powrót na /login/.
LOGIN_ALL_GET = ["home", "my_profile", "device_select", "messages_inbox", "messages_drawer",
                 "scanner_launcher", "warehouse_search"]


class PermissionsYamlDecidedTests(TestCase):
    def test_no_undecided_entries_left(self):
        spec = yaml.safe_load((REPO / "tests" / "permissions.yaml").read_text(encoding="utf-8"))
        undecided = [name for name, route in spec["trasy"].items()
                     if "?" in (route.get("oczekiwane") or {}).values()]
        self.assertEqual(undecided, [], "„?” w macierzy — decyzja właściciela albo wpis w "
                                        "tests/tools/build_permissions.py")

    def test_styleguide_entry_is_admin_only(self):
        spec = yaml.safe_load((REPO / "tests" / "permissions.yaml").read_text(encoding="utf-8"))
        exp = spec["trasy"]["ui:ui_styleguide"]["oczekiwane"]
        self.assertEqual(exp["Administratorzy"], 200)
        self.assertTrue(all(exp[r] == 403 for r in NARROW))


class LoginOnlyRoutesForNarrowRolesTests(TestCase):
    def test_narrow_roles_open_personal_and_warehouse_routes(self):
        for persona in NARROW:
            client = client_for(persona)
            for name in LOGIN_ALL_GET:
                with self.subTest(persona=persona, route=name):
                    r = client.get(reverse(f"ui:{name}"))
                    self.assertNotEqual(r.status_code, 403)
                    if r.status_code in (301, 302):
                        self.assertNotIn("/login/", r["Location"])
                    else:
                        self.assertEqual(r.status_code, 200)


class StyleguideAdminOnlyTests(TestCase):
    def test_narrow_and_business_roles_get_403(self):
        for persona in NARROW + ["Master Data", "Transport", "Podgląd"]:
            with self.subTest(persona=persona):
                self.assertEqual(client_for(persona).get(reverse("ui:ui_styleguide")).status_code, 403)

    def test_admins_see_styleguide(self):
        for persona in ("Administratorzy", "superuser"):
            with self.subTest(persona=persona):
                self.assertEqual(client_for(persona).get(reverse("ui:ui_styleguide")).status_code, 200)

    def test_anonymous_redirected_to_login(self):
        r = client_for("anon").get(reverse("ui:ui_styleguide"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])
