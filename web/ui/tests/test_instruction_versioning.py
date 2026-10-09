"""Editing a palletization instruction: overwrite the version vs. branch a new one."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Product, PalletizationInstruction
from ui.roles import ALL_GROUPS


def _user_all_roles():
    u = get_user_model().objects.create_user(username="ed", password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class InstructionVersioningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.product = Product.objects.create(code="DMOM10001", name="Rękawice M")
        cls.instr = PalletizationInstruction.objects.create(
            product=cls.product, version=1, name="Import migracji",
            carton_l=29, carton_w=25, carton_h=22, unit_weight=0.5,
            pcs_per_carton=10, demand_pcs=1000)

    def setUp(self):
        self.client.force_login(self.user)

    def _post(self, save_mode, pcs):
        return self.client.post(
            reverse("ui:planner_instruction_edit", args=[self.instr.pk]),
            {
                "product": self.product.pk, "version": 1, "name": "Edycja",
                "pallet_code": "EU", "max_height_total_cm": 215, "max_weight_kg": 1000,
                "carton_l": 29, "carton_w": 25, "carton_h": 22,
                "unit_weight": "0.5", "pcs_per_carton": pcs, "carton_tare": "0",
                "demand_pcs": 1000, "is_active": "on", "save_mode": save_mode,
            })

    def test_overwrite_keeps_single_version(self):
        resp = self._post("overwrite", pcs=12)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.product.instructions.count(), 1)
        self.instr.refresh_from_db()
        self.assertEqual(self.instr.pcs_per_carton, 12)        # in place

    def test_new_version_branches_and_keeps_original(self):
        resp = self._post("new_version", pcs=14)
        self.assertEqual(resp.status_code, 302)
        versions = sorted(self.product.instructions.values_list("version", flat=True))
        self.assertEqual(versions, [1, 2])                     # original kept + new
        self.instr.refresh_from_db()
        self.assertEqual(self.instr.pcs_per_carton, 10)        # v1 untouched
        v2 = self.product.instructions.get(version=2)
        self.assertEqual(v2.pcs_per_carton, 14)
