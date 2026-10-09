"""RODO: gdpr_find (art. 15) i retencja danych osobowych (audyt GDPR-001/002)."""
import json
from datetime import timedelta
from io import StringIO

from axes.models import AccessLog
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from testkit.factories import CustomerFactory, ShipmentFactory, UserFactory
from transport.models import DriverAssignment
from ui.tasks import gdpr_retention


class GdprFindTests(TestCase):
    def test_finds_person_across_tables(self):
        UserFactory(username="jkowalski", email="jan.kowalski@przewoz.pl")
        CustomerFactory(contact_email="jan.kowalski@przewoz.pl")
        DriverAssignment.objects.create(shipment=ShipmentFactory(), driver_name="Jan Kowalski", driver_phone="600100200")
        out = StringIO()
        call_command("gdpr_find", "kowalski", "--json", stdout=out)
        found = json.loads(out.getvalue())
        self.assertEqual(set(found), {"auth.User", "ui.Customer", "transport.DriverAssignment"})
        call_command("gdpr_find", "600100200", "--json", stdout=(out := StringIO()))
        self.assertEqual(list(json.loads(out.getvalue())), ["transport.DriverAssignment"])

    def test_too_short_term_rejected(self):
        with self.assertRaises(CommandError):
            call_command("gdpr_find", "ab")


class GdprRetentionTests(TestCase):
    def setUp(self):
        self.old = DriverAssignment.objects.create(shipment=ShipmentFactory(), driver_name="Stary", driver_phone="1", driver_plate="SK1")
        self.new = DriverAssignment.objects.create(shipment=ShipmentFactory(), driver_name="Nowy", driver_phone="2", driver_plate="SK2")
        DriverAssignment.objects.filter(pk=self.old.pk).update(created_at=timezone.now() - timedelta(days=100))
        log = AccessLog.objects.create(username="x", ip_address="10.0.0.1", user_agent="ua")
        AccessLog.objects.filter(pk=log.pk).update(attempt_time=timezone.now() - timedelta(days=100))
        AccessLog.objects.create(username="y", ip_address="10.0.0.2", user_agent="ua")

    def test_off_by_default(self):
        self.assertEqual(gdpr_retention(), {"drivers": 0, "access_logs": 0})
        self.old.refresh_from_db()
        self.assertEqual(self.old.driver_name, "Stary")

    @override_settings(GDPR_DRIVER_RETAIN_DAYS=90, GDPR_ACCESSLOG_RETAIN_DAYS=90)
    def test_anonymizes_only_expired(self):
        self.assertEqual(gdpr_retention(), {"drivers": 1, "access_logs": 1})
        self.old.refresh_from_db(); self.new.refresh_from_db()
        self.assertEqual((self.old.driver_name, self.old.driver_phone, self.old.driver_plate), ("", "", ""))
        self.assertEqual(self.new.driver_name, "Nowy")
        self.assertEqual(list(AccessLog.objects.values_list("username", flat=True)), ["y"])
        self.assertEqual(gdpr_retention(), {"drivers": 0, "access_logs": 0})   # idempotentne
