"""Warehouse readiness confirmations (token links, 15-min pre-check + date check)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, Client
from django.urls import reverse

from ui.models import (
    Shipment, ShipmentLine, WarehouseReadiness, Product, PalletizationInstruction,
)
from ui.roles import ALL_GROUPS


def _user_all_roles():
    u = get_user_model().objects.create_user(username="wh", password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class WarehouseReadinessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.sh = Shipment.objects.create(name="Dostawa 1", destination_city="VALDEMO")

    def setUp(self):
        self.client.force_login(self.user)

    def test_pre_request_sets_15min_deadline(self):
        resp = self.client.post(reverse("ui:planner_shipment_wh_request", args=[self.sh.pk, "pre"]))
        self.assertEqual(resp.status_code, 302)
        wr = WarehouseReadiness.objects.get(shipment=self.sh, kind="pre")
        self.assertEqual(wr.status, "pending")
        delta = (wr.deadline - wr.requested_at).total_seconds()
        self.assertAlmostEqual(delta, 900, delta=2)            # 15 minutes

    def test_date_request_stores_pickup_date(self):
        self.client.post(reverse("ui:planner_shipment_wh_request", args=[self.sh.pk, "date"]),
                         {"pickup_date": "2026-07-01"})
        wr = WarehouseReadiness.objects.get(shipment=self.sh, kind="date")
        self.assertEqual(str(wr.pickup_date), "2026-07-01")

    def test_warehouse_mailto_targets_warehouse_recipients(self):
        from ui.models import QuoteRecipient
        QuoteRecipient.objects.create(name="Magazyn", email="magazyn@firma.pl", is_warehouse=True)
        QuoteRecipient.objects.create(name="Spedytor", email="spedytor@firma.pl")  # forwarder
        self.client.post(reverse("ui:planner_shipment_wh_request", args=[self.sh.pk, "pre"]))
        det = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]))
        mailto = det.context["wh_confirmations"][0]["mailto"]
        self.assertIn("magazyn@firma.pl", mailto)        # '@' kept literal (valid mailto)
        self.assertNotIn("spedytor", mailto)

    def test_public_response_confirms(self):
        self.client.post(reverse("ui:planner_shipment_wh_request", args=[self.sh.pk, "pre"]))
        wr = WarehouseReadiness.objects.get(shipment=self.sh, kind="pre")
        url = reverse("ui:wh_readiness_response", args=[wr.token])
        anon = Client()                                        # warehouse not logged in
        self.assertEqual(anon.get(url).status_code, 200)
        anon.post(url, {"answer": "yes", "note": "OK na 14:00"})
        wr.refresh_from_db()
        self.assertEqual(wr.status, "yes")
        self.assertIsNotNone(wr.answered_at)
        # Shows on the shipment detail.
        det = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]))
        self.assertContains(det, "Gotowość magazynu")
        self.assertContains(det, "Potwierdzone")

    def test_request_sends_html_email_with_clickable_link(self):
        from django.core import mail
        from ui.models import QuoteRecipient
        QuoteRecipient.objects.create(name="Magazyn", email="mag@example.com",
                                      is_active=True, is_warehouse=True)
        self.client.post(reverse("ui:planner_shipment_wh_request", args=[self.sh.pk, "pre"]))
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual(m.to, ["mag@example.com"])
        html = m.alternatives[0][0]
        wr = WarehouseReadiness.objects.get(shipment=self.sh, kind="pre")
        self.assertIn(f"/wh-readiness/{wr.token}/", html)     # real <a href> link, clickable once
        self.assertIn("Potwierdź gotowość", html)

    def test_resend_endpoint_sends_again(self):
        from django.core import mail
        from ui.models import QuoteRecipient
        QuoteRecipient.objects.create(name="Magazyn", email="mag@example.com",
                                      is_active=True, is_warehouse=True)
        self.client.post(reverse("ui:planner_shipment_wh_request", args=[self.sh.pk, "pre"]))
        wr = WarehouseReadiness.objects.get(shipment=self.sh, kind="pre")
        mail.outbox.clear()
        self.client.post(reverse("ui:planner_shipment_wh_send", args=[self.sh.pk, wr.pk]))
        self.assertEqual(len(mail.outbox), 1)


class WarehouseCountNegotiationTests(TestCase):
    """The pallet-count loop: ask → confirm/counter with a ready time → re-quote on the
    warehouse-agreed figure → it shows on the pickup schedule."""

    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.sh = Shipment.objects.create(name="Dostawa 81772168", destination_city="Reykjavik",
                                         destination_country="IS", stowage_efficiency_pct=85)
        p = Product.objects.create(code="X1", name="X1")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=1, demand_pcs=100, is_active=True)
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=300, unit="kar")

    def setUp(self):
        self.client.force_login(self.user)

    def test_request_records_asked_pallets(self):
        self.client.post(reverse("ui:planner_shipment_wh_request", args=[self.sh.pk, "pre"]))
        wr = WarehouseReadiness.objects.get(shipment=self.sh, kind="pre")
        self.assertGreater(wr.asked_pallets or 0, 0)

    def test_public_suggest_different_count_with_ready_time(self):
        self.client.post(reverse("ui:planner_shipment_wh_request", args=[self.sh.pk, "pre"]))
        wr = WarehouseReadiness.objects.get(shipment=self.sh, kind="pre")
        url = reverse("ui:wh_readiness_response", args=[wr.token])
        anon = Client()
        # The page renders the pallet visualisation (one tile per pallet).
        self.assertContains(anon.get(url), "Wizualizacja palet")
        anon.post(url, {"answer": "suggest", "suggested_pallets": "2",
                        "ready_at": "2026-07-01T08:30", "note": "Zmieścimy w 2"})
        wr.refresh_from_db()
        self.assertEqual(wr.status, "yes")
        self.assertEqual(wr.suggested_pallets, 2)
        self.assertIsNotNone(wr.ready_at)

    def test_apply_wh_count_anchors_quote_and_requotes(self):
        from ui.views.core.helpers import _calc_shipment_data
        self.client.post(reverse("ui:planner_shipment_wh_request", args=[self.sh.pk, "pre"]))
        wr = WarehouseReadiness.objects.get(shipment=self.sh, kind="pre")
        wr.status = "yes"; wr.suggested_pallets = 2; wr.save()
        resp = self.client.post(reverse("ui:planner_shipment_apply_wh_count", args=[self.sh.pk]))
        self.assertEqual(resp.status_code, 302)
        self.sh.refresh_from_db()
        self.assertEqual(self.sh.warehouse_pallets, 2)
        # The quote now anchors to the warehouse-agreed count at any efficiency.
        sc = _calc_shipment_data(self.sh, stow_eff=95)["scenarios"][0]
        self.assertEqual(sc["n_pallets"], 2)
        self.assertEqual(sc["wh_pallets"], 2)

    def test_apply_adopts_count_from_date_readiness(self):
        # Regression: the counter-proposal lives on the 'date' readiness, with a bare 'pre'
        # present — apply must adopt the 'date' suggestion, not the empty 'pre' figure.
        WarehouseReadiness.objects.create(shipment=self.sh, kind="pre", asked_pallets=3)
        WarehouseReadiness.objects.create(shipment=self.sh, kind="date", status="yes",
                                          asked_pallets=3, suggested_pallets=2)
        resp = self.client.post(reverse("ui:planner_shipment_apply_wh_count", args=[self.sh.pk]))
        self.assertEqual(resp.status_code, 302)
        self.sh.refresh_from_db()
        self.assertEqual(self.sh.warehouse_pallets, 2)

    def test_pickup_schedule_lists_shipment_with_ready_time(self):
        from django.utils import timezone
        from datetime import timedelta
        wr = WarehouseReadiness.objects.create(
            shipment=self.sh, kind="date", status="yes",
            ready_at=timezone.now() + timedelta(days=2))
        resp = self.client.get(reverse("ui:planner_pickup_schedule"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Dostawa 81772168")
        self.assertContains(resp, "Harmonogram odbiorów")

    def test_suggest_raises_transport_task_and_notifies(self):
        from ui.models import Task, Notification
        # A Transport planner who should receive the alert.
        planner = get_user_model().objects.create_user(username="trans", password="x")
        planner.groups.add(Group.objects.get_or_create(name="Transport")[0])
        self.client.post(reverse("ui:planner_shipment_wh_request", args=[self.sh.pk, "pre"]))
        wr = WarehouseReadiness.objects.get(shipment=self.sh, kind="pre")
        url = reverse("ui:wh_readiness_response", args=[wr.token])
        Client().post(url, {"answer": "suggest", "suggested_pallets": "2", "ready_at": "2026-07-01T08:00"})
        # Team task raised (deduped per shipment) + planner notified.
        self.assertTrue(Task.objects.filter(dedup_key__startswith=f"wh_resp:{self.sh.pk}").exclude(status="done").exists())
        self.assertTrue(Notification.objects.filter(recipient=planner, title__icontains="proponuje").exists())

    def test_apply_count_closes_transport_task(self):
        from ui.models import Task
        self.client.post(reverse("ui:planner_shipment_wh_request", args=[self.sh.pk, "pre"]))
        wr = WarehouseReadiness.objects.get(shipment=self.sh, kind="pre")
        Client().post(reverse("ui:wh_readiness_response", args=[wr.token]),
                      {"answer": "suggest", "suggested_pallets": "2"})
        self.client.post(reverse("ui:planner_shipment_apply_wh_count", args=[self.sh.pk]))
        self.assertFalse(Task.objects.filter(dedup_key__startswith=f"wh_resp:{self.sh.pk}").exclude(status="done").exists())

    def test_date_confirmation_notifies_creator(self):
        from ui.models import Notification
        # Operator who created the delivery (matched by author_email).
        creator = get_user_model().objects.create_user(username="op", password="x", email="op@example.com")
        self.sh.author_email = "op@example.com"
        self.sh.save(update_fields=["author_email"])
        self.client.post(reverse("ui:planner_shipment_wh_request", args=[self.sh.pk, "date"]),
                         {"pickup_date": "2026-07-10"})
        wr = WarehouseReadiness.objects.get(shipment=self.sh, kind="date")
        Client().post(reverse("ui:wh_readiness_response", args=[wr.token]), {"answer": "yes"})
        # Loop closed → creator is told the warehouse is ready (no blocking task for this).
        self.assertTrue(Notification.objects.filter(recipient=creator, title__icontains="gotowy").exists())


class ClientLoadedNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from ui.models import Customer
        cls.user = _user_all_roles()
        cust = Customer.objects.create(name="Nordmed", code="C1", contact_email="klient@nordmed.example")
        cls.sh = Shipment.objects.create(name="Dostawa 81772168", customer=cust)

    def setUp(self):
        self.client.force_login(self.user)

    def test_notify_client_sends_email_and_marks(self):
        from django.core import mail
        with self.settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            resp = self.client.post(reverse("ui:planner_shipment_notify_client", args=[self.sh.pk]),
                                    {"client_eta": "2026-07-12"})
        self.assertEqual(resp.status_code, 302)
        self.sh.refresh_from_db()
        self.assertIsNotNone(self.sh.client_loaded_notified_at)
        self.assertEqual(str(self.sh.client_eta), "2026-07-12")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("klient@nordmed.example", mail.outbox[0].to)
        self.assertIn("załadowany", mail.outbox[0].subject)

    def test_notify_client_without_email_errors(self):
        sh2 = Shipment.objects.create(name="Bez klienta")
        resp = self.client.post(reverse("ui:planner_shipment_notify_client", args=[sh2.pk]))
        self.assertEqual(resp.status_code, 302)
        sh2.refresh_from_db()
        self.assertIsNone(sh2.client_loaded_notified_at)   # nothing sent, no crash
