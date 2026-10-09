"""Quote request screen (per-recipient send, no line items) + forwarder response form."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import (
    Product, Shipment, ShipmentLine, PalletizationInstruction, QuoteRecipient,
    ShipmentQuoteOffer,
)
from ui.roles import ALL_GROUPS


def _user_all_roles():
    u = get_user_model().objects.create_user(username="q", password="x",
                                              first_name="Jan", last_name="Kontakt",
                                              email="jan@firma.pl")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class QuoteFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.sh = Shipment.objects.create(name="Dostawa 1", stowage_efficiency_pct=80,
                                         recipient_name="PHARMO", destination_city="VALDEMO",
                                         author="SOMEONE_ELSE")
        p = Product.objects.create(code="RG-50", name="RG")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=10, demand_pcs=100, is_active=True)
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=20, unit="kar")
        cls.r1 = QuoteRecipient.objects.create(name="Speed A", email="a@speed.pl")
        cls.r2 = QuoteRecipient.objects.create(name="Speed B", email="b@speed.pl")

    def setUp(self):
        self.client.force_login(self.user)

    def test_quote_screen_separate_per_recipient_no_positions(self):
        resp = self.client.get(reverse("ui:planner_shipment_quote", args=[self.sh.pk]))
        self.assertEqual(resp.status_code, 200)
        recs = resp.context["recipients"]
        self.assertEqual(len(recs), 2)
        # Each mailto targets ONE recipient only (no competitor in the same message).
        m1 = recs[0]["mailto"]
        # '@' is kept literal in the mailto recipient (valid per RFC 6068).
        self.assertIn("mailto:a@speed.pl", m1)
        self.assertNotIn("b@speed.pl", m1)
        # Body has the summary + contact = logged-in user, NOT the shipment author.
        self.assertIn("Jan%20Kontakt", m1)
        self.assertNotIn("SOMEONE_ELSE", m1)
        self.assertNotIn("Pozycje", m1)                 # no line items
        # An offer (with token + response link) was created per recipient.
        self.assertEqual(ShipmentQuoteOffer.objects.filter(shipment=self.sh).count(), 2)

    def test_bad_amount_does_not_lock_form(self):
        # A garbled amount must re-render with an error and NOT submit/lock the offer.
        self.client.get(reverse("ui:planner_shipment_quote", args=[self.sh.pk]))
        offer = ShipmentQuoteOffer.objects.get(shipment=self.sh, recipient=self.r1)
        from django.test import Client
        anon = Client()
        resp = anon.post(reverse("ui:quote_response", args=[offer.token]),
                         {"amount": "1.234,56", "currency": "PLN"})   # thousands sep → unparseable
        self.assertEqual(resp.status_code, 200)
        offer.refresh_from_db()
        self.assertIsNone(offer.submitted_at)        # not locked — forwarder can retry
        self.assertContains(resp, "kwot")            # error mentions the amount

    def test_forwarder_response_saves_amount_and_dates(self):
        self.client.get(reverse("ui:planner_shipment_quote", args=[self.sh.pk]))   # creates offers
        offer = ShipmentQuoteOffer.objects.get(shipment=self.sh, recipient=self.r1)
        url = reverse("ui:quote_response", args=[offer.token])
        # Public form — accessible without login.
        from django.test import Client
        anon = Client()
        self.assertEqual(anon.get(url).status_code, 200)
        resp = anon.post(url, {"amount": "1850,50", "currency": "PLN",
                               "truck_date": "2026-07-01", "delivery_date": "2026-07-03",
                               "notes": "FTL"})
        self.assertEqual(resp.status_code, 200)
        offer.refresh_from_db()
        self.assertEqual(str(offer.amount), "1850.50")
        self.assertEqual(str(offer.truck_date), "2026-07-01")
        self.assertIsNotNone(offer.submitted_at)
        # Shows up on the shipment detail (amount localised, so match name + section).
        det = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]))
        self.assertContains(det, "Otrzymane wyceny")
        self.assertContains(det, "Speed A")
        self.assertEqual(list(det.context["quote_offers"]), [offer])

    def test_response_page_shows_load_and_company(self):
        self.client.get(reverse("ui:planner_shipment_quote", args=[self.sh.pk]))
        offer = ShipmentQuoteOffer.objects.get(shipment=self.sh, recipient=self.r1)
        from django.test import Client
        resp = Client().get(reverse("ui:quote_response", args=[offer.token]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ACME")
        self.assertContains(resp, "Ładunek")
        self.assertContains(resp, "Palety")
        # UX #8: pola dat przyszłościowych mają min = dziś (blokada dat wstecz).
        from django.utils import timezone
        # Data LOKALNA (jak {% now %} w szablonie) — timezone.now() to UTC i między 00:00 a 02:00
        # czasu polskiego dawało wczorajszą datę (test padał w CI po północy).
        today = timezone.localdate().isoformat()
        self.assertContains(resp, f'name="truck_date" id="truck_date" min="{today}"')
        self.assertContains(resp, f'name="delivery_date" id="delivery_date" min="{today}"')
        self.assertEqual(offer.sender_name, "Jan Kontakt")   # recorded from the requester

    def test_send_quote_email_html(self):
        from django.core import mail
        resp = self.client.post(reverse("ui:planner_shipment_send_quote", args=[self.sh.pk, self.r1.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual(m.to, ["a@speed.pl"])
        self.assertIn("Zapytanie o wycenę", m.subject)
        html = m.alternatives[0][0]
        self.assertIn("Transport palet", html)            # styled HTML body
        self.assertIn("Wyślij wycenę", html)              # CTA button
        offer = ShipmentQuoteOffer.objects.get(shipment=self.sh, recipient=self.r1)
        self.assertIn(offer.token, html)                  # response-form link

    def test_send_quote_emits_non_sensitive_carrier_event(self):
        """Po wysłaniu wyceny leci zdarzenie carrier_quote do n8n — WYŁĄCZNIE nie-wrażliwe
        pola (spedytor + miasto/kraj), bez klienta/REF/ceny (granica prywatności)."""
        from unittest.mock import patch
        with patch("ui.notifications.emit_event") as mock_emit:
            self.client.post(reverse("ui:planner_shipment_send_quote", args=[self.sh.pk, self.r1.pk]))
        mock_emit.assert_called_once()
        kind, payload = mock_emit.call_args.args
        self.assertEqual(kind, "carrier_quote")
        self.assertEqual(set(payload), {"carrier_name", "dest_city", "dest_country"})
        self.assertEqual(payload["carrier_name"], "Speed A")
        self.assertEqual(payload["dest_city"], "VALDEMO")
        self.assertNotIn("PHARMO", str(payload))          # nazwa klienta nie wychodzi

    def test_form_locked_after_submit(self):
        self.client.get(reverse("ui:planner_shipment_quote", args=[self.sh.pk]))
        offer = ShipmentQuoteOffer.objects.get(shipment=self.sh, recipient=self.r1)
        url = reverse("ui:quote_response", args=[offer.token])
        from django.test import Client
        anon = Client()
        anon.post(url, {"amount": "1000", "currency": "PLN",
                        "truck_date": "2026-07-01", "delivery_date": "2026-07-02"})
        # Second visit → locked (no form).
        resp = anon.get(url)
        self.assertEqual(resp.context["locked"], True)
        # A second POST must not overwrite.
        anon.post(url, {"amount": "999"})
        offer.refresh_from_db()
        self.assertEqual(str(offer.amount), "1000.00")

    def test_delivery_before_truck_rejected(self):
        self.client.get(reverse("ui:planner_shipment_quote", args=[self.sh.pk]))
        offer = ShipmentQuoteOffer.objects.get(shipment=self.sh, recipient=self.r1)
        url = reverse("ui:quote_response", args=[offer.token])
        from django.test import Client
        anon = Client()
        resp = anon.post(url, {"amount": "1000", "truck_date": "2026-07-05",
                               "delivery_date": "2026-07-01"})       # earlier than truck
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "nie może być wcześniejsza")
        offer.refresh_from_db()
        self.assertIsNone(offer.submitted_at)                        # not saved

    def test_quote_response_accepts_attachment(self):
        import tempfile
        from django.test import override_settings
        from django.core.files.uploadedfile import SimpleUploadedFile
        offer = ShipmentQuoteOffer.objects.create(shipment=self.sh, recipient=self.r1,
                                                  carrier_name="Speed A")
        f = SimpleUploadedFile("oferta.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        with tempfile.TemporaryDirectory() as md, override_settings(MEDIA_ROOT=md):
            resp = self.client.post(reverse("ui:quote_response", args=[offer.token]),
                                    {"amount": "1500", "currency": "PLN", "attachment": f})
            self.assertEqual(resp.status_code, 200)
            offer.refresh_from_db()
            self.assertTrue(offer.attachment)
            self.assertIn("oferta", offer.attachment.name)
