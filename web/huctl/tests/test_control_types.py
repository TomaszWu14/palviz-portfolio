"""Control-hub warehouse-type map: leaders pick which warehouse types are under HU
control, and that selection filters the control screens + the scan entry."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Shipment, HandlingUnit, ControlledWarehouseType
from ui.roles import GROUP_ADMIN, GROUP_LEADER, GROUP_CONTROLLER


def _user(*groups):
    u = get_user_model().objects.create_user(username="u" + groups[0], password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class ControlledWarehouseTypeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.leader = _user(GROUP_ADMIN, GROUP_LEADER)
        cls.controller = _user(GROUP_CONTROLLER)
        cls.sh = Shipment.objects.create(name="Kontener", is_stock=True)
        cls.hu_wms = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HU-WMS",
                                                 warehouse_type="WMS", status="to_recheck")
        cls.hu_vna = HandlingUnit.objects.create(shipment=cls.sh, seq=2, code="HU-VNA",
                                                 warehouse_type="VNA", status="to_recheck")

    def test_codes_none_when_unconfigured(self):
        self.assertIsNone(ControlledWarehouseType.controlled_codes())

    def test_hub_lists_warehouse_types(self):
        self.client.force_login(self.leader)
        r = self.client.get(reverse("ui:hu_control_hub"))
        self.assertContains(r, "WMS")
        self.assertContains(r, "VNA")
        self.assertContains(r, "Typy magazynów")

    def test_leader_saves_subset_and_it_filters(self):
        self.client.force_login(self.leader)
        self.client.post(reverse("ui:hu_control_types"),
                         {"wh_types": ["WMS"], "all_types": ["WMS", "VNA"]})
        self.assertEqual(ControlledWarehouseType.controlled_codes(), {"WMS"})
        # Recheck list now shows only the controlled type
        r = self.client.get(reverse("ui:hu_control_recheck_list"))
        self.assertContains(r, "HU-WMS")
        self.assertNotContains(r, "HU-VNA")

    def test_selecting_all_clears_config(self):
        ControlledWarehouseType.objects.create(code="WMS")
        self.client.force_login(self.leader)
        self.client.post(reverse("ui:hu_control_types"),
                         {"wh_types": ["WMS", "VNA"], "all_types": ["WMS", "VNA"]})
        self.assertIsNone(ControlledWarehouseType.controlled_codes())   # all controlled again

    def test_scan_blocks_uncontrolled_type(self):
        ControlledWarehouseType.objects.create(code="WMS")              # only WMS controlled
        self.client.force_login(self.leader)
        r = self.client.post(reverse("ui:hu_control_scan"), {"code": "HU-VNA"})
        self.assertRedirects(r, reverse("ui:hu_control_menu"))          # not opened
        r2 = self.client.post(reverse("ui:hu_control_scan"), {"code": "HU-WMS"})
        self.assertRedirects(r2, reverse("ui:hu_control_detail", args=[self.hu_wms.pk]))

    def test_non_leader_cannot_save(self):
        self.client.force_login(self.controller)
        r = self.client.post(reverse("ui:hu_control_types"),
                             {"wh_types": ["WMS"], "all_types": ["WMS", "VNA"]})
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(ControlledWarehouseType.controlled_codes())   # unchanged

    def test_hub_totals_count_only_controlled_types(self):
        """The hub 'to control' totals must sum only the CONTROLLED warehouse types, not
        every type found in the feed. Both HUs are to_recheck; controlling only WMS → 1."""
        ControlledWarehouseType.objects.create(code="WMS")     # only WMS controlled
        self.client.force_login(self.leader)
        r = self.client.get(reverse("ui:hu_control_hub"))
        self.assertEqual(r.context["status_counts"]["to_recheck"], 1)   # WMS only, not VNA

    def test_leader_panel_counts_only_controlled_types(self):
        """Panel lidera: rekontrole i „zaplanowane bez przydziału" liczą TYLKO kontrolowane
        typy, nie sumy HU całego magazynu w każdym typie."""
        HandlingUnit.objects.create(shipment=self.sh, seq=10, code="HU-P-WMS",
                                    warehouse_type="WMS", status="planned")
        HandlingUnit.objects.create(shipment=self.sh, seq=11, code="HU-P-VNA",
                                    warehouse_type="VNA", status="planned")
        ControlledWarehouseType.objects.create(code="WMS")
        self.client.force_login(self.leader)
        r = self.client.get(reverse("ui:hu_control_leader"))
        self.assertEqual(r.context["planned_unassigned"], 1)            # tylko WMS
        self.assertEqual(len(r.context["rechecks"]), 1)                 # tylko HU-WMS

    def test_tv_scoped_and_extended(self):
        """TV: liczniki tylko z kontrolowanych typów; nowe elementy — działy z kolorami,
        przełącznik zakresu, licznik aktywnych kontrolerów."""
        ControlledWarehouseType.objects.create(code="WMS")
        self.client.force_login(self.leader)
        r = self.client.get(reverse("ui:hu_control_tv"))
        self.assertEqual(r.context["status_counts"]["to_recheck"], 1)   # WMS only
        self.assertEqual([t["code"] for t in r.context["type_rows"]], ["WMS"])
        self.assertIn("color", r.context["type_rows"][0])
        self.assertEqual(r.context["active_controllers"], 0)
        self.assertContains(r, "?range=week")
        r2 = self.client.get(reverse("ui:hu_control_tv"), {"range": "week"})
        self.assertEqual(r2.context["range"], "week")

    def test_find_recipient_type_dropdown_is_deduplicated(self):
        """Many HUs share a warehouse type; the "Typ magazynu" dropdown must list each
        type ONCE. The model orders by (shipment, seq), which used to leak into the
        DISTINCT and produce one row per HU instead of per type."""
        for seq in range(3, 8):                       # extra WMS HUs (seqs 1/2 already exist)
            HandlingUnit.objects.create(shipment=self.sh, seq=seq,
                                        code=f"HU-WMS-{seq}", warehouse_type="WMS")
        self.client.force_login(self.controller)
        r = self.client.get(reverse("ui:hu_control_find_recipient"))
        wh_types = r.context["wh_types"]
        self.assertEqual(wh_types, ["VNA", "WMS"])     # unique + sorted, no repeats
        self.assertEqual(len(wh_types), len(set(wh_types)))
