"""Regression tests for role-based access control on planner write views.

Read views are open to any member of one of the 9 roles; data-mutation views require the
domain's write role (Master Data / Transport). See ui/roles.py.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.roles import GROUP_MASTER_DATA, GROUP_TRANSPORT, GROUP_VIEWER


def _user(username, group_name):
    User = get_user_model()
    u = User.objects.create_user(username=username, password="x")
    u.groups.add(Group.objects.get_or_create(name=group_name)[0])
    return u


class RoleEnforcementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.viewer = _user("viewer", GROUP_VIEWER)
        cls.md = _user("md", GROUP_MASTER_DATA)
        cls.tr = _user("tr", GROUP_TRANSPORT)

    # ── Master-data write views ──────────────────────────────────────────────
    def test_viewer_blocked_from_product_form(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("ui:planner_product_new")).status_code, 403)

    def test_master_data_allowed_product_form(self):
        self.client.force_login(self.md)
        self.assertEqual(self.client.get(reverse("ui:planner_product_new")).status_code, 200)

    def test_viewer_blocked_from_carton_form(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("ui:planner_carton_new")).status_code, 403)

    # ── Transport write views ────────────────────────────────────────────────
    def test_viewer_blocked_from_carrier_form(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("ui:planner_carrier_new")).status_code, 403)

    def test_master_data_blocked_from_carrier_form(self):
        # carriers are a transport-domain write — master-data must not pass
        self.client.force_login(self.md)
        self.assertEqual(self.client.get(reverse("ui:planner_carrier_new")).status_code, 403)

    def test_transport_allowed_carrier_form(self):
        self.client.force_login(self.tr)
        self.assertEqual(self.client.get(reverse("ui:planner_carrier_new")).status_code, 200)

    # ── Read views stay open to any authenticated user ───────────────────────
    def test_viewer_can_read_dashboard(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("ui:planner_dashboard")).status_code, 200)

    def test_viewer_can_read_products_list(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("ui:planner_products")).status_code, 200)

    def test_viewer_blocked_from_shipments_read(self):
        # shipments_read is ADMIN+TRANSPORT only (roles.MODULE_ROLES)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("ui:planner_shipments")).status_code, 403)

    def test_transport_allowed_shipments_read(self):
        self.client.force_login(self.tr)
        self.assertEqual(self.client.get(reverse("ui:planner_shipments")).status_code, 200)


class AnonymousGateTests(TestCase):
    """P5: żaden wrażliwy endpoint nie serwuje 200 anonimowemu użytkownikowi.
    Nowy moduł/endpoint → dopisz go tutaj (widoczny checklist bramek)."""
    GET_ENDPOINTS = [
        "planner_dashboard", "planner_products", "planner_shipments", "data_center",
        "warehouse_map", "hu_control_hub", "hu_print_home", "tasks_home",
        "planner_customers", "zaria_home",
        "admin_panel", "admin_users", "admin_module_access", "admin_role_matrix",
        "admin_access_audit",
    ]
    POST_ENDPOINTS = ["zaria_api_chat"]

    def test_anonymous_never_gets_200(self):
        for name in self.GET_ENDPOINTS:
            with self.subTest(endpoint=name):
                self.assertNotEqual(self.client.get(reverse("ui:" + name)).status_code, 200)
        for name in self.POST_ENDPOINTS:
            with self.subTest(endpoint=name, method="POST"):
                self.assertNotEqual(self.client.post(reverse("ui:" + name)).status_code, 200)


class AccessAuditSSOAdminTests(TestCase):
    """P3 audyt zmian ról, P4 blokada edycji przy SSO, P6 spójny is_app_admin."""
    @classmethod
    def setUpTestData(cls):
        from ui.roles import GROUP_ADMIN
        cls.admin = _user("aud_admin", GROUP_ADMIN)          # grupa, NIE superuser
        cls.target = get_user_model().objects.create_user("aud_target", password="x")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_role_change_is_audited_with_diff(self):
        from ui.models import AccessAudit
        md = Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0]
        r = self.client.post(reverse("ui:admin_user_edit", args=[self.target.pk]),
                             {"username": "aud_target", "groups": [md.pk], "is_active": "1"})
        self.assertEqual(r.status_code, 302)
        self.target.refresh_from_db()
        self.assertTrue(self.target.groups.filter(name=GROUP_MASTER_DATA).exists())
        a = AccessAudit.objects.filter(action="role_change", target_user=self.target).first()
        self.assertIsNotNone(a)
        self.assertIn(GROUP_MASTER_DATA, a.detail)   # diff zawiera dodaną rolę

    def test_module_access_matrix_is_audited(self):
        from ui.models import AccessAudit
        # Optimistic lock: zapis wymaga aktualnego tokenu (pobranego z GET).
        token = self.client.get(reverse("ui:admin_module_access")).context["matrix_token"]
        r = self.client.post(reverse("ui:admin_module_access"), {"matrix_token": token})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(AccessAudit.objects.filter(action="module_access").exists())

    def test_access_audit_page_renders(self):
        self.assertEqual(self.client.get(reverse("ui:admin_access_audit")).status_code, 200)

    def test_sso_locks_role_editing(self):
        from django.test import override_settings
        from ui.models import AccessAudit
        md = Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0]
        with override_settings(OIDC_ENABLED=True):
            r = self.client.post(reverse("ui:admin_user_edit", args=[self.target.pk]),
                                 {"username": "aud_target", "groups": [md.pk], "is_active": "1"})
        self.assertEqual(r.status_code, 302)
        self.target.refresh_from_db()
        self.assertFalse(self.target.groups.filter(name=GROUP_MASTER_DATA).exists())  # SSO owns it
        self.assertFalse(AccessAudit.objects.filter(action="role_change").exists())


class PlannerRequiresRoleTests(TestCase):
    """SEC-004/ACL-004: ekrany planera (_planner) wymagają dowolnej z 9 ról — samo
    zalogowanie (konto bez grupy, np. z SSO) nie wystarcza; profil zostaje dostępny."""
    @classmethod
    def setUpTestData(cls):
        cls.nogroup = get_user_model().objects.create_user(username="bez_grupy", password="x")
        cls.viewer = _user("viewer_p", GROUP_VIEWER)

    def test_no_group_user_gets_403_on_planner_and_magazyn(self):
        self.client.force_login(self.nogroup)
        self.assertEqual(self.client.get("/planner/products/").status_code, 403)
        self.assertEqual(self.client.get("/magazyn/").status_code, 403)

    def test_no_group_user_can_open_profile(self):
        self.client.force_login(self.nogroup)
        self.assertEqual(self.client.get("/profil/").status_code, 200)

    def test_viewer_role_can_read_planner(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get("/planner/products/").status_code, 200)
