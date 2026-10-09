"""Frozen-contract guard for the group-name strings in ui/roles.py.

These string literals are Django auth_group row values AND the vocabulary the SSO
backend (oidc.py) maps IdP claims onto. Changing a value silently strips access
and breaks SSO sync. This test pins the exact 8 values so any edit fails CI.
Adding a new group? Add its constant here too. See memory: genuine-cut-vertices.
"""
from django.test import SimpleTestCase

from ui import roles


class GroupStringContractTests(SimpleTestCase):
    # The frozen contract. Do NOT change a value to fix this test — that IS the
    # breakage. Only add a new entry when a genuinely new group is introduced.
    FROZEN = {
        "GROUP_ADMIN":       "Administratorzy",
        "GROUP_MASTER_DATA": "Master Data",
        "GROUP_TRANSPORT":   "Transport",
        "GROUP_CONTROLLER":  "Kontrola HU",
        "GROUP_LEADER":      "Lider kontroli",
        "GROUP_VIEWER":      "Podgląd",
        "GROUP_CLIENT":      "Obsługa klienta",
        "GROUP_WAREHOUSE":   "Magazyn",
        "GROUP_OPTIMIZER":   "Optymalizacja kartonów",
    }

    def test_group_strings_unchanged(self):
        for name, value in self.FROZEN.items():
            self.assertEqual(getattr(roles, name), value, f"{name} string changed — frozen contract")

    def test_all_groups_is_exactly_the_frozen_set(self):
        # ALL_GROUPS must list every group once and nothing extra (uniqueness +
        # completeness — this is what SSO iterates over).
        self.assertEqual(sorted(roles.ALL_GROUPS), sorted(self.FROZEN.values()))
        self.assertEqual(len(roles.ALL_GROUPS), len(set(roles.ALL_GROUPS)))
