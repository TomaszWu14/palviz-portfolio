"""SSO / OIDC — the claim→group mapping (pure) and that SSO stays OFF by default."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import SuspiciousOperation
from django.test import TestCase, override_settings
from django.urls import reverse

from ui.oidc import groups_from_claims
from ui.roles import GROUP_ADMIN, GROUP_WAREHOUSE


class GroupsFromClaimsTests(TestCase):
    def test_keycloak_group_paths_map_to_palviz_groups(self):
        claims = {"groups": ["/Administratorzy", "/Magazyn", "/Nieznana grupa"]}
        self.assertEqual(groups_from_claims(claims), [GROUP_ADMIN, GROUP_WAREHOUSE])  # unknown dropped

    def test_plain_names_and_single_string(self):
        self.assertEqual(groups_from_claims({"groups": "Administratorzy"}), [GROUP_ADMIN])
        self.assertEqual(groups_from_claims({"groups": ["Magazyn"]}), [GROUP_WAREHOUSE])

    def test_custom_claim_and_empty(self):
        self.assertEqual(groups_from_claims({"roles": ["Magazyn"]}, "roles"), [GROUP_WAREHOUSE])
        self.assertEqual(groups_from_claims({}, "groups"), [])
        self.assertEqual(groups_from_claims(None), [])


@override_settings(OIDC_OP_TOKEN_ENDPOINT="https://sso.example.test/token",
                   OIDC_OP_USER_ENDPOINT="https://sso.example.test/userinfo",
                   OIDC_RP_CLIENT_ID="groove", OIDC_RP_CLIENT_SECRET="s")
class OidcBackendHardeningTests(TestCase):
    """SEC-016: email_verified wymagany (gdy podany) i brak kont bez ról."""

    def setUp(self):
        from ui.oidc import GrooveOIDCBackend
        self.backend = GrooveOIDCBackend()

    def test_unverified_email_rejected(self):
        self.assertFalse(self.backend.verify_claims({"email": "a@x.pl", "email_verified": False}))
        self.assertFalse(self.backend.verify_claims({"email": "a@x.pl", "email_verified": "false"}))

    def test_verified_or_absent_email_verified_accepted(self):
        self.assertTrue(self.backend.verify_claims({"email": "a@x.pl", "email_verified": True}))
        self.assertTrue(self.backend.verify_claims({"email": "a@x.pl"}))

    def test_no_account_created_without_roles(self):
        with self.assertRaises(SuspiciousOperation):
            self.backend.create_user({"email": "nowy@x.pl", "groups": ["/Nieznana grupa"]})
        self.assertFalse(get_user_model().objects.filter(email="nowy@x.pl").exists())

    def test_account_created_with_role(self):
        Group.objects.get_or_create(name=GROUP_WAREHOUSE)
        user = self.backend.create_user({"email": "mag@x.pl", "groups": ["/Magazyn"]})
        self.assertEqual(list(user.groups.values_list("name", flat=True)), [GROUP_WAREHOUSE])


class OidcDisabledByDefaultTests(TestCase):
    def test_sso_off_by_default(self):
        self.assertFalse(settings.OIDC_ENABLED)                          # opt-in
        self.assertNotIn("mozilla_django_oidc", settings.INSTALLED_APPS)
        self.assertNotIn("ui.oidc.GrooveOIDCBackend", settings.AUTHENTICATION_BACKENDS)

    def test_login_page_has_no_sso_button_when_disabled(self):
        r = self.client.get(reverse("ui:login"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Zaloguj przez SSO")
