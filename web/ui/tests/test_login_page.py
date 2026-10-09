from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

class LoginPageTests(TestCase):
    def test_root_redirects_to_branded_login(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)
        self.assertNotIn("/admin/login/", r.url)

    @override_settings(APP_NAME="MONTY")
    def test_login_page_renders_branded(self):
        r = self.client.get("/login/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "MONTY")            # brand is env-driven (APP_NAME)
        self.assertContains(r, "Nazwa użytkownika")

    def test_login_page_offers_qr_badge_scan(self):
        r = self.client.get("/login/")
        self.assertContains(r, "badge QR")          # QR-badge login affordance present
        self.assertContains(r, "palvizBadgeScan")

    def test_successful_login_then_root_ok(self):
        get_user_model().objects.create_user(username="joe", password="secret123")
        r = self.client.post("/login/", {"username": "joe", "password": "secret123"}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["user"].is_authenticated)

    def test_logout_requires_post_and_redirects(self):
        get_user_model().objects.create_user(username="joe", password="secret123")
        # authenticate via the login view so django-axes gets the request it needs
        self.client.post("/login/", {"username": "joe", "password": "secret123"})
        self.assertEqual(self.client.get("/logout/").status_code, 405)   # GET not allowed in Django 5.2
        r = self.client.post("/logout/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)
