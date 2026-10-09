"""Maszyna stanów wysyłki (audyt BIZ-003, decyzja Q-41 = „Automatycznie”).

„Zatwierdzone” ustawia się samo przy wyborze oferty spedycji, „Wysłane” — gdy kierowca
potwierdzi odbiór linkiem publicznym. Formularz przesyłki nie pozwala wybrać statusu;
Transport może tylko anulować albo wrócić do roboczej (ponowna wycena). Przejścia w
jednym miejscu: ``Shipment.mark_*`` / ``Shipment.STATUS_TRANSITIONS``.
"""
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from testkit.factories import ShipmentFactory
from testkit.personas import client_for
from transport.models import DriverAssignment, QuoteRecipient, Shipment, ShipmentQuoteOffer
from ui.forms_transport import ShipmentForm

STATUSES = [s for s, _ in Shipment.STATUS]


def _offer(sh, name="Speed A"):
    """Oferta złożona przez spedycję (z kwotą i submitted_at) — jak w realnym flow."""
    r = QuoteRecipient.objects.create(name=name, email=f"{name.split()[-1].lower()}@speed.pl")
    return ShipmentQuoteOffer.objects.create(shipment=sh, recipient=r, carrier_name=name,
                                             amount=1500, submitted_at=timezone.now())


class ShipmentStatusTransitionsModelTests(TestCase):
    """Tabela przejść: tylko dozwolone zmieniają status, reszta to no-op (False)."""

    ALLOWED = {
        ("draft", "confirmed"), ("draft", "sent"), ("confirmed", "sent"),
        ("confirmed", "draft"), ("cancelled", "draft"),
        ("draft", "cancelled"), ("confirmed", "cancelled"), ("sent", "cancelled"),
    }

    def test_only_allowed_transitions_change_status(self):
        for src in STATUSES:
            for target in STATUSES:
                with self.subTest(src=src, target=target):
                    sh = ShipmentFactory(status=src)
                    changed = getattr(sh, f"mark_{target}")()
                    sh.refresh_from_db()
                    if (src, target) in self.ALLOWED:
                        self.assertTrue(changed)
                        self.assertEqual(sh.status, target)
                    else:
                        self.assertFalse(changed)
                        self.assertEqual(sh.status, src)

    def test_cancelled_never_becomes_confirmed_or_sent(self):
        sh = ShipmentFactory(status="cancelled")
        self.assertFalse(sh.mark_confirmed())
        self.assertFalse(sh.mark_sent())
        sh.refresh_from_db()
        self.assertEqual(sh.status, "cancelled")

    def test_sent_is_not_downgraded_to_confirmed(self):
        sh = ShipmentFactory(status="sent")
        self.assertFalse(sh.mark_confirmed())
        sh.refresh_from_db()
        self.assertEqual(sh.status, "sent")


