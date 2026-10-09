"""Linki kierowcy wygasają (audyt SEC-013): zamknięta przesyłka albo > DRIVER_LINK_TTL_DAYS."""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from testkit.factories import ShipmentFactory
from transport.models import DriverAssignment


class DriverLinkExpiryTests(TestCase):
    def _da(self, **sh):
        return DriverAssignment.objects.create(shipment=ShipmentFactory(**sh), driver_name="Jan", driver_phone="600100200")

    def test_active_links_work(self):
        da = self._da()
        self.assertEqual(self.client.get(f"/driver-form/{da.form_token}/").status_code, 200)
        self.assertEqual(self.client.get(f"/driver-confirm/{da.confirm_token}/").status_code, 200)

    def test_closed_shipment_links_gone_and_data_hidden(self):
        for status in ("sent", "cancelled"):
            da = self._da(status=status)
            for url in (f"/driver-form/{da.form_token}/", f"/driver-confirm/{da.confirm_token}/"):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 410, url)
                self.assertNotContains(r, "600100200", status_code=410)
            r = self.client.post(f"/driver-confirm/{da.confirm_token}/", {"answer": "declined"})
            self.assertEqual(r.status_code, 410)
            da.refresh_from_db()
            self.assertNotEqual(da.pickup_status, "declined")

    @override_settings(DRIVER_LINK_TTL_DAYS=30)
    def test_old_assignment_expires(self):
        da = self._da()
        DriverAssignment.objects.filter(pk=da.pk).update(created_at=timezone.now() - timedelta(days=31))
        self.assertEqual(self.client.get(f"/driver-form/{da.form_token}/").status_code, 410)
