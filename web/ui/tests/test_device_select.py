"""Wybór typu urządzenia po zalogowaniu: Zebra / telefon / tablet / komputer.
Zasila UserProfile.last_device (routing zadań ze zdjęciem — Zebra bez aparatu)."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(DEVICE_CONFIRM_ENABLED=True)
class DeviceSelectTests(TestCase):
    def setUp(self):
        self.u = get_user_model().objects.create_user("c1", password="x")

    def test_login_redirects_to_device_select(self):
        self.client.post(reverse("ui:login"), {"username": "c1", "password": "x"})
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/urzadzenie/"))

    def test_choice_saved_to_session_cookie_and_profile(self):
        self.client.post(reverse("ui:login"), {"username": "c1", "password": "x"})
        r = self.client.post(reverse("ui:device_select"), {"device_type": "tablet",
                                                           "next": "/"})
        self.assertRedirects(r, "/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["device_type"], "tablet")
        self.assertEqual(self.client.cookies["pv_devtype"].value, "tablet")
        self.u.refresh_from_db()
        self.assertEqual(self.u.profile.last_device, "tablet")
        self.assertTrue(self.u.profile.has_camera)
        # po wyborze aplikacja przestaje przekierowywać
        self.assertEqual(self.client.get(reverse("ui:my_profile")).status_code, 200)

    def test_zebra_has_no_camera(self):
        self.client.post(reverse("ui:login"), {"username": "c1", "password": "x"})
        self.client.post(reverse("ui:device_select"), {"device_type": "zebra", "next": "/"})
        self.u.refresh_from_db()
        self.assertFalse(self.u.profile.has_camera)

    def test_force_login_not_redirected(self):
        # Testowe force_login (i sesje sprzed wdrożenia) nie mają flagi pending —
        # bez przekierowania, żeby nie łamać istniejących smoke testów i sesji.
        self.client.force_login(self.u)
        self.assertEqual(self.client.get(reverse("ui:my_profile")).status_code, 200)

    def test_invalid_type_not_accepted(self):
        self.client.post(reverse("ui:login"), {"username": "c1", "password": "x"})
        self.client.post(reverse("ui:device_select"), {"device_type": "toster", "next": "/"})
        self.assertNotIn("device_type", self.client.session)
