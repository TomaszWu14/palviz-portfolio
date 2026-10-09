"""Reguła „druga para oczu": rekontroli NIGDY nie robi autor pierwotnej kontroli.

Do 2026-08-26 lider/admin (i superuser przez has_role) omijał strażnika i mógł
rekontrolować własną kontrolę. Decyzja usera: zasada obowiązuje każdą rolę —
lider/admin/superuser może rekontrolować wyłącznie CUDZE HU.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import HandlingUnit, HandlingUnitItem, HUControlAttempt, Shipment
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


def _user(name, group=None, superuser=False):
    U = get_user_model()
    u = (U.objects.create_superuser(username=name, password="x", email="")
         if superuser else U.objects.create_user(username=name, password="x"))
    if group:
        u.groups.add(Group.objects.get_or_create(name=group)[0])
    return u


class RecheckSecondPairTests(TestCase):
    def setUp(self):
        self.first = _user("pierwszy", GROUP_CONTROLLER)
        self.sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(
            shipment=self.sh, seq=1, code="RC1", status="to_recheck", warehouse_type="WT01")
        self.item = HandlingUnitItem.objects.create(
            hu=self.hu, ref_code="RG-50", base_unit="OP", base_qty=10,
            controlled=True, result="error")
        # Audyt: pierwotną kontrolę (nie-recheck) zrobił `pierwszy`.
        HUControlAttempt.objects.create(hu=self.hu, item=self.item,
                                        controller=self.first, is_recheck=False)

    def _count_as(self, user):
        self.client.force_login(user)
        return self.client.post(
            reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
            {"action": "confirm", "qty_base": "10"}, follow=True)

    def test_original_controller_blocked(self):
        r = self._count_as(self.first)
        self.assertContains(r, "Rekontrolę musi wykonać inny kontroler")

    def test_original_leader_blocked_even_with_role(self):
        # Autor pierwotnej kontroli awansowany na lidera dalej nie rekontroluje SWOJEJ HU.
        self.first.groups.add(Group.objects.get_or_create(name=GROUP_LEADER)[0])
        r = self._count_as(self.first)
        self.assertContains(r, "Rekontrolę musi wykonać inny kontroler")

    def test_original_superuser_blocked(self):
        boss = _user("root", superuser=True)
        HUControlAttempt.objects.all().delete()
        HUControlAttempt.objects.create(hu=self.hu, item=self.item,
                                        controller=boss, is_recheck=False)
        r = self._count_as(boss)
        self.assertContains(r, "Rekontrolę musi wykonać inny kontroler")

    def test_other_controller_allowed(self):
        second = _user("drugi", GROUP_CONTROLLER)
        r = self._count_as(second)
        self.assertNotContains(r, "Rekontrolę musi wykonać inny kontroler")

    def test_leader_allowed_on_foreign_hu(self):
        leader = _user("lider", GROUP_LEADER)
        r = self._count_as(leader)
        self.assertNotContains(r, "Rekontrolę musi wykonać inny kontroler")
