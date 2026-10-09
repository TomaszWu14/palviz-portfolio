"""Automatic driver pickup-confirmation reminders (Celery beat task)."""
from unittest import mock
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from ui.models import Shipment, DriverAssignment
from ui.tasks import send_driver_reminders


@override_settings(DRIVER_REMINDER_INTERVAL_MIN=30, DRIVER_REMINDER_MAX=3,
                   SITE_BASE_URL="https://groove.example.com")   # link wymaga hosta (fix wzgl. URL)
class DriverReminderTests(TestCase):
    def _da(self, **kw):
        sh = Shipment.objects.create(name="D")
        defaults = dict(shipment=sh, driver_phone="+48600000000", filled_at=timezone.now(),
                        pickup_status="pending", sms_count=0)
        defaults.update(kw)
        return DriverAssignment.objects.create(**defaults)

    @mock.patch("transport.sms.send_sms", return_value=True)
    def test_reminds_pending_driver(self, sms):
        da = self._da()
        out = send_driver_reminders()
        self.assertEqual(out["reminders_sent"], 1)
        da.refresh_from_db()
        self.assertEqual(da.sms_count, 1)
        # Within the interval → no second reminder.
        self.assertEqual(send_driver_reminders()["reminders_sent"], 0)

    @mock.patch("transport.sms.send_sms", return_value=True)
    def test_respects_interval_and_cap(self, sms):
        old = timezone.now() - timedelta(minutes=40)
        da = self._da(sms_count=1, sms_last_at=old)
        send_driver_reminders()
        da.refresh_from_db()
        self.assertEqual(da.sms_count, 2)                  # interval elapsed → reminded
        # At the cap → stop.
        da.sms_count = 3; da.sms_last_at = timezone.now() - timedelta(hours=2); da.save()
        send_driver_reminders()
        da.refresh_from_db()
        self.assertEqual(da.sms_count, 3)

    @mock.patch("transport.sms.send_sms", return_value=True)
    def test_skips_confirmed(self, sms):
        self._da(pickup_status="confirmed")
        self.assertEqual(send_driver_reminders()["reminders_sent"], 0)