class ShipmentStatusFlowViewTests(TestCase):
    def setUp(self):
        self.client = client_for("Transport")
        self.sh = ShipmentFactory()
        self.offer = _offer(self.sh)

    def _select(self, sh=None, offer=None):
        sh, offer = sh or self.sh, offer or self.offer
        return self.client.post(reverse("ui:planner_shipment_select_offer", args=[sh.pk, offer.pk]))

    def _status(self, sh=None):
        (sh or self.sh).refresh_from_db()
        return (sh or self.sh).status

    def test_selecting_offer_confirms_shipment(self):
        self.assertEqual(self._select().status_code, 302)
        self.assertEqual(self._status(), "confirmed")

    def test_switching_offer_keeps_confirmed(self):
        self._select()
        other = _offer(self.sh, "Speed B")
        self._select(offer=other)
        self.assertEqual(self._status(), "confirmed")

    def test_selecting_offer_on_cancelled_shipment_does_not_confirm(self):
        sh = ShipmentFactory(status="cancelled")
        self._select(sh, _offer(sh, "Speed C"))
        self.assertEqual(self._status(sh), "cancelled")

    def test_driver_pickup_confirmation_marks_sent(self):
        self._select()
        da = DriverAssignment.objects.get(shipment=self.sh)
        r = Client().post(reverse("ui:driver_confirm", args=[da.confirm_token]), {"answer": "confirmed"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._status(), "sent")

    def test_driver_decline_does_not_mark_sent(self):
        self._select()
        da = DriverAssignment.objects.get(shipment=self.sh)
        Client().post(reverse("ui:driver_confirm", args=[da.confirm_token]), {"answer": "declined"})
        self.assertEqual(self._status(), "confirmed")

    def test_driver_confirmation_on_legacy_draft_marks_sent(self):
        # Przesyłki sprzed zmiany: oferta wybrana, status został „Robocze”.
        da = DriverAssignment.objects.create(shipment=self.sh, offer=self.offer)
        Client().post(reverse("ui:driver_confirm", args=[da.confirm_token]), {"answer": "confirmed"})
        self.assertEqual(self._status(), "sent")

    def test_cancel_view_cancels_confirmed(self):
        self._select()
        self.client.post(reverse("ui:planner_shipment_cancel", args=[self.sh.pk]))
        self.assertEqual(self._status(), "cancelled")

    def test_requote_returns_confirmed_and_cancelled_to_draft(self):
        self._select()
        self.client.post(reverse("ui:planner_shipment_requote", args=[self.sh.pk]))
        self.assertEqual(self._status(), "draft")
        self.client.post(reverse("ui:planner_shipment_cancel", args=[self.sh.pk]))
        self.client.post(reverse("ui:planner_shipment_requote", args=[self.sh.pk]))
        self.assertEqual(self._status(), "draft")

    def test_requote_does_not_revert_sent(self):
        sh = ShipmentFactory(status="sent")
        self.client.post(reverse("ui:planner_shipment_requote", args=[sh.pk]))
        self.assertEqual(self._status(sh), "sent")

    def _apply_wh_count(self, sh=None, count=5):
        return self.client.post(reverse("ui:planner_shipment_apply_wh_count",
                                        args=[(sh or self.sh).pk]), {"count": str(count)})

    def test_warehouse_count_reopening_quote_returns_confirmed_to_draft(self):
        # Nowa liczba palet od magazynu czyści wybór oferty → wysyłka nie jest już
        # „Zatwierdzona” (jak przy ponownej wycenie).
        self._select()
        self._apply_wh_count()
        self.assertEqual(self._status(), "draft")
        self.assertFalse(self.sh.quote_offers.filter(selected=True).exists())

    def test_warehouse_count_never_restores_cancelled_or_reverts_sent(self):
        for status in ("cancelled", "sent"):
            with self.subTest(status=status):
                sh = ShipmentFactory(status=status)
                self._apply_wh_count(sh)
                self.assertEqual(self._status(sh), status)

    def test_cancelled_detail_offers_restore(self):
        sh = ShipmentFactory(status="cancelled")
        r = self.client.get(reverse("ui:planner_shipment_detail", args=[sh.pk]))
        self.assertContains(r, reverse("ui:planner_shipment_requote", args=[sh.pk]))


class ShipmentFormHasNoStatusTests(TestCase):
    def setUp(self):
        self.client = client_for("Transport")

    def test_form_has_no_status_field(self):
        self.assertNotIn("status", ShipmentForm().fields)

    def test_edit_cannot_set_status_and_keeps_existing(self):
        sh = ShipmentFactory(status="confirmed")
        r = self.client.post(reverse("ui:planner_shipment_edit", args=[sh.pk]),
                             {"name": "Zmieniona", "status": "sent", "stowage_efficiency_pct": 80})
        self.assertEqual(r.status_code, 302)
        sh.refresh_from_db()
        self.assertEqual(sh.name, "Zmieniona")
        self.assertEqual(sh.status, "confirmed")          # ani „sent” z POST, ani reset do draft

    def test_new_shipment_is_draft_even_if_status_posted(self):
        r = self.client.post(reverse("ui:planner_shipment_new"),
                             {"name": "NOWA-ST", "status": "sent", "stowage_efficiency_pct": 80})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Shipment.objects.get(name="NOWA-ST").status, "draft")

    def test_form_page_shows_readonly_status(self):
        sh = ShipmentFactory(status="confirmed")
        r = self.client.get(reverse("ui:planner_shipment_edit", args=[sh.pk]))
        self.assertContains(r, 'id="shipment-status-ro"')
        self.assertContains(r, "Zatwierdzone")
        self.assertNotContains(r, 'name="status"')
