"""BLOK G — master data użytkowników: stanowiska (Position → grupy) + lider.

Warstwa pośrednia nad zamrożonym kontraktem grup: przypisanie stanowiska
synchronizuje grupy konta, widoki bez zmian. Seed 11 stanowisk w migracji 0180."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Position, UserProfile
from ui.roles import (ALL_GROUPS, GROUP_ADMIN, GROUP_CONTROLLER, GROUP_LEADER,
                      GROUP_WAREHOUSE)


def _admin():
    u = get_user_model().objects.create_user(username="adm-pos", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_ADMIN)[0])
    return u


class PositionSeedTests(TestCase):
    def test_seed_contains_accepted_positions(self):
        names = set(Position.objects.values_list("name", flat=True))
        for expected in ("Picker", "Kontroler HU", "Lider kontroli",
                         "Planista (Master Data)", "Administrator"):
            self.assertIn(expected, names)
        self.assertGreaterEqual(len(names), 11)

    def test_seed_groups_use_frozen_contract_names(self):
        lider = Position.objects.get(name="Lider kontroli")
        self.assertEqual(set(lider.groups.values_list("name", flat=True)),
                         {GROUP_LEADER, GROUP_CONTROLLER})
        for pos in Position.objects.all():
            for g in pos.groups.all():
                self.assertIn(g.name, ALL_GROUPS)


class PositionApplyTests(TestCase):
    def test_assigning_position_in_panel_syncs_groups_and_leader(self):
        admin = _admin()
        leader = get_user_model().objects.create_user(username="lider-x", password="x")
        pos = Position.objects.get(name="Picker")
        self.client.force_login(admin)
        r = self.client.post(reverse("ui:admin_user_new"), {
            "username": "nowy-picker", "password1": "Trudne#Haslo9", "password2": "Trudne#Haslo9",
            "is_active": "1", "position": str(pos.pk), "leader": str(leader.pk)})
        self.assertEqual(r.status_code, 302)
        u = get_user_model().objects.get(username="nowy-picker")
        self.assertEqual(set(u.groups.values_list("name", flat=True)), {GROUP_WAREHOUSE})
        self.assertEqual(u.profile.position, pos)
        self.assertEqual(u.profile.leader, leader)

    def test_editing_position_reapplies_groups_to_members(self):
        """Kryterium „bez wdrożenia kodu": zmiana definicji stanowiska w panelu
        przelicza grupy WSZYSTKICH przypisanych kont."""
        admin = _admin()
        pos = Position.objects.get(name="Picker")
        member = get_user_model().objects.create_user(username="czlonek", password="x")
        prof, _ = UserProfile.objects.get_or_create(user=member)
        prof.position = pos
        prof.save(update_fields=["position"])
        pos.apply_to(member)
        self.assertEqual(set(member.groups.values_list("name", flat=True)), {GROUP_WAREHOUSE})
        self.client.force_login(admin)
        kontrola = Group.objects.get_or_create(name=GROUP_CONTROLLER)[0]
        r = self.client.post(reverse("ui:admin_position_edit", args=[pos.pk]), {
            "name": "Picker", "description": "x", "order": "10", "is_active": "1",
            "groups": [str(kontrola.pk)]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(set(member.groups.values_list("name", flat=True)), {GROUP_CONTROLLER})

    def test_positions_matrix_renders(self):
        self.client.force_login(_admin())
        r = self.client.get(reverse("ui:admin_positions"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Picker")
        self.assertContains(r, "Stanowiska")

    def test_duplicate_name_rejected(self):
        self.client.force_login(_admin())
        r = self.client.post(reverse("ui:admin_position_new"), {
            "name": "Picker", "order": "5", "is_active": "1"})
        self.assertEqual(r.status_code, 200)          # formularz z błędem, bez zapisu
        self.assertEqual(Position.objects.filter(name="Picker").count(), 1)
