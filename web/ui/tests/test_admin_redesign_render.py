"""Render-smoke redesignu panelu admina: każdy przepisany ekran musi się wyrenderować
(realny request → widok → szablon) jako superuser. Łapie błędy tagów/kontekstu, których
`manage.py check` nie widzi. (Odzyskany — zginął w wyścigu auto-merge PR #319.)"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class AdminRedesignRenderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("smoke_admin", "a@a.pl", "x")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_redesigned_admin_pages_render(self):
        for name in ["admin_module_access", "admin_users", "admin_control_zones",
                     "admin_role_matrix", "admin_zaria_panel"]:
            with self.subTest(page=name):
                resp = self.client.get(reverse(f"ui:{name}"))
                self.assertEqual(resp.status_code, 200, f"{name} nie wyrenderował się (200)")

    def test_module_access_optimistic_lock(self):
        """Zapis macierzy z nieświeżym tokenem (drugi admin edytował w międzyczasie)
        jest wstrzymany, żeby nie nadpisać cudzych zmian."""
        from ui.models import UserModuleAccess
        from ui.platform_modules import MODULES
        mkey = MODULES[0].key
        url = reverse("ui:admin_module_access")
        stale_token = self.client.get(url).context["matrix_token"]
        # Pierwszy zapis z aktualnym tokenem — ustawia jedną komórkę allow → sukces.
        field = f"m_{self.admin.pk}_{mkey}"
        self.client.post(url, {"matrix_token": stale_token, field: "allow"})
        self.assertEqual(UserModuleAccess.objects.filter(module_key=mkey, allowed=True).count(), 1)
        # Drugi zapis z TYM SAMYM (już nieświeżym) tokenem, pusta siatka → wstrzymany:
        # override z kroku 1 NIE może zniknąć.
        self.client.post(url, {"matrix_token": stale_token})
        self.assertEqual(UserModuleAccess.objects.filter(module_key=mkey, allowed=True).count(), 1)
        # Świeży token → zapis przechodzi (czyści siatkę).
        fresh = self.client.get(url).context["matrix_token"]
        self.client.post(url, {"matrix_token": fresh})
        self.assertEqual(UserModuleAccess.objects.count(), 0)
