"""HU control additions: per-user zone (warehouse-type) permission matrix, and the
mandatory confirmation of the customer's special delivery requirements before posting."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import (Shipment, HandlingUnit, HandlingUnitItem, Customer,
                       ControllerZone)
from ui.roles import GROUP_ADMIN, GROUP_LEADER, GROUP_CONTROLLER


def _user(*groups, name="u"):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class ControllerZoneTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.controller = _user(GROUP_CONTROLLER, name="ctrl")
        cls.leader = _user(GROUP_ADMIN, GROUP_LEADER, name="lead")
        cls.sh = Shipment.objects.create(name="Kontener", is_stock=True)
        cls.hu_wms = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HU-WMS",
                                                 warehouse_type="WMS")
        cls.hu_vna = HandlingUnit.objects.create(shipment=cls.sh, seq=2, code="HU-VNA",
                                                 warehouse_type="VNA")

    def test_unconfigured_user_controls_all_zones(self):
        self.assertIsNone(ControllerZone.zones_for(self.controller))
        self.assertTrue(ControllerZone.can_control(self.controller, "VNA"))

    def test_admin_leader_bypass_zone_limits(self):
        # Even with an explicit (irrelevant) row, a leader bypasses → None (all zones).
        ControllerZone.objects.create(user=self.leader, code="WMS")
        self.assertIsNone(ControllerZone.zones_for(self.leader))
        self.assertTrue(ControllerZone.can_control(self.leader, "VNA"))

    def test_subset_limits_control(self):
        ControllerZone.objects.create(user=self.controller, code="WMS")
        self.assertEqual(ControllerZone.zones_for(self.controller), {"WMS"})
        self.assertTrue(ControllerZone.can_control(self.controller, "WMS"))
        self.assertFalse(ControllerZone.can_control(self.controller, "VNA"))

    def test_scan_respects_zone(self):
        ControllerZone.objects.create(user=self.controller, code="WMS")
        self.client.force_login(self.controller)
        r = self.client.post(reverse("ui:hu_control_scan"), {"code": "HU-VNA"})
        self.assertRedirects(r, reverse("ui:hu_control_menu"))          # blocked
        r2 = self.client.post(reverse("ui:hu_control_scan"), {"code": "HU-WMS"})
        self.assertRedirects(r2, reverse("ui:hu_control_detail", args=[self.hu_wms.pk]))

    def test_detail_blocked_for_forbidden_zone(self):
        ControllerZone.objects.create(user=self.controller, code="WMS")
        self.client.force_login(self.controller)
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu_vna.pk]))
        self.assertRedirects(r, reverse("ui:hu_control_menu"))
        self.hu_vna.refresh_from_db()
        self.assertEqual(self.hu_vna.status, "planned")                 # not opened/blocked

    def test_admin_matrix_saves_subset_and_clears(self):
        admin = _user(GROUP_ADMIN, name="adm")
        self.client.force_login(admin)
        self.client.post(reverse("ui:admin_control_zones"),
                         {"user_id": self.controller.pk, "zones": ["WMS"]})
        self.assertEqual(ControllerZone.zones_for(self.controller), {"WMS"})
        # Ticking every known zone clears the config → all zones. Zbiór znanych stref
        # jest dynamiczny (typy z HU + REGAŁOWE kody katalogu — seed 0174 dodał 0010…0070),
        # więc liczymy go tak samo jak widok, zamiast zakładać zamknięty świat.
        from ui.models import HandlingUnit, WarehouseRackType
        all_zones = set(t for t in HandlingUnit.objects.values_list("warehouse_type", flat=True) if t)
        all_zones |= set(WarehouseRackType.objects.filter(kind="rack").values_list("code", flat=True))
        self.client.post(reverse("ui:admin_control_zones"),
                         {"user_id": self.controller.pk, "zones": sorted(all_zones)})
        self.assertIsNone(ControllerZone.zones_for(self.controller))

    def test_matrix_requires_admin(self):
        self.client.force_login(self.controller)
        r = self.client.get(reverse("ui:admin_control_zones"))
        self.assertEqual(r.status_code, 403)


class ClientRequirementConfirmTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.controller = _user(GROUP_CONTROLLER, name="ctrl")
        cls.cust = Customer.objects.create(name="ACME", requires_fumigated_pallet=True)

    def _hu_with_item(self, customer=None):
        sh = Shipment.objects.create(name="Dostawa", customer=customer)
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HU-1", status="in_control",
                                         controlled_by=self.controller)
        HandlingUnitItem.objects.create(hu=hu, ref_code="A1", base_qty=10, base_unit="OP")
        return hu

    def _count_all(self, hu):
        """Policz każdą pozycję zgodnie z oczekiwaniem (liczenie obowiązkowe — B1)."""
        for it in hu.items.all():
            self.client.post(reverse("ui:hu_control_count", args=[hu.pk, it.pk]),
                             {"action": "confirm", "qty_base": str(it.base_qty)})

    def test_finalize_blocked_until_requirements_confirmed(self):
        hu = self._hu_with_item(self.cust)
        self.assertTrue(hu.has_client_requirements)
        self.client.force_login(self.controller)
        self._count_all(hu)                                              # pozycje policzone
        # Pozycje policzone → dochodzi do bramki WYMAGAŃ klienta (fumigacja niepotwierdzona).
        r = self.client.post(reverse("ui:hu_control_finalize", args=[hu.pk]))
        self.assertRedirects(r, reverse("ui:hu_control_detail", args=[hu.pk]))
        hu.refresh_from_db()
        self.assertEqual(hu.status, "in_control")                       # not posted

        # Confirm, then finalize succeeds.
        self.client.post(reverse("ui:hu_control_confirm_reqs", args=[hu.pk]))
        hu.refresh_from_db()
        self.assertIsNotNone(hu.client_reqs_confirmed_at)
        self.assertEqual(hu.client_reqs_confirmed_by, self.controller)
        r2 = self.client.post(reverse("ui:hu_control_finalize", args=[hu.pk]))
        self.assertRedirects(r2, reverse("ui:hu_control_menu"))
        hu.refresh_from_db()
        self.assertEqual(hu.status, "ok")

    def test_finalize_ok_without_requirements(self):
        hu = self._hu_with_item(customer=None)                          # no special reqs
        self.assertFalse(hu.has_client_requirements)
        self.client.force_login(self.controller)
        self._count_all(hu)
        r = self.client.post(reverse("ui:hu_control_finalize", args=[hu.pk]))
        self.assertRedirects(r, reverse("ui:hu_control_menu"))
        hu.refresh_from_db()
        self.assertEqual(hu.status, "ok")
