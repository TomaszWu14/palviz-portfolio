"""Carrier selection → driver data form → driver pickup confirmation (SMS link)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, Client
from django.urls import reverse

from ui.models import (
    Product, Shipment, ShipmentLine, PalletizationInstruction, QuoteRecipient,
    ShipmentQuoteOffer, DriverAssignment,
)
from ui.roles import ALL_GROUPS


def _user_all_roles():
    u = get_user_model().objects.create_user(username="d", password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class DriverFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.sh = Shipment.objects.create(name="Dostawa 1", destination_city="VALDEMO")
        p = Product.objects.create(code="RG-50", name="RG")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=10, demand_pcs=100, is_active=True)
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=20, unit="kar")
        cls.r1 = QuoteRecipient.objects.create(name="Speed A", email="a@speed.pl")
        # BIZ-009: wybrać można tylko ofertę złożoną (submitted_at) z kwotą.
        from django.utils import timezone
        cls.offer = ShipmentQuoteOffer.objects.create(
            shipment=cls.sh, recipient=cls.r1, carrier_name="Speed A", amount=1500,
            submitted_at=timezone.now())

    def setUp(self):
        self.client.force_login(self.user)

    def test_select_offer_creates_driver_assignment(self):
        resp = self.client.post(reverse("ui:planner_shipment_select_offer", args=[self.sh.pk, self.offer.pk]))
        self.assertEqual(resp.status_code, 302)
        self.offer.refresh_from_db()
        self.assertTrue(self.offer.selected)
        da = DriverAssignment.objects.get(shipment=self.sh)
        self.assertEqual(da.offer_id, self.offer.pk)

    def test_forwarder_fills_driver_data_then_driver_confirms(self):
        self.client.post(reverse("ui:planner_shipment_select_offer", args=[self.sh.pk, self.offer.pk]))
        da = DriverAssignment.objects.get(shipment=self.sh)
        anon = Client()
        # Forwarder fills the driver form (public token page).
        fresp = anon.post(reverse("ui:driver_form", args=[da.form_token]),
                          {"driver_plate": "SZA 12345", "driver_phone": "+48600000000",
                           "driver_name": "Jan K"})
        self.assertEqual(fresp.status_code, 200)
        da.refresh_from_db()
        self.assertEqual(da.driver_plate, "SZA 12345")
        self.assertIsNotNone(da.filled_at)
        # Driver confirms pickup via the SMS link page.
        cresp = anon.post(reverse("ui:driver_confirm", args=[da.confirm_token]), {"answer": "confirmed"})
        self.assertEqual(cresp.status_code, 200)
        da.refresh_from_db()
        self.assertEqual(da.pickup_status, "confirmed")
        self.assertIsNotNone(da.confirmed_at)

    def test_cancel_sets_status(self):
        resp = self.client.post(reverse("ui:planner_shipment_cancel", args=[self.sh.pk]))
        self.assertEqual(resp.status_code, 302)
        self.sh.refresh_from_db()
        self.assertEqual(self.sh.status, "cancelled")

    def test_requote_unlocks_and_clears(self):
        from django.utils import timezone
        self.offer.submitted_at = timezone.now()
        self.offer.selected = True
        self.offer.save()
        self.client.post(reverse("ui:planner_shipment_select_offer", args=[self.sh.pk, self.offer.pk]))
        self.assertTrue(DriverAssignment.objects.filter(shipment=self.sh).exists())
        resp = self.client.post(reverse("ui:planner_shipment_requote", args=[self.sh.pk]))
        self.assertRedirects(resp, reverse("ui:planner_shipment_quote", args=[self.sh.pk]))
        self.offer.refresh_from_db()
        self.assertFalse(self.offer.selected)
        self.assertIsNone(self.offer.submitted_at)
        self.assertFalse(DriverAssignment.objects.filter(shipment=self.sh).exists())

    def test_send_sms_without_gateway_warns_and_counts(self):
        self.client.post(reverse("ui:planner_shipment_select_offer", args=[self.sh.pk, self.offer.pk]))
        da = DriverAssignment.objects.get(shipment=self.sh)
        da.driver_phone = "+48600000000"
        da.save()
        resp = self.client.post(reverse("ui:planner_shipment_send_driver_sms", args=[self.sh.pk]))
        self.assertEqual(resp.status_code, 302)
        da.refresh_from_db()
        self.assertEqual(da.sms_count, 1)        # counted even though unconfigured (manual link shown)

    def test_sms_body_includes_pickup_and_delivery_dates(self):
        from unittest.mock import patch
        import datetime as dt
        self.offer.truck_date = dt.date(2026, 7, 1)
        self.offer.delivery_date = dt.date(2026, 7, 3)
        self.offer.save()
        self.client.post(reverse("ui:planner_shipment_select_offer", args=[self.sh.pk, self.offer.pk]))
        da = DriverAssignment.objects.get(shipment=self.sh)
        da.driver_phone = "+48600000000"
        da.save()
        with patch("transport.views.driver._send_sms", return_value=True) as m:
            self.client.post(reverse("ui:planner_shipment_send_driver_sms", args=[self.sh.pk]))
        body = m.call_args[0][1]
        self.assertIn("podstawienie 01.07.2026", body)
        self.assertIn("dostawa ~03.07.2026", body)
        self.assertIn("VALDEMO", body)
        self.assertIn("/driver-confirm/", body)              # the confirmation link

    def test_driver_form_language_and_eta(self):
        self.client.post(reverse("ui:planner_shipment_select_offer", args=[self.sh.pk, self.offer.pk]))
        da = DriverAssignment.objects.get(shipment=self.sh)
        Client().post(reverse("ui:driver_form", args=[da.form_token]),
                      {"driver_plate": "X1", "driver_phone": "+48600", "driver_name": "J",
                       "driver_language": "en", "truck_eta": "2026-07-01T08:30"})
        from django.utils import timezone
        da.refresh_from_db()
        self.assertEqual(da.driver_language, "en")
        self.assertIsNotNone(da.truck_eta)
        self.assertEqual(timezone.localtime(da.truck_eta).strftime("%Y-%m-%d %H:%M"),
                         "2026-07-01 08:30")

    def test_driver_confirm_shows_info_packet(self):
        from ui.models import SiteInfo
        SiteInfo.objects.create(leaders="Jan 600100200", instructions="Wjazd bramą nr 2.")
        Shipment.objects.filter(pk=self.sh.pk).update(ramp="12")
        self.client.post(reverse("ui:planner_shipment_select_offer", args=[self.sh.pk, self.offer.pk]))
        da = DriverAssignment.objects.get(shipment=self.sh)
        resp = Client().get(reverse("ui:driver_confirm", args=[da.confirm_token]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "RAMPA")
        self.assertContains(resp, "12")
        self.assertContains(resp, "Jan 600100200")
        self.assertContains(resp, "Wjazd bramą nr 2.")
        self.assertContains(resp, "VALDEMO")            # delivery address

    def test_filling_driver_form_pushes_sms(self):
        self.client.post(reverse("ui:planner_shipment_select_offer", args=[self.sh.pk, self.offer.pk]))
        da = DriverAssignment.objects.get(shipment=self.sh)
        Client().post(reverse("ui:driver_form", args=[da.form_token]),
                      {"driver_plate": "X1", "driver_phone": "+48600000000", "driver_name": "J"})
        da.refresh_from_db()
        self.assertEqual(da.sms_count, 1)                 # info packet pushed on fill
        self.assertIsNotNone(da.sms_last_at)
