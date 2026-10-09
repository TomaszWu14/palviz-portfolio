"""Selecting a forwarder offer auto-sends the order-confirmation + driver-form e-mail
(server-side), and the resend button works."""
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from ui import models as m


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class DriverConfirmEmailTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x", email="planner@x.pl")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.ship = m.Shipment.objects.create(name="Dostawa 9")
        self.rec = m.QuoteRecipient.objects.create(name="SpedTrans", email="sped@trans.pl")
        self.offer = m.ShipmentQuoteOffer.objects.create(
            shipment=self.ship, recipient=self.rec, carrier_name="SpedTrans",
            amount=500, currency="PLN", submitted_at=timezone.now())   # BIZ-009: złożona

    def test_select_offer_sends_confirmation(self):
        url = reverse("ui:planner_shipment_select_offer", args=[self.ship.pk, self.offer.pk])
        r = self.client.post(url)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn("sped@trans.pl", msg.to)
        self.assertIn("Potwierdzenie zlecenia", msg.subject)
        # the driver-data form link must be in the body
        da = self.ship.driver
        self.assertTrue(any(da.form_token in (b[0] if isinstance(b, tuple) else b)
                            for b in [msg.body] + [a[0] for a in msg.alternatives]))

    def test_resend_button(self):
        self.client.post(reverse("ui:planner_shipment_select_offer", args=[self.ship.pk, self.offer.pk]))
        mail.outbox.clear()
        r = self.client.post(reverse("ui:planner_shipment_driver_send", args=[self.ship.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="")
    def test_no_smtp_falls_back_to_outlook_mailto(self):
        # Without SMTP nothing is server-sent; select-offer redirects with the flag that
        # auto-opens the Outlook mailto on the detail page.
        url = reverse("ui:planner_shipment_select_offer", args=[self.ship.pk, self.offer.pk])
        r = self.client.post(url)
        self.assertEqual(r.status_code, 302)
        self.assertIn("driver_mail=1", r.url)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="")
    def test_forwarder_without_email_surfaces_error(self):
        # No e-mail and no SMTP → no mailto fallback is possible; the failure must be
        # surfaced, not hidden behind a fake "opening Outlook" + ?driver_mail flag.
        self.rec.email = ""
        self.rec.save(update_fields=["email"])
        url = reverse("ui:planner_shipment_select_offer", args=[self.ship.pk, self.offer.pk])
        r = self.client.post(url)
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("driver_mail", r.url)
        self.assertEqual(len(mail.outbox), 0)
        # the offer is still selected even though the mail couldn't go out
        self.offer.refresh_from_db()
        self.assertTrue(self.offer.selected)
