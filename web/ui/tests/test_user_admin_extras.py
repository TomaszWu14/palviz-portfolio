# Zarządzanie użytkownikami: walidacja haseł, unikalność e-maila, pola profilu,
# samoobsługowy „Mój profil" i przepływ resetu hasła.
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from ui.models import UserProfile


class _AdminBase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "boss@example.com", "Zx9!longpass")
        self.client.force_login(self.admin)


class AdminUserFormTests(_AdminBase):
    def _post_user(self, **extra):
        data = {"username": "nowy", "email": "nowy@example.com",
                "password1": "Dl9!ugieHaslo", "password2": "Dl9!ugieHaslo",
                "is_active": "1", "email_notifications": "1"}
        data.update(extra)
        return self.client.post(reverse("ui:admin_user_new"), data)

    def test_weak_password_rejected_by_validators(self):
        r = self._post_user(password1="1234567", password2="1234567")
        self.assertEqual(r.status_code, 200)          # formularz wraca z błędami
        self.assertFalse(User.objects.filter(username="nowy").exists())

    def test_duplicate_email_rejected(self):
        r = self._post_user(email="boss@example.com")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="nowy").exists())

    def test_profile_fields_saved(self):
        r = self._post_user(phone="+48 600 100 200", department="Magazyn")
        self.assertEqual(r.status_code, 302)
        prof = User.objects.get(username="nowy").profile
        self.assertEqual(prof.phone, "+48 600 100 200")
        self.assertEqual(prof.department, "Magazyn")
        self.assertTrue(prof.email_notifications)

    def test_email_opt_out_saved(self):
        self._post_user(email_notifications="")
        self.assertFalse(User.objects.get(username="nowy").profile.email_notifications)


class MyProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("jan", "jan@example.com", "Zx9!longpass")
        self.client.force_login(self.user)

    def test_renders_and_saves_phone_and_opt_out(self):
        self.assertEqual(self.client.get(reverse("ui:my_profile")).status_code, 200)
        r = self.client.post(reverse("ui:my_profile"),
                             {"phone": "+48 601 000 000"})   # brak checkboxa = opt-out
        self.assertEqual(r.status_code, 302)
        prof = UserProfile.objects.get(user=self.user)
        self.assertEqual(prof.phone, "+48 601 000 000")
        self.assertFalse(prof.email_notifications)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   EMAIL_HOST="smtp.test")
class PasswordResetFlowTests(TestCase):
    def test_pages_render(self):
        for name in ("password_reset", "password_reset_done", "password_reset_complete"):
            self.assertEqual(self.client.get(reverse(f"ui:{name}")).status_code, 200)

    @override_settings(EMAIL_HOST="")
    def test_reset_form_404_without_smtp(self):
        self.assertEqual(self.client.get(reverse("ui:password_reset")).status_code, 404)

    def test_email_sent_with_confirm_link(self):
        from django.core import mail
        User.objects.create_user("jan", "jan@example.com", "Zx9!longpass")
        r = self.client.post(reverse("ui:password_reset"), {"email": "jan@example.com"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/password-reset/", mail.outbox[0].body)

    def test_invalid_confirm_link_renders_error(self):
        r = self.client.get(reverse("ui:password_reset_confirm",
                                    kwargs={"uidb64": "xx", "token": "yy-zz"}))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "nieprawidłowy")
