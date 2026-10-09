"""Anulowanie wysyłki domyka proces (audyt BIZ-009, decyzja „Domknij wszystko”).

- wybór oferty spedycji: tylko na nieanulowanej wysyłce, tylko oferta złożona
  (submitted_at) i z kwotą — inaczej polski komunikat, nic się nie zmienia;
- publiczny link kierowcy (formularz spedycji i potwierdzenie odbioru) dla anulowanej
  wysyłki: strona „Link jest już nieaktywny” (410), bez zmiany stanu;
- anulowanie nie kasuje ani nie zmienia HU (ślad audytowy) — znikają tylko z kolejki
  (patrz huctl/tests/test_hu_queue_cancelled_shipment.py).
"""
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from testkit.factories import HandlingUnitFactory, ShipmentFactory
from testkit.personas import client_for
from transport.models import DriverAssignment, QuoteRecipient, ShipmentQuoteOffer


def _offer(sh, **kw):
    r = QuoteRecipient.objects.create(name="Speed A", email="a@speed.pl")
    data = {"carrier_name": "Speed A", "amount": 1500, "submitted_at": timezone.now()}
    data.update(kw)
    return ShipmentQuoteOffer.objects.create(shipment=sh, recipient=r, **data)


class SelectOfferGuardTests(TestCase):
    def setUp(self):
        self.client = client_for("Transport")

    def _select(self, sh, offer):
        return self.client.post(reverse("ui:planner_shipment_select_offer", args=[sh.pk, offer.pk]))

    def _assert_rejected(self, resp, sh, offer, fragment):
        self.assertRedirects(resp, reverse("ui:planner_shipment_detail", args=[sh.pk]),
                             fetch_redirect_response=False)
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any(fragment in m for m in msgs), msgs)
        offer.refresh_from_db()
        self.assertFalse(offer.selected)
        self.assertFalse(DriverAssignment.objects.filter(shipment=sh).exists())

    def test_cancelled_shipment_rejects_offer(self):
        sh = ShipmentFactory(status="cancelled")
        offer = _offer(sh)
        self._assert_rejected(self._select(sh, offer), sh, offer, "anulowana")
        sh.refresh_from_db()
        self.assertEqual(sh.status, "cancelled")

    def test_offer_without_amount_rejected(self):
        sh = ShipmentFactory()
        offer = _offer(sh, amount=None)
        self._assert_rejected(self._select(sh, offer), sh, offer, "nie złożyła jeszcze wyceny")

    def test_offer_not_submitted_rejected(self):
        sh = ShipmentFactory()
        offer = _offer(sh, submitted_at=None)
        self._assert_rejected(self._select(sh, offer), sh, offer, "nie złożyła jeszcze wyceny")

    def test_submitted_priced_offer_is_selected(self):
        sh = ShipmentFactory()
        offer = _offer(sh)
        self.assertEqual(self._select(sh, offer).status_code, 302)
        offer.refresh_from_db()
        self.assertTrue(offer.selected)
        self.assertTrue(DriverAssignment.objects.filter(shipment=sh, offer=offer).exists())


class CancelledDriverLinkTests(TestCase):
    def setUp(self):
        self.sh = ShipmentFactory(status="cancelled")
        self.da = DriverAssignment.objects.create(
            shipment=self.sh, driver_name="Jan", driver_plate="SZA 1", driver_phone="600100200")
        self.anon = Client()

    def test_confirm_link_gone_without_state_change(self):
        url = reverse("ui:driver_confirm", args=[self.da.confirm_token])
        r = self.anon.get(url)
        self.assertContains(r, "Link jest już nieaktywny", status_code=410)
        for answer in ("confirmed", "declined", "confirmed"):      # próba „przełączania”
            self.assertEqual(self.anon.post(url, {"answer": answer}).status_code, 410)
        self.da.refresh_from_db()
        self.assertEqual(self.da.pickup_status, "pending")
        self.assertIsNone(self.da.confirmed_at)
        self.sh.refresh_from_db()
        self.assertEqual(self.sh.status, "cancelled")

    def test_forwarder_form_link_gone_without_state_change(self):
        url = reverse("ui:driver_form", args=[self.da.form_token])
        self.assertContains(self.anon.get(url), "Link jest już nieaktywny", status_code=410)
        r = self.anon.post(url, {"driver_plate": "XX 999", "driver_phone": "+48600000000"})
        self.assertEqual(r.status_code, 410)
        self.da.refresh_from_db()
        self.assertEqual(self.da.driver_plate, "SZA 1")
        self.assertIsNone(self.da.filled_at)


class CancelKeepsHandlingUnitsTests(TestCase):
    def test_cancel_does_not_delete_or_change_hus(self):
        sh = ShipmentFactory()
        planned = HandlingUnitFactory(shipment=sh, status="planned")
        in_ctrl = HandlingUnitFactory(shipment=sh, status="in_control")
        client_for("Transport").post(reverse("ui:planner_shipment_cancel", args=[sh.pk]))
        sh.refresh_from_db()
        self.assertEqual(sh.status, "cancelled")
        planned.refresh_from_db()
        in_ctrl.refresh_from_db()
        self.assertEqual((planned.status, in_ctrl.status), ("planned", "in_control"))


class SelectOfferButtonTests(TestCase):
    """Przycisk „Wybierz” tylko tam, gdzie bramka widoku przepuści wybór."""

    def setUp(self):
        self.client = client_for("Transport")

    def _has_button(self, sh, offer):
        r = self.client.get(reverse("ui:planner_shipment_detail", args=[sh.pk]))
        return reverse("ui:planner_shipment_select_offer", args=[sh.pk, offer.pk]) in r.content.decode()

    def test_button_only_for_priced_offer_on_active_shipment(self):
        sh = ShipmentFactory()
        self.assertTrue(self._has_button(sh, _offer(sh)))
        sh2 = ShipmentFactory()
        self.assertFalse(self._has_button(sh2, _offer(sh2, amount=None)))
        sh3 = ShipmentFactory(status="cancelled")
        self.assertFalse(self._has_button(sh3, _offer(sh3)))
