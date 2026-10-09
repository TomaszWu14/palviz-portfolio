"""UserProfile: auto-creation signal + e-mail notification opt-out."""
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings

from ui.models import UserProfile
from ui.notifications import notify


class UserProfileSignalTests(TestCase):
    def test_profile_auto_created_on_user_create(self):
        u = get_user_model().objects.create_user(username="newbie", password="x")
        self.assertTrue(UserProfile.objects.filter(user=u).exists())
        self.assertTrue(u.profile.email_notifications)  # default on

    def test_profile_not_duplicated_on_resave(self):
        u = get_user_model().objects.create_user(username="again", password="x")
        u.first_name = "X"
        u.save()
        self.assertEqual(UserProfile.objects.filter(user=u).count(), 1)


@override_settings(EMAIL_HOST="smtp.example.com",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailOptOutTests(TestCase):
    def setUp(self):
        self.u = get_user_model().objects.create_user(
            username="rcpt", password="x", email="rcpt@example.com")

    def test_email_sent_when_opted_in(self):
        notify([self.u], "Tytuł", "treść", email=True)
        self.assertEqual(len(mail.outbox), 1)

    def test_email_suppressed_when_opted_out(self):
        self.u.profile.email_notifications = False
        self.u.profile.save()
        notify([self.u], "Tytuł", "treść", email=True)
        self.assertEqual(len(mail.outbox), 0)     # in-app notification still created
        self.assertEqual(self.u.notifications.count(), 1)
