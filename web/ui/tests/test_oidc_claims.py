"""SSO — synchronizacja grup i is_staff w GrooveOIDCBackend (ui/oidc.py, TEST-004).

Uzupełnia test_oidc.py (czysta funkcja groups_from_claims) o ścieżkę create_user/update_user.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import SuspiciousOperation
from django.test import TestCase, override_settings

from ui.oidc import GrooveOIDCBackend, groups_from_claims
from ui.roles import ALL_GROUPS, GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_VIEWER

OIDC_SETTINGS = {
    "OIDC_RP_CLIENT_ID": "groove",
    "OIDC_RP_CLIENT_SECRET": "secret",
    "OIDC_OP_TOKEN_ENDPOINT": "https://idp.invalid/token",
    "OIDC_OP_USER_ENDPOINT": "https://idp.invalid/userinfo",
    "OIDC_RP_SIGN_ALGO": "HS256",
}


class GroupsFromClaimsEdgeTests(TestCase):
    def test_duplicates_whitespace_and_non_string_items(self):
        claims = {"groups": [" /Master Data ", "Master Data", 42, None, "/Podgląd"]}
        self.assertEqual(groups_from_claims(claims), [GROUP_MASTER_DATA, GROUP_VIEWER])

    def test_claim_present_but_null(self):
        self.assertEqual(groups_from_claims({"groups": None}), [])

    def test_all_known_groups_accepted(self):
        self.assertEqual(groups_from_claims({"groups": list(ALL_GROUPS)}), list(ALL_GROUPS))


@override_settings(**OIDC_SETTINGS)
class GrooveOIDCBackendTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name in ALL_GROUPS:
            Group.objects.get_or_create(name=name)

    def setUp(self):
        self.backend = GrooveOIDCBackend()

    def _names(self, user):
        return sorted(user.groups.values_list("name", flat=True))

    def test_create_user_maps_known_claims_and_grants_staff_for_admin(self):
        claims = {"email": "anna@example.com", "groups": ["/Administratorzy", "/Magazyn", "/Obca"]}
        user = self.backend.create_user(claims)
        user.refresh_from_db()
        self.assertEqual(self._names(user), sorted([GROUP_ADMIN, "Magazyn"]))
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)  # superuser nigdy przez SSO

    def test_create_user_missing_claim_refused(self):
        # SEC-016: brak claimu ról = brak konta (nie zakładamy kont bez roli).
        with self.assertRaises(SuspiciousOperation):
            self.backend.create_user({"email": "jan@example.com"})
        self.assertFalse(get_user_model().objects.filter(email="jan@example.com").exists())

    def test_create_user_only_unknown_claims_refused(self):
        with self.assertRaises(SuspiciousOperation):
            self.backend.create_user({"email": "x@example.com", "groups": ["/Obca", "Inna"]})
        self.assertFalse(get_user_model().objects.filter(email="x@example.com").exists())

    def test_update_user_replaces_groups_and_revokes_staff(self):
        User = get_user_model()
        user = User.objects.create_user("ewa", email="ewa@example.com", is_staff=True)
        user.groups.add(Group.objects.get(name=GROUP_ADMIN))
        self.backend.update_user(user, {"email": "ewa@example.com", "groups": ["/Podgląd"]})
        user.refresh_from_db()
        self.assertEqual(self._names(user), [GROUP_VIEWER])
        self.assertFalse(user.is_staff)

    def test_update_user_keeps_superuser_flag(self):
        User = get_user_model()
        user = User.objects.create_superuser("root", email="root@example.com", password="x")
        self.backend.update_user(user, {"groups": []})
        user.refresh_from_db()
        self.assertTrue(user.is_superuser)
        self.assertFalse(user.is_staff)  # zgodnie z implementacją: is_staff = członkostwo w Administratorzy

    @override_settings(OIDC_ROLES_CLAIM="roles")
    def test_custom_roles_claim_setting(self):
        user = self.backend.create_user({"email": "r@example.com", "roles": ["Master Data"], "groups": ["Magazyn"]})
        self.assertEqual(self._names(user), [GROUP_MASTER_DATA])
