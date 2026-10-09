"""Wąska rola „Obsługa klienta" — dostęp tylko do bazy klientów."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.platform_modules import modules_for
from ui.roles import GROUP_CLIENT, GROUP_ADMIN, ALL_GROUPS


def _user(*groups):
    User = get_user_model()
    u = User.objects.create_user(f"u{User.objects.count()}", password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class ClientRoleTests(TestCase):
    def setUp(self):
        self.client_user = _user(GROUP_CLIENT)
        self.client.force_login(self.client_user)

    def test_client_can_open_customers(self):
        r = self.client.get(reverse("ui:planner_customers"))
        self.assertEqual(r.status_code, 200)

    def test_client_can_open_customer_form(self):
        r = self.client.get(reverse("ui:planner_customer_new"))
        self.assertEqual(r.status_code, 200)

    def test_client_denied_data_center(self):
        # Data Center (master-data hub) is Master Data / admin only.
        self.assertEqual(self.client.get(reverse("ui:data_center")).status_code, 403)

    def test_client_denied_shipments(self):
        self.assertEqual(self.client.get(reverse("ui:planner_shipments")).status_code, 403)

    def test_client_denied_product_create(self):
        # Read access to products is open, but creating one is Master Data only.
        self.assertEqual(self.client.get(reverse("ui:planner_product_new")).status_code, 403)

    def test_modules_for_client_is_customers_only(self):
        keys = [m.key for m in modules_for(self.client_user)]
        self.assertEqual(keys, ["klienci"])

    def test_group_seeded_in_all_groups(self):
        self.assertIn(GROUP_CLIENT, ALL_GROUPS)

    def test_admin_reaches_customers_via_data_center_only(self):
        # Admin reaches customers inside Data Center, so the standalone "klienci" hub
        # tile is not duplicated for them.
        admin = _user(GROUP_ADMIN)
        keys = [m.key for m in modules_for(admin)]
        self.assertIn("data_center", keys)       # customers live here for admin
        self.assertNotIn("klienci", keys)        # no duplicate hub tile
