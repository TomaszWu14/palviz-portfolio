# Fala 2: widok HU (palety) — sortowanie, grupowanie, zakładki kompletacji.
from datetime import date

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import GROUP_MASTER_DATA


class HuListViewTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_MASTER_DATA)
        u = User.objects.create_user("md", "md@example.com", "Zx9!longpass")
        u.groups.add(g)
        self.client.force_login(u)
        vip = Customer.objects.create(name="Demoprime", is_vip=True)
        std = Customer.objects.create(name="Szpital X")
        sh_new = Shipment.objects.create(name="D-NEW", customer=vip,
                                         outbound_created_date=date(2026, 8, 4),
                                         wz_number="WZ/1")
        sh_old = Shipment.objects.create(name="D-OLD", customer=std,
                                         outbound_created_date=date(2026, 8, 1))
        self.h_new = HandlingUnit.objects.create(shipment=sh_new, code="HU-NEW",
                                                 is_completed=True, warehouse_type="P1")
        self.h_old = HandlingUnit.objects.create(shipment=sh_old, code="HU-OLD",
                                                 warehouse_type="P2")
        self.url = reverse("ui:planner_stock_contents")

    def _codes(self, resp):
        return [h.code for h in resp.context["page_obj"].object_list]

    def test_default_sort_newest_first(self):
        r = self.client.get(self.url, {"view": "hu"})
        self.assertEqual(self._codes(r), ["HU-NEW", "HU-OLD"])

    def test_sort_oldest_and_vip(self):
        r = self.client.get(self.url, {"view": "hu", "sort": "created"})
        self.assertEqual(self._codes(r), ["HU-OLD", "HU-NEW"])
        r = self.client.get(self.url, {"view": "hu", "sort": "vip"})
        self.assertEqual(self._codes(r)[0], "HU-NEW")     # VIP pierwszy

    def test_tabs_split_by_completion(self):
        r = self.client.get(self.url, {"view": "hu", "tab": "completed"})
        self.assertEqual(self._codes(r), ["HU-NEW"])
        self.assertEqual(r.context["n_open"], 1)
        self.assertEqual(r.context["n_completed"], 1)
        r = self.client.get(self.url, {"view": "hu", "tab": "open"})
        self.assertEqual(self._codes(r), ["HU-OLD"])

    def test_grouping_by_zone_and_vip(self):
        r = self.client.get(self.url, {"view": "hu", "group": "zone"})
        rows = {g["value"]: g["n"] for g in r.context["group_rows"]}
        self.assertEqual(rows, {"P1": 1, "P2": 1})
        r = self.client.get(self.url, {"view": "hu", "group": "vip"})
        rows = {g["value"]: g["n"] for g in r.context["group_rows"]}
        self.assertEqual(rows, {True: 1, False: 1})

    def test_group_expansion_filters(self):
        r = self.client.get(self.url, {"view": "hu", "vip": "1"})
        self.assertEqual(self._codes(r), ["HU-NEW"])
        r = self.client.get(self.url, {"view": "hu", "day": "2026-08-01"})
        self.assertEqual(self._codes(r), ["HU-OLD"])

    def test_items_mode_regression(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["mode"], "items")

    def test_group_by_recipient(self):
        r = self.client.get(self.url, {"view": "hu", "group": "recipient"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("group_rows", r.context)

    def test_batch_call_ready_for_recipient(self):
        from django.urls import reverse
        sh_id = self.h_new.shipment_id
        self.client.post(reverse("ui:hu_call_batch"), {"shipment_id": sh_id})
        self.h_new.refresh_from_db()
        self.assertIsNotNone(self.h_new.called_at)  # gotowa (is_completed) → wywołana
