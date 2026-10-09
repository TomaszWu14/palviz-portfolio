"""SMS channel for notifications (user profile phone)."""
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from ui.notifications import notify, _user_phone


class SmsNotifyTests(TestCase):
    def test_sms_sent_to_users_with_phone(self):
        u = get_user_model().objects.create_user("op", password="x")
        # The profile is auto-created by the post_save signal; set its phone.
        u.profile.phone = "+48500100200"
        u.profile.save()
        self.assertEqual(_user_phone(u), "+48500100200")
        with patch("transport.sms.send_sms") as mock_sms:
            notify([u], "Pilne", "treść", level="error", sms=True)
            mock_sms.assert_called_once()
            self.assertEqual(mock_sms.call_args[0][0], "+48500100200")

    def test_no_sms_without_phone(self):
        u = get_user_model().objects.create_user("op2", password="x")
        with patch("transport.sms.send_sms") as mock_sms:
            notify([u], "Pilne", sms=True)
            mock_sms.assert_not_called()
